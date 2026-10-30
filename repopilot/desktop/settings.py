"""Desktop-owned configuration. Secrets are encrypted with current-user Windows DPAPI."""
from contextlib import closing
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

from repopilot.core.paths import default_env_file
from repopilot.delivery.forge import Origin, open_forge
from repopilot.storage.jobs import StateError, job_lock


class WindowsVault:
    def _crypt(self, data: bytes, decrypt=False):
        if os.name != 'nt':
            raise StateError('desktop_secure_storage_requires_windows')
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
        raw = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
        output = Blob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        # UI_FORBIDDEN, current-user scope; never use LOCAL_MACHINE or plaintext fallback.
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise StateError('desktop_secure_storage_unavailable')
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def encrypt(self, data):
        return self._crypt(data)

    def decrypt(self, data):
        return self._crypt(data, True)


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['environment', 'desktop'] = 'desktop'
    app_id: str = Field(default='', max_length=20)
    installation_id: str = Field(default='', max_length=20)
    owner: str = Field(default='', max_length=100)
    repository: str = Field(default='', max_length=210)

    @field_validator('app_id', 'installation_id')
    @classmethod
    def numeric(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'[1-9][0-9]{0,19}', value):
            raise ValueError('must_be_numeric_id')
        return value

    @field_validator('owner')
    @classmethod
    def owner_login(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,99}', value):
            raise ValueError('invalid_github_login')
        return value

    @field_validator('repository')
    @classmethod
    def repository_name(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
            raise ValueError('use_owner_slash_repository')
        return value


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['environment', 'desktop'] = 'desktop'
    provider: Literal['deepseek', 'qwen', 'openai-compatible', 'anthropic'] = 'deepseek'
    base_url: str = Field(default='https://api.deepseek.com', max_length=500)
    model: str = Field(default='deepseek-flash', min_length=1, max_length=150)
    max_run_cost_cny: float = Field(default=2, gt=0, le=10000)
    budget_cny: float = Field(default=200, gt=0, le=1000000)
    input_cny_per_million: float = Field(default=3, gt=0, le=100000)
    output_cny_per_million: float = Field(default=12, gt=0, le=100000)

    @field_validator('base_url')
    @classmethod
    def endpoint(cls, value):
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('use_https_endpoint_without_credentials')
        return value.rstrip('/')


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    github: GitHubSettings
    model: ModelSettings
    private_key: str | None = Field(default=None, max_length=20000, repr=False)
    user_token: str | None = Field(default=None, max_length=2048, repr=False)
    model_api_key: str | None = Field(default=None, max_length=4096, repr=False)
    clear_private_key: bool = False
    clear_user_token: bool = False
    clear_model_api_key: bool = False


class SettingsStore:
    def __init__(self, root, *, vault=None, env_file=None, transport=None):
        self.root = Path(root)
        self.path = self.root / 'desktop-settings.bin'
        self.vault = vault or WindowsVault()
        self.env_file = Path(env_file) if env_file is not None else default_env_file()
        self.transport = transport
        self.lock = RLock()

    def load(self):
        if not self.path.exists():
            return {'revision': 0, 'github': {'source': 'environment'}, 'model': {'source': 'environment'}, 'secrets': {}}
        try:
            data = self.path.read_bytes()
            if len(data) > 100000:
                raise ValueError
            value = json.loads(self.vault.decrypt(data))
            if not isinstance(value, dict) or value.get('schema') != 1:
                raise ValueError
            return value
        except Exception:
            raise StateError('desktop_settings_cannot_decrypt_use_same_windows_account') from None

    def environment(self):
        values = dotenv_values(self.env_file, interpolate=False) if self.env_file.exists() else {}
        return lambda name, default='': str(os.environ.get(name, values.get(name)) or default).strip()

    def public(self):
        with self.lock:
            stored = self.load()
            env = self.environment()
            github = GitHubSettings(**stored.get('github', {})).model_dump()
            model = ModelSettings(**stored.get('model', {})).model_dump()
            secrets = stored.get('secrets') or {}
            if github['source'] == 'environment':
                github.update(app_id=env('GITHUB_APP_ID'), installation_id=env('GITHUB_APP_INSTALLATION_ID'),
                              owner=env('REPOPILOT_OWNER'))
                github['private_key_configured'] = bool(env('GITHUB_APP_PRIVATE_KEY_FILE'))
                github['user_token_configured'] = bool(env('GH_TOKEN') or env('GITHUB_TOKEN'))
            else:
                github['private_key_configured'] = bool(secrets.get('private_key'))
                github['user_token_configured'] = bool(secrets.get('user_token'))
            if model['source'] == 'environment':
                model.update(provider=env('REPOPILOT_PROVIDER', 'deepseek'),
                             base_url=env('REPOPILOT_BASE_URL', env('DS_BASE_URL', 'https://api.deepseek.com')),
                             model=env('REPOPILOT_MODEL', env('DS_MODEL', 'deepseek-flash')))
                model['api_key_configured'] = bool(env('REPOPILOT_API_KEY') or (model['provider'] == 'deepseek' and env('DS_API_KEY')))
                for name in ('budget_cny', 'input_cny_per_million', 'output_cny_per_million'):
                    value = env('REPOPILOT_' + name.upper())
                    if value:
                        try:
                            model[name] = float(value)
                        except ValueError:
                            pass  # The explicit format check reports invalid environment values.
            else:
                model['api_key_configured'] = bool(secrets.get('model_api_key'))
            return {'revision': stored['revision'], 'github': github, 'model': model,
                    'secure_storage': 'windows-current-user-dpapi' if os.name == 'nt' else 'unavailable',
                    'runtime': str(self.root.resolve())}

    def save(self, update: SettingsUpdate):
        with self.lock, job_lock(self.root, 'desktop-settings'):
            stored = self.load()
            if update.revision != stored['revision']:
                raise StateError('desktop_settings_revision_conflict')
            secrets = dict(stored.get('secrets') or {})
            for name in ('private_key', 'user_token', 'model_api_key'):
                value = getattr(update, name)
                if getattr(update, 'clear_' + name):
                    secrets.pop(name, None)
                elif value and value.strip():
                    secrets[name] = value.strip()
            if update.private_key and update.private_key.strip() and not update.clear_private_key:
                try:
                    from cryptography.hazmat.primitives.serialization import load_pem_private_key
                    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
                    if not isinstance(load_pem_private_key(update.private_key.encode(), password=None), RSAPrivateKey):
                        raise ValueError
                except Exception:
                    raise StateError('github_private_key_must_be_unencrypted_rsa_pem') from None
            for name in ('user_token', 'model_api_key'):
                if any(c.isspace() for c in secrets.get(name, '')):
                    raise StateError('credential_must_not_contain_whitespace')
            value = {'schema': 1, 'revision': stored['revision'] + 1,
                     'github': update.github.model_dump(), 'model': update.model.model_dump(), 'secrets': secrets}
            encrypted = self.vault.encrypt(json.dumps(value, ensure_ascii=False).encode())
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, prefix='.desktop-settings-', delete=False) as file:
                    temporary = Path(file.name)
                    file.write(encrypted); file.flush(); os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            return self.public()

    def github(self, repository, *, sender='bot'):
        from repopilot.delivery.github import GitHub
        from repopilot.delivery.github_app import InstallationAuth
        stored = self.load()
        github = stored.get('github') or {}
        secrets = stored.get('secrets') or {}
        if sender == 'user':
            token = secrets.get('user_token') if github.get('source') == 'desktop' else None
            if token:
                return GitHub(token, transport=self.transport)
            if github.get('source') == 'environment':
                env = self.environment()
                token = env('GH_TOKEN') or env('GITHUB_TOKEN')
                if token:
                    return GitHub(token, transport=self.transport)
            raise StateError('github_personal_comment_token_required')
        if github.get('source') == 'desktop':
            if not github.get('app_id') or not github.get('installation_id') or not secrets.get('private_key'):
                raise StateError('github_app_configuration_incomplete')
            auth = InstallationAuth(github['app_id'], github['installation_id'], private_key=secrets['private_key'].encode(),
                                    transport=self.transport)
            return GitHub('', app_auth=auth, transport=self.transport)
        return open_forge(Origin('github', 'github.com', repository), env_file=self.env_file, transport=self.transport)

    def check_github(self, repository=None):
        settings = self.public()['github']
        repository = repository or settings['repository']
        GitHubSettings(repository=repository)
        if not repository:
            raise StateError('github_test_repository_required')
        with closing(self.github(repository)) as client:
            login = client.viewer()
            value = client.request('GET', f'/repos/{repository}')
            if str(value.get('full_name', '')).casefold() != repository.casefold():
                raise StateError('github_repository_identity_mismatch')
            return {'status': 'connected', 'login': login, 'repository': value['full_name'],
                    'owner': settings['owner'], 'write_verified': False,
                    'permissions': value.get('permissions') or {}, 'checked': 'read_only'}

    def runtime_settings(self):
        """Resolve desktop model defaults for future workers without touching any ledger."""
        from repopilot.core.config import RuntimeSettings, load_settings
        stored = self.load()
        model = stored.get('model') or {}
        if model.get('source') == 'environment':
            return load_settings(self.env_file)
        options = {k: v for k, v in model.items() if k != 'source'}
        options['api_key'] = (stored.get('secrets') or {}).get('model_api_key', '')
        return RuntimeSettings(**options)
