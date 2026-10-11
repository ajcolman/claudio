"""Entorno aislado para probar el manejo de cuentas sin tocar credenciales reales."""
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

NOW = 1_700_000_000


def oauth_credentials(refresh='refresh-1', access='access-1', expires_in=3600, now=NOW, **extra):
    """A Claude Code credential blob shaped like the real one, with fictitious tokens."""
    return json.dumps({'claudeAiOauth': {'accessToken': access, 'refreshToken': refresh, 'expiresAt': int((now + expires_in) * 1000),
                                         'scopes': ['user:inference', 'user:profile'], 'subscriptionType': 'max'}, **extra})


def oauth_account(email, uuid='', organization='', name=''):
    return {'emailAddress': email, 'accountUuid': uuid, 'organizationUuid': organization or None, 'organizationName': name or None}


class Sandbox(unittest.TestCase):
    """A temporary HOME so every path the code resolves stays inside the test."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        environment = {'HOME': str(self.home), 'USERPROFILE': str(self.home), 'APPDATA': str(self.home / 'AppData'), 'USER': 'tester'}
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('CLAUDE_CONFIG_DIR', 'CLAUDE_SECURESTORAGE_CONFIG_DIR', 'XDG_DATA_HOME'):
            os.environ.pop(name, None)
        self.claude = self.home / '.claude'
        self.claude.mkdir()

    def write_login(self, credentials, account, **config):
        """Leave Claude Code logged in the way its own /login does on Linux."""
        (self.claude / '.credentials.json').write_text(credentials)
        (self.home / '.claude.json').write_text(json.dumps({'oauthAccount': account, 'numStartups': 7, **config}))

    def live_credentials(self):
        return (self.claude / '.credentials.json').read_text()

    def live_config(self):
        return json.loads((self.home / '.claude.json').read_text())


class FakeKeychain:
    """Stands in for /usr/bin/security with an in-memory login keychain."""

    def __init__(self):
        self.items = {}
        self.locked = False
        self.calls = []

    def run(self, argv, input=None, **_):
        arguments = shlex.split(input) if argv[1:] == ['-i'] else argv[1:]
        self.calls.append(arguments[0])
        if self.locked:
            return subprocess.CompletedProcess(argv, 36, '', 'User interaction is not allowed.')
        options, position = {}, 1
        while position < len(arguments):
            flag = arguments[position]
            if flag in ('-a', '-s', '-X'):
                options[flag] = arguments[position + 1]
                position += 1
            position += 1
        key = (options['-s'], options['-a'])
        if arguments[0] == 'add-generic-password':
            self.items[key] = bytes.fromhex(options['-X']).decode()
            return subprocess.CompletedProcess(argv, 0, '', '')
        if key not in self.items:
            return subprocess.CompletedProcess(argv, 44, '', 'The specified item could not be found in the keychain.')
        value = self.items.pop(key) if arguments[0] == 'delete-generic-password' else self.items[key]
        return subprocess.CompletedProcess(argv, 0, value + '\n', '')


def use_macos(test):
    """Make the code under test believe it runs on macOS, backed by a fake keychain."""
    from accounts import keychain
    fake = FakeKeychain()
    for patcher in (patch.object(sys, 'platform', 'darwin'), patch.object(keychain.subprocess, 'run', fake.run)):
        patcher.start()
        test.addCleanup(patcher.stop)
    return fake


class FakeNetwork:
    """Answers Anthropic's three endpoints from scripted data; nothing leaves the machine."""

    def __init__(self):
        self.tokens = {}    # refresh token -> answer, or (status, body) to fail
        self.usage = {}     # access token -> answer, or an exception to raise
        self.profiles = {}  # access token -> answer
        self.requests = []

    def urlopen(self, request, timeout=None):
        url = request.full_url
        headers = {name.lower(): value for name, value in request.header_items()}
        token = headers.get('authorization', '')[len('Bearer '):]
        body = json.loads(request.data) if request.data else None
        self.requests.append({'url': url, 'token': token, 'body': body, 'headers': headers})
        if url.endswith('/oauth/token'):
            answer = self.tokens.get(body['refresh_token'], (400, '{"error": "invalid_grant"}'))
        elif url.endswith('/oauth/usage'):
            answer = self.usage.get(token, (401, '{}'))
        else:
            answer = self.profiles.get(token, (401, '{}'))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            raise urllib.error.HTTPError(url, answer[0], 'error', {}, io.BytesIO(answer[1].encode()))
        return io.BytesIO(json.dumps(answer).encode())

    def calls(self, ending):
        return [request for request in self.requests if request['url'].endswith(ending)]


def use_network(test):
    from accounts import oauth
    fake = FakeNetwork()
    patcher = patch.object(oauth.urllib.request, 'urlopen', fake.urlopen)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fake


def quota(five_hour=None, seven_day=None, reset='2099-01-01T00:00:00+00:00'):
    """A usage answer shaped like the real endpoint's."""
    answer = {}
    if five_hour is not None:
        answer['five_hour'] = {'utilization': five_hour, 'resets_at': reset}
    if seven_day is not None:
        answer['seven_day'] = {'utilization': seven_day, 'resets_at': reset}
    return answer


class ManagerCase(Sandbox):
    def setUp(self):
        super().setUp()
        self.network = use_network(self)
        self.now = NOW
        from accounts.manager import Accounts
        self.accounts = Accounts(clock=lambda: self.now)

    def login(self, email, refresh, organization='', name='', expires_in=3600, **extra):
        """Log Claude Code in as an account, the way its own /login leaves the machine."""
        self.write_login(oauth_credentials(refresh, f'access-{refresh}', expires_in, NOW, **extra), oauth_account(email, f'uuid-{email}', organization, name), projects={'/work': {}})

    def link(self, email, refresh, alias='', **options):
        self.login(email, refresh, **options)
        self.accounts.link_current(alias)

    def stored(self, number):
        return self.accounts.registry.credentials(str(number))

    def record(self, number):
        return self.accounts.registry.load()['accounts'][str(number)]

    def refresh_token(self, text):
        return json.loads(text)['claudeAiOauth']['refreshToken']
