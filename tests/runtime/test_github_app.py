"""Real RSA/JWT with synthetic HTTP; no app registration or real credentials."""

from contextlib import closing
from datetime import datetime, timezone
import json
import time

import httpx
import pytest

pytest.importorskip('jwt', reason='optional github-app dependencies')
pytest.importorskip('cryptography', reason='optional github-app dependencies')

from repopilot.delivery.forge import ForgeError, open_forge
from repopilot.delivery.github_app import InstallationAuth


@pytest.fixture(scope='module')
def rsa_key():
    serialization = pytest.importorskip('cryptography.hazmat.primitives.serialization')
    rsa = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.rsa')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()), key.public_key()


def test_app_installation_identity_and_token_renewal(tmp_path, rsa_key):
    jwt = pytest.importorskip('jwt')
    key, public = rsa_key
    file = tmp_path/'key.pem';file.write_bytes(key)
    now = [time.time()]; issued=[]; seen=[]
    def handler(request):
        if request.url.path in {'/app', '/app/installations/2/access_tokens'}:
            claims=jwt.decode(request.headers['Authorization'][7:], public, algorithms=['RS256'],
                              options={'verify_exp':False,'verify_iat':False})
            assert claims['iss']=='1' and claims['exp']-claims['iat']==600
            if request.url.path=='/app':
                return httpx.Response(200,json={'id':1,'slug':'repopilot-example'})
            issued.append(request)
            return httpx.Response(201,json={'token':f'synthetic-installation-{len(issued)}',
                'expires_at':datetime.fromtimestamp(now[0]+3600,timezone.utc).isoformat()})
        seen.append(request.headers['Authorization'])
        return httpx.Response(200,json={'full_name':'owner/repo','default_branch':'main'})
    auth=InstallationAuth('1','2',file,transport=httpx.MockTransport(handler),clock=lambda:now[0])
    from repopilot.delivery.github import GitHub
    with closing(GitHub('',app_auth=auth,transport=httpx.MockTransport(handler))) as api:
        assert api.viewer()=='repopilot-example[bot]'
        api.repository_metadata('owner/repo')
        now[0]+=3500
        api.repository_metadata('owner/repo')
        assert api.token=='synthetic-installation-2'
    assert len(issued)==2 and seen==['Bearer synthetic-installation-1','Bearer synthetic-installation-2']


def test_explicit_app_mode_never_falls_back_to_personal_token(tmp_path,monkeypatch):
    file=tmp_path/'.env';file.write_text('GITHUB_AUTH=app\nGITHUB_TOKEN=synthetic-personal\n')
    for key in ('GITHUB_AUTH','GITHUB_APP_ID','GITHUB_APP_INSTALLATION_ID','GITHUB_APP_PRIVATE_KEY_FILE'):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(ForgeError,match='github_app_private_key_file_required'):
        open_forge('https://github.com/owner/repo/issues/1',env_file=file)


def test_signing_failure_does_not_expose_private_material(tmp_path):
    file=tmp_path/'key.pem';file.write_text('synthetic-private-material')
    auth=InstallationAuth(1,2,file)
    try:
        with pytest.raises(ForgeError,match='github_app_signing_failed') as error:
            auth.jwt()
        assert 'synthetic-private-material' not in str(error.value)
    finally:
        auth.close()


def test_app_config_resolves_private_key_relative_to_env_file(tmp_path,rsa_key,monkeypatch):
    file=tmp_path/'key.pem';file.write_bytes(rsa_key[0])
    env=tmp_path/'.env'
    env.write_text('GITHUB_AUTH=app\nGITHUB_APP_ID=1\nGITHUB_APP_INSTALLATION_ID=2\nGITHUB_APP_PRIVATE_KEY_FILE=key.pem\n')
    for key in ('GITHUB_AUTH','GITHUB_APP_ID','GITHUB_APP_INSTALLATION_ID','GITHUB_APP_PRIVATE_KEY_FILE'):
        monkeypatch.delenv(key,raising=False)
    def handler(request):
        if request.url.path.endswith('/access_tokens'):
            return httpx.Response(201,json={'token':'synthetic-app-token',
                'expires_at':datetime.fromtimestamp(time.time()+3600,timezone.utc).isoformat()})
        return httpx.Response(200,json={'id':1,'slug':'example-app'})
    with closing(open_forge('https://github.com/owner/repo/issues/1',env_file=env,
                            transport=httpx.MockTransport(handler))) as api:
        assert api.viewer()=='example-app[bot]'


def test_installation_token_shapes_are_redacted():
    from repopilot.presentation.formatting import redact
    secret='gh'+'s_'+'A'*24+'.'+'B'*24
    assert secret not in redact(secret)
    from repopilot.knowledge.documents import checked_body,KnowledgeFault
    stateless='gh'+'s_'+'1234567_'+'eyJ'+'A'*40+'.'+'B'*40
    with pytest.raises(KnowledgeFault):
        checked_body(stateless)


def test_app_auth_never_sends_credentials_to_other_origin(tmp_path):
    auth=InstallationAuth(1,2,tmp_path/'missing-private-key.pem')
    try:
        with closing(httpx.Client(auth=auth,transport=httpx.MockTransport(lambda _:pytest.fail('no request')))) as client:
            with pytest.raises(ForgeError,match='github_app_auth_origin_mismatch'):
                client.get('https://example.invalid/repos/owner/repo')
    finally:
        auth.close()


def test_malformed_installation_token_response_fails_closed(tmp_path,rsa_key):
    file=tmp_path/'key.pem';file.write_bytes(rsa_key[0])
    auth=InstallationAuth(1,2,file,transport=httpx.MockTransport(
        lambda _:httpx.Response(201,json={'token':'synthetic-sensitive-value','expires_at':None})))
    try:
        with pytest.raises(ForgeError,match='github_app_invalid_token_response') as error:
            auth.token()
        assert 'synthetic-sensitive-value' not in str(error.value)
    finally:
        auth.close()
