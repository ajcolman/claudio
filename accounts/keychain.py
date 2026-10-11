"""Llavero de macOS mediante la herramienta security del sistema."""
import os
import subprocess

# Absolute on purpose: a PATH lookup could hand the secret to another binary.
SECURITY = '/usr/bin/security'
NOT_FOUND = 44
TIMEOUT = 5.0
# security -i reads each command into a 4096-byte line buffer.
STDIN_LIMIT = 4096 - 64


class KeychainError(RuntimeError):
    pass


def account_name():
    """Same account Claude Code uses, so both see the same Keychain item."""
    user = os.environ.get('USER')
    if user:
        return user
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return 'claude-code-user'


def _run(arguments, stdin=None):
    try:
        return subprocess.run([SECURITY] + arguments, input=stdin, capture_output=True, text=True, timeout=TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise KeychainError(str(error)) from error


def _failure(result):
    return KeychainError(result.stderr.strip() or f'security terminó con código {result.returncode}.')


def system_roots():
    """Root certificates macOS trusts, as PEM text; '' when they cannot be read."""
    try:
        return _run(['find-certificate', '-a', '-p', '/System/Library/Keychains/SystemRootCertificates.keychain']).stdout
    except KeychainError:
        return ''


def read(service, account=None):
    """Return the stored value, or None when the item does not exist."""
    result = _run(['find-generic-password', '-a', account or account_name(), '-w', '-s', service])
    if result.returncode == 0:
        return result.stdout.removesuffix('\n')
    if result.returncode == NOT_FOUND:
        return None
    raise _failure(result)


def write(service, value, account=None):
    account = account or account_name()
    encoded = value.encode('utf-8').hex()

    def quote(text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    command = f'add-generic-password -U -a {quote(account)} -s {quote(service)} -X {encoded}\n'
    # Through stdin the secret stays out of the process list; longer values do not fit the line buffer.
    if len(command.encode('utf-8')) <= STDIN_LIMIT:
        result = _run(['-i'], stdin=command)
    else:
        result = _run(['add-generic-password', '-U', '-a', account, '-s', service, '-X', encoded])
    if result.returncode:
        raise _failure(result)


def delete(service, account=None):
    result = _run(['delete-generic-password', '-a', account or account_name(), '-s', service])
    if result.returncode not in (0, NOT_FOUND):
        raise _failure(result)
