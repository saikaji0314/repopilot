"""Host-only GitHub App installation authentication and short-lived token renewal."""

from datetime import datetime
from pathlib import Path
import re
from threading import RLock
import time

import httpx

from repopilot.delivery.forge import ForgeError


class InstallationAuth(httpx.Auth):
    def __init__(self, app_id, installation_id, private_key_file, *, transport=None, clock=time.time):
        if not all(re.fullmatch(r'[1-9][0-9]{0,19}', str(x)) for x in (app_id, installation_id)):
            raise ForgeError('github_app_ids_required')
        self.app_id, self.installation_id = str(app_id), str(installation_id)
        self.key_file = Path(private_key_file)
        self.clock = clock
        self.lock = RLock()
        self._token, self._expires, self._login = '', 0, None
        self.client = httpx.Client(base_url='https://api.github.com', timeout=25,
            follow_redirects=False, transport=transport,
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'RepoPilot',
                     'X-GitHub-Api-Version': '2022-11-28'})

    def jwt(self):
        try:
            import jwt
        except ImportError:
            raise ForgeError('install_github_app_extra_required') from None
        try:
            key = self.key_file.read_bytes()
            now = int(self.clock())
            return jwt.encode({'iat': now - 60, 'exp': now + 540, 'iss': self.app_id}, key, algorithm='RS256')
        except Exception:
            raise ForgeError('github_app_signing_failed') from None

    def app_request(self, method, path):
        if (method, path) not in {('GET', '/app'),
                ('POST', f'/app/installations/{self.installation_id}/access_tokens')}:
            raise ForgeError('invalid_github_app_path')
        try:
            response = self.client.request(method, path, headers={'Authorization': 'Bearer ' + self.jwt()})
        except httpx.HTTPError:
            raise ForgeError('github_app_transport_error') from None
        if response.status_code not in {200, 201}:
            raise ForgeError(f'github_app_http_{response.status_code}', status=response.status_code)
        try:
            value = response.json()
        except ValueError:
            raise ForgeError('github_app_invalid_response') from None
        if not isinstance(value, dict):
            raise ForgeError('github_app_invalid_response')
        return value

    def token(self):
        with self.lock:
            if self._token and self.clock() < self._expires - 120:
                return self._token
            value = self.app_request('POST', f'/app/installations/{self.installation_id}/access_tokens')
            try:
                token = value['token']
                if not isinstance(value['expires_at'], str):
                    raise ValueError
                expiry = datetime.fromisoformat(value['expires_at'].replace('Z', '+00:00'))
                if (not isinstance(token, str) or not token or any(c.isspace() for c in token)
                        or expiry.tzinfo is None or expiry.timestamp() <= self.clock() + 120):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ForgeError('github_app_invalid_token_response') from None
            self._token, self._expires = token, expiry.timestamp()
            return self._token

    def login(self):
        with self.lock:
            if self._login is None:
                value = self.app_request('GET', '/app')
                slug = value.get('slug')
                if str(value.get('id')) != self.app_id or not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9-]+', slug):
                    raise ForgeError('github_app_identity_mismatch')
                self._login = slug + '[bot]'
            return self._login

    def auth_flow(self, request):
        if request.url.scheme != 'https' or request.url.host != 'api.github.com':
            raise ForgeError('github_app_auth_origin_mismatch')
        request.headers['Authorization'] = 'Bearer ' + self.token()
        yield request

    def close(self):
        self.client.close()
