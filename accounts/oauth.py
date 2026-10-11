"""Renovación de tokens y consulta de cuotas contra los servicios de Anthropic."""
import json
import time
import urllib.error
import urllib.request

from . import credentials

TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'
# Public identifier of the Claude Code client; refresh tokens are bound to it.
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
BETA = 'oauth-2025-04-20'
# Same margin Claude Code uses before it renews a token on its own.
EXPIRY_MARGIN = 5 * 60


class ServiceError(OSError):
    """The service answered with an error status."""

    def __init__(self, status, body):
        super().__init__(f'HTTP {status}')
        self.status, self.body = status, body


def expired(stored, now=None):
    expires = credentials.login(stored).get('expiresAt')
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return False
    return (time.time() if now is None else now) + EXPIRY_MARGIN >= expires / 1000


def _request(url, token=None, body=None, timeout=5.0, beta=False):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'claudio/1.0'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if beta:
        headers['anthropic-beta'] = BETA
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        with error:
            raise ServiceError(error.code, error.read().decode('utf-8', errors='replace')) from None


def refresh(stored, now=None):
    """Renew a login. Returns (credentials, '') or (None, reason).

    The reason is 'invalid_grant' or 'invalid_client' only when the server said
    so explicitly: a wrong verdict would make a working account look dead,
    while a wrong 'transient' costs one retry.
    """
    data, section = credentials.parse(stored), credentials.login(stored)
    if data is None:
        return None, 'transient'
    if not section.get('refreshToken'):
        return None, 'no_refresh_token'
    try:
        answer = _request(TOKEN_URL, timeout=10.0, body={'grant_type': 'refresh_token', 'refresh_token': section['refreshToken'], 'client_id': CLIENT_ID})
        renewed = dict(section, accessToken=answer['access_token'],
                       expiresAt=int(((time.time() if now is None else now) + answer['expires_in']) * 1000))
    except ServiceError as error:
        if error.status in (400, 401, 403):
            try:
                verdict = json.loads(error.body).get('error')
            except (ValueError, AttributeError):
                verdict = None
            if verdict in ('invalid_grant', 'invalid_client'):
                return None, verdict
        return None, 'transient'
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'transient'
    if answer.get('refresh_token'):
        renewed['refreshToken'] = answer['refresh_token']
    if answer.get('scope'):
        renewed['scopes'] = answer['scope'].split()
    return json.dumps(dict(data, claudeAiOauth=renewed)), ''


def usage(token):
    """Raw quota data of the token's account. Raises OSError or ValueError when unavailable."""
    return _request(USAGE_URL, token=token, beta=True)


def windows(answer):
    """The two quota windows the interface shows, or None when the answer has neither."""
    result = {}
    for source, name in (('five_hour', 'fiveHour'), ('seven_day', 'sevenDay')):
        window = answer.get(source) if isinstance(answer, dict) else None
        percentage = window.get('utilization') if isinstance(window, dict) else None
        if isinstance(percentage, bool) or not isinstance(percentage, (int, float)):
            continue
        result[name] = {'pct': percentage}
        if window.get('resets_at'):
            result[name]['resetsAt'] = window['resets_at']
    return result or None


def profile(token):
    """Whose token this is, or None when the service cannot tell."""
    try:
        answer = _request(PROFILE_URL, token=token)
    except (OSError, ValueError):
        return None
    account = answer.get('account') if isinstance(answer, dict) else None
    uuid = account.get('uuid') if isinstance(account, dict) else None
    if not isinstance(uuid, str) or not uuid.strip():
        return None
    organization = answer.get('organization')
    organization = organization.get('uuid') if isinstance(organization, dict) else None
    email = account.get('email')
    return {'uuid': uuid.strip(), 'email': email if isinstance(email, str) else None,
            'organizationUuid': organization if isinstance(organization, str) else None}
