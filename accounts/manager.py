"""Operaciones sobre las cuentas vinculadas: vincular, cambiar y consultar cuotas."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import credentials, oauth, paths, sessions, storage
from .registry import Registry, find, next_number

USAGE_TTL = 60
# A candidate must sit this far below the threshold, so two accounts at the line never alternate.
ROTATION_MARGIN = 5
ROTATION_COOLDOWN = 300
STRATEGIES = ('best', 'next-available', 'consume-first')
EMAIL = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


class AccountError(RuntimeError):
    """A problem the user can act on; the message is shown as is."""


def _identity(config):
    """Account the config says Claude Code is logged in as: (email, organization) or None."""
    account = config.get('oauthAccount')
    email = account.get('emailAddress') if isinstance(account, dict) else None
    return (email, account.get('organizationUuid') or '') if isinstance(email, str) and email else None


def _label(record):
    return record.get('alias') or record.get('email') or 'La cuenta'


def _expiry(stored):
    value = credentials.login(stored).get('expiresAt')
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _binding(usage):
    """Consumption of the tightest window, or None without figures."""
    return max((window['pct'] for window in usage.values()), default=None) if usage else None


def _weekly_reset(usage):
    # Accounts without a known reset sort last.
    return (usage.get('sevenDay') or {}).get('resetsAt') or '9999'


class Accounts:
    def __init__(self, clock=time.time):
        self.registry = Registry()
        self._clock = clock
        self._lock = threading.RLock()
        self._usage = {}
        self._verdict = None
        self._rotated_at = None
        with self.registry.lock():
            self.registry.import_claude_swap()

    # --- Reading the current state -------------------------------------------------------

    def _live(self):
        return credentials.read_active(), storage.read_json(paths.claude_config()) or {}

    def _record(self, data, number):
        record = data['accounts'].get(str(number))
        if record is None:
            raise AccountError('La cuenta seleccionada ya no está vinculada.')
        return record

    def _profile(self, number, record):
        return sessions.profile_dir(self.registry.root, number, record.get('email') or '')

    def _stored(self, number, record):
        """Newest saved login of an account: a parallel session renews its own copy."""
        saved = self.registry.credentials(number)
        session = sessions.stored_credentials(self._profile(number, record))
        return session if credentials.has_login(session) and _expiry(session) > _expiry(saved) else saved

    def _owner(self, data, live, config):
        """Which linked account the live login belongs to, and how sure that is.

        'own' is certain; 'unverified' means the config names the account but the
        service could not confirm it; 'unlinked' is a login of an account that is
        not linked; 'none' is logged out. Never call this holding Claude Code's
        locks: it may ask the service.
        """
        if not credentials.has_login(live):
            return None, 'none'
        accounts = data['accounts']
        identity = _identity(config)
        claimed = find(data, *identity) if identity else None
        tokens = credentials.login(live)
        for number in accounts:
            saved = self.registry.credentials(number)
            if credentials.is_api_key(live):
                if saved.strip() == live.strip():
                    return number, 'own'
            # Same token lineage as a saved login: certain without asking anyone.
            elif any(tokens.get(key) and tokens[key] == credentials.login(saved).get(key) for key in ('refreshToken', 'accessToken')):
                return number, 'own'
        if credentials.is_api_key(live):
            return (claimed, 'unverified') if claimed and accounts[claimed].get('kind') == 'api_key' else (None, 'unlinked')
        key = (live, identity, tuple(sorted(accounts)))
        if self._verdict and self._verdict[0] == key and self._clock() < self._verdict[1]:
            return self._verdict[2]
        who = oauth.profile(tokens['accessToken']) if tokens.get('accessToken') else None
        if who is None:
            verdict = (claimed, 'unverified') if claimed else (None, 'unlinked')
        else:
            matches = [number for number, record in accounts.items() if record.get('uuid') == who['uuid']] or \
                      [number for number, record in accounts.items() if not record.get('uuid') and who['email'] and record.get('email') == who['email']]
            if len(matches) > 1:
                # The same person in a personal and an organization account.
                exact = [number for number in matches if (accounts[number].get('organizationUuid') or '') == (who['organizationUuid'] or '')]
                matches = exact if len(exact) == 1 else [claimed] if claimed in matches else []
            verdict = (matches[0], 'own') if matches else (None, 'unlinked')
        self._verdict = (key, self._clock() + USAGE_TTL, verdict)
        return verdict

    def _sync(self, data, live, config):
        """Find the active account and keep its saved login current.

        Claude Code renews the login while it works, so the saved copy of the
        active account would otherwise fall behind. Returns (active, foreign);
        foreign means the live login is not the active account's own.
        """
        owner, state = self._owner(data, live, config)
        identity = _identity(config)
        claimed = find(data, *identity) if identity else None
        active = owner or claimed
        renewed = state == 'own' and (self.registry.credentials(owner) != live or data['accounts'][owner].get('needsLogin'))
        moved = active is not None and data.get('active') != int(active)
        if renewed or moved:
            with self.registry.lock():
                if renewed:
                    self.registry.save_credentials(owner, live)
                    data['accounts'][owner].pop('needsLogin', None)
                if moved:
                    data['active'] = int(active)
                self.registry.save(data)
        return active, state == 'unlinked' and claimed is not None

    # --- Quotas --------------------------------------------------------------------------

    def _save_login(self, number, record, stored):
        with self.registry.lock():
            self.registry.save_credentials(number, stored)
            profile = self._profile(number, record)
            if (profile / '.credentials.json').exists() and not sessions.is_live(profile):
                sessions.save_credentials(profile, stored)

    def _try_renew(self, number, record, stored):
        """Renew an idle account's expired login. Returns (login, '' | 'relogin_required' | 'token_expired')."""
        if not oauth.expired(stored, self._clock()):
            return stored, ''
        renewed, reason = oauth.refresh(stored, self._clock())
        if renewed:
            self._save_login(number, record, renewed)
            return renewed, ''
        if reason != 'invalid_grant':
            return stored, 'token_expired'
        # Only a new login fixes this; remember it so the dead token is not sent again.
        record['needsLogin'] = True
        with self.registry.lock():
            data = self.registry.load()
            if number in data['accounts']:
                data['accounts'][number]['needsLogin'] = True
                self.registry.save(data)
        return stored, 'relogin_required'

    def _known_status(self, number, record, live, foreign):
        """Why an account has no quota to ask for, decided without the network; '' when it can be asked."""
        if record.get('kind') == 'api_key':
            return 'api_key'
        if foreign:
            return 'foreign_credential'
        stored = live if live is not None else self._stored(number, record)
        if not credentials.has_login(stored):
            return 'no_credentials'
        if live is None and record.get('needsLogin'):
            return 'relogin_required'
        # The active account and any open parallel session renew their own login; doing it here too would log them out.
        if oauth.expired(stored, self._clock()) and (live is not None or sessions.is_live(self._profile(number, record))):
            return 'token_expired'
        return ''

    def _measure(self, data, live, active, foreign):
        now = self._clock()
        pending = {}
        for number, record in data['accounts'].items():
            entry = self._usage.setdefault(number, {'usage': None, 'status': 'unavailable', 'retry': 0, 'failures': 0})
            mine = live if number == active else None
            status = self._known_status(number, record, mine, foreign and number == active)
            if status:
                entry.update(usage=None, status=status, retry=0, failures=0)
                continue
            if now < entry['retry']:
                continue
            stored, status = (mine, '') if mine is not None else self._try_renew(number, record, self._stored(number, record))
            if status == 'relogin_required':
                entry.update(usage=None, status=status)
            elif status:
                self._failed(entry, status, now)
            else:
                pending[number] = credentials.login(stored).get('accessToken')
        if not pending:
            return

        def ask(token):
            try:
                return oauth.windows(oauth.usage(token))
            except (OSError, ValueError):
                return None

        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            answers = dict(zip(pending, pool.map(ask, pending.values())))
        for number, usage in answers.items():
            if usage is None:
                # The last known figures stay on screen while the service is unreachable.
                self._failed(self._usage[number], 'unavailable', now)
            else:
                self._usage[number].update(usage=usage, status='ok', retry=now + USAGE_TTL, failures=0)

    @staticmethod
    def _failed(entry, status, now):
        entry['failures'] += 1
        entry.update(status=status, retry=now + min(900, 30 * 2 ** (entry['failures'] - 1)))

    def _figures(self, number):
        """Quota good enough to decide on: only what the service answered on the last attempt."""
        entry = self._usage.get(number)
        return entry['usage'] if entry and entry['status'] == 'ok' else None

    def overview(self):
        with self._lock:
            data = self.registry.load()
            live, config = self._live()
            active, foreign = self._sync(data, live, config)
            self._measure(data, live, active, foreign)
            rows = []
            for number in sorted(data['accounts'], key=int):
                record, entry = data['accounts'][number], self._usage[number]
                row = {'number': int(number), 'email': record.get('email') or '', 'organizationName': record.get('organizationName') or '',
                       'organizationUuid': record.get('organizationUuid') or '', 'isOrganization': bool(record.get('organizationUuid')),
                       'active': number == active, 'usageStatus': entry['status'], 'usage': entry['usage']}
                if record.get('alias'):
                    row['alias'] = record['alias']
                if record.get('disabled'):
                    row['disabled'] = True
                rows.append(row)
            identity = _identity(config)
            current = None
            if active:
                current = dict(next(row for row in rows if row['active']), managed=True)
                del current['active']
                current.pop('disabled', None)
            elif identity:
                current = {'email': identity[0], 'managed': False}
            return {'status': {'schemaVersion': 1, 'active': current},
                    'list': {'schemaVersion': 1, 'activeAccountNumber': int(active) if active else None, 'accounts': rows}}

    # --- Linking -------------------------------------------------------------------------

    def _timestamp(self):
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    @staticmethod
    def _alias(data, alias, number):
        alias = alias.strip()
        if len(alias) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in alias):
            raise AccountError('El alias no es válido: usa hasta 80 caracteres en una sola línea.')
        if alias and any(other != number and (record.get('alias') or '').casefold() == alias.casefold() for other, record in data['accounts'].items()):
            raise AccountError('Ese alias ya lo usa otra cuenta.')
        return alias

    def link_current(self, alias=''):
        with self._lock:
            live, config = self._live()
            identity = _identity(config)
            if not credentials.has_login(live) or not identity:
                raise AccountError('No hay una sesión de Claude Code iniciada. Ejecuta `claude`, inicia sesión y vuelve a intentarlo.')
            if credentials.is_api_key(live):
                raise AccountError('La sesión actual usa una API key. Vincúlala desde la pestaña Token / API key.')
            tokens = credentials.login(live)
            who = oauth.profile(tokens['accessToken']) if tokens.get('accessToken') else None
            if who and who['email'] and who['email'] != identity[0]:
                # Mid /login the new credential is already in place while the config still names the old account.
                raise AccountError('Claude Code está cambiando de cuenta en este momento. Intenta de nuevo en unos segundos.')
            account = config['oauthAccount']
            with self.registry.lock():
                data = self.registry.load()
                number = find(data, *identity) or str(next_number(data))
                alias = self._alias(data, alias, number)
                record = data['accounts'].get(number) or {'added': self._timestamp()}
                record.update(email=identity[0], uuid=(who or {}).get('uuid') or account.get('accountUuid') or '', organizationUuid=identity[1],
                              organizationName=account.get('organizationName') or '', oauthAccount=account)
                record.pop('needsLogin', None)
                if alias:
                    record['alias'] = alias
                self.registry.save_credentials(number, live)
                data['accounts'][number] = record
                data['active'] = int(number)
                self.registry.save(data)
            return f'Cuenta vinculada: {_label(record)}.'

    def link_token(self, token, email='', slot=None, alias=''):
        token, email = token.strip(), email.strip()
        if not token:
            raise AccountError('Ingresa el token o API key.')
        if email and not EMAIL.match(email):
            raise AccountError('El correo o identificador no es válido.')
        if slot is not None and (isinstance(slot, bool) or not str(slot).isdigit() or int(slot) < 1):
            raise AccountError('El número de cuenta no es válido.')
        api_key = credentials.is_api_key(token)
        with self._lock, self.registry.lock():
            data = self.registry.load()
            number = str(int(slot)) if slot is not None else None
            if not email:
                number = number or str(next_number(data))
                email = f'{"api-key" if api_key else "setup-token"}-{number}@token.local'
            existing = find(data, email, '')
            number = number or existing or str(next_number(data))
            record = data['accounts'].get(number)
            if existing not in (None, number) or (record is not None and existing != number):
                raise AccountError(f'El número {number} o ese identificador ya pertenecen a otra cuenta.')
            if record is not None and (record.get('kind') == 'api_key') != api_key:
                raise AccountError('Ese identificador ya lo usa una cuenta de otro tipo.')
            if record is None:
                record = {'email': email, 'uuid': '', 'organizationUuid': '', 'organizationName': '', 'added': self._timestamp(),
                          'oauthAccount': {'emailAddress': email, 'accountUuid': '', 'organizationUuid': None, 'organizationName': None}}
                if api_key:
                    record['kind'] = 'api_key'
            record.pop('needsLogin', None)
            alias = self._alias(data, alias, number)
            if alias:
                record['alias'] = alias
            # A setup token is a login without a way to renew it.
            self.registry.save_credentials(number, token if api_key else json.dumps({'claudeAiOauth': {'accessToken': token, 'scopes': ['user:inference']}}))
            data['accounts'][number] = record
            self.registry.save(data)
            self._usage.pop(number, None)
            return f'Cuenta vinculada: {_label(record)}.'

    def remove(self, number):
        number = str(number)
        with self._lock, self.registry.lock():
            data = self.registry.load()
            record = self._record(data, number)
            profile = self._profile(number, record)
            if sessions.is_live(profile):
                raise AccountError(f'{_label(record)} tiene una sesión paralela abierta. Ciérrala antes de desvincularla.')
            sessions.remove(profile)
            self.registry.delete_credentials(number)
            del data['accounts'][number]
            if data.get('active') == int(number):
                data['active'] = None
            self.registry.save(data)
            self._usage.pop(number, None)
            return f'Cuenta desvinculada: {_label(record)}.'

    def set_alias(self, number, alias):
        number = str(number)
        with self._lock, self.registry.lock():
            data = self.registry.load()
            record = self._record(data, number)
            alias = self._alias(data, alias, number)
            if alias:
                record['alias'] = alias
            else:
                record.pop('alias', None)
            self.registry.save(data)
            return 'Alias guardado.' if alias else 'Alias eliminado.'

    def set_disabled(self, number, disabled):
        with self._lock, self.registry.lock():
            data = self.registry.load()
            record = self._record(data, number)
            if disabled:
                record['disabled'] = True
            else:
                record.pop('disabled', None)
            self.registry.save(data)
            return f'{_label(record)} quedó {"en pausa" if disabled else "disponible para la rotación"}.'

    # --- Switching -----------------------------------------------------------------------

    def _usable(self, number, record):
        """Saved login of an account that could take over right now, or None."""
        stored = self._stored(number, record)
        if not credentials.has_login(stored) or not record.get('oauthAccount') or record.get('needsLogin') or sessions.is_live(self._profile(number, record)):
            return None
        return stored

    def _require_usable(self, number, record):
        stored = self._stored(number, record)
        if not credentials.has_login(stored) or not record.get('oauthAccount'):
            raise AccountError(f'{_label(record)} no tiene credenciales guardadas. Inicia sesión con esa cuenta en Claude Code y vuelve a vincularla.')
        if record.get('needsLogin'):
            raise AccountError(f'{_label(record)} necesita iniciar sesión de nuevo. Entra con esa cuenta en Claude Code y vuelve a vincularla.')
        return stored

    def _choose(self, data, current, strategy):
        """Account to switch to under a strategy, or None when staying is the best choice."""
        if strategy not in STRATEGIES:
            raise AccountError('Estrategia no válida.')
        candidates = [number for number in sorted(data['accounts'], key=int)
                      if number != current and not data['accounts'][number].get('disabled') and self._usable(number, data['accounts'][number])]
        if not candidates:
            raise AccountError('No hay otra cuenta disponible para cambiar.')
        figures = {number: self._figures(number) for number in candidates}
        if strategy == 'next-available':
            position = int(current or 0)
            following = sorted(candidates, key=lambda number: (int(number) <= position, int(number)))
            # Without figures an account is assumed to have room.
            available = [number for number in following if (_binding(figures[number]) or 0) < 100]
            if not available:
                raise AccountError('Ninguna otra cuenta tiene cuota disponible en este momento.')
            return available[0]
        known = {number: usage for number, usage in figures.items() if usage}
        if not known:
            raise AccountError('Todavía no hay datos de cuota de otras cuentas para comparar.')
        if strategy == 'consume-first':
            open_ = [number for number in known if _binding(known[number]) < 100]
            if not open_:
                raise AccountError('Ninguna otra cuenta tiene cuota disponible en este momento.')
            return min(open_, key=lambda number: (_weekly_reset(known[number]), int(number)))
        best = min(known, key=lambda number: (_binding(known[number]), int(number)))
        mine = _binding(self._figures(current)) if current else None
        return best if mine is None or _binding(known[best]) < mine else None

    def switch(self, target=None, strategy=None):
        """Make another account the one Claude Code uses. Give a number, or a strategy to choose one."""
        with self._lock:
            data = self.registry.load()
            live, config = self._live()
            if target is None:
                active, foreign = self._sync(data, live, config)
                self._measure(data, live, active, foreign)
                target = self._choose(data, active, strategy or 'next-available')
                if target is None:
                    return 'La cuenta activa ya es la que tiene más cuota disponible.'
            number = str(target)
            record = self._record(data, number)
            stored = self._require_usable(number, record)
            if sessions.is_live(self._profile(number, record)):
                raise AccountError(f'{_label(record)} tiene una sesión paralela abierta. Ciérrala antes de usarla como cuenta principal.')
            owner, state = self._owner(data, live, config)
            if owner == number:
                return f'{_label(record)} ya es la cuenta activa.'
            if state == 'unlinked':
                raise AccountError('La sesión actual de Claude Code no pertenece a una cuenta vinculada. Vincúlala antes de cambiar de cuenta para no perderla.')
            # Everything that needs the network happens before Claude Code's locks are taken.
            stored, status = self._try_renew(number, record, stored)
            if status == 'relogin_required':
                raise AccountError(f'{_label(record)} necesita iniciar sesión de nuevo. Entra con esa cuenta en Claude Code y vuelve a vincularla.')
            self._activate(number, owner, live, stored)
            return f'Cuenta activa: {_label(record)}.'

    def _activate(self, number, owner, live, stored):
        with self.registry.lock(), storage.claude_locks():
            if credentials.read_active() != live:
                raise AccountError('La sesión de Claude Code cambió durante la operación. Intenta nuevamente.')
            data = self.registry.load()
            record = self._record(data, number)
            previous = storage.read_text(paths.claude_config())
            if owner in data['accounts']:
                # The live copy is the newest login of the account being left.
                self.registry.save_credentials(owner, live)
                leaving = data['accounts'][owner]
                leaving.pop('needsLogin', None)
                config = storage.read_json(paths.claude_config()) or {}
                if _identity(config) == (leaving.get('email'), leaving.get('organizationUuid') or ''):
                    leaving['oauthAccount'] = config['oauthAccount']
            try:
                credentials.write_active(credentials.with_shared_fields(stored, live))
                credentials.update_config(lambda config: config.update(oauthAccount=record['oauthAccount']))
                data['active'] = int(number)
                self.registry.save(data)
            except BaseException:
                # Leave Claude Code exactly as it was.
                if credentials.has_login(live):
                    credentials.write_active(live)
                if previous:
                    storage.write_private(paths.claude_config(), previous)
                raise

    def rotate(self, threshold, strategy):
        """One pass of automatic rotation. Returns a message when it switched, else None."""
        with self._lock:
            data = self.registry.load()
            live, config = self._live()
            active, foreign = self._sync(data, live, config)
            self._measure(data, live, active, foreign)
            current = _binding(self._figures(active)) if active else None
            now = self._clock()
            if current is None or current < threshold:
                return None
            # A floor on the switch rate, lifted only when the active account cannot work at all.
            if current < 100 and self._rotated_at is not None and now - self._rotated_at < ROTATION_COOLDOWN:
                return None
            figures = {number: self._figures(number) for number, record in data['accounts'].items()
                       if number != active and not record.get('disabled') and self._usable(number, record)}
            ready = {number: usage for number, usage in figures.items() if usage and _binding(usage) <= threshold - ROTATION_MARGIN}
            if not ready:
                return None
            if strategy == 'consume-first':
                target = min(ready, key=lambda number: (_weekly_reset(ready[number]), int(number)))
            else:
                target = min(ready, key=lambda number: (_binding(ready[number]), int(number)))
            message = self.switch(target)
            self._rotated_at = now
            return message

    # --- Parallel sessions ---------------------------------------------------------------

    def session(self, number):
        """Prepare a terminal session as another account.

        Returns the environment for the terminal, or None when the account is the
        active one: a second copy of the active login would compete with it.
        """
        number = str(number)
        with self._lock:
            data = self.registry.load()
            record = self._record(data, number)
            if record.get('kind') == 'api_key':
                raise AccountError('Las cuentas de API key no admiten sesión paralela.')
            live, config = self._live()
            if self._owner(data, live, config)[0] == number:
                return None
            stored = self._require_usable(number, record)
            profile = self._profile(number, record)
            if not sessions.is_live(profile):
                stored, status = self._try_renew(number, record, stored)
                if status == 'relogin_required':
                    raise AccountError(f'{_label(record)} necesita iniciar sesión de nuevo. Entra con esa cuenta en Claude Code y vuelve a vincularla.')
            sessions.prepare(profile, stored, record['oauthAccount'])
            return sessions.environment(profile)
