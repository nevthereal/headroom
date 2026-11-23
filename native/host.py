#!/usr/bin/env python3
"""Read-only Codex and shared Cursor/Grok queries for the menu bar app."""
import json
import base64
import hashlib
from contextlib import closing
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


class BridgeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward the Cursor bearer token to another destination.
        return None


def cursor_access_token(value):
    if not value.startswith('cursor-safe-storage:'):
        return value
    if sys.platform != 'darwin' or not value.startswith('cursor-safe-storage:v1:'):
        raise BridgeError('The local Cursor login is encrypted. Sign in to the Cursor desktop app and refresh.')
    try:
        blob = base64.b64decode(value.removeprefix('cursor-safe-storage:v1:'), validate=True)
        if not blob.startswith(b'v10') or len(blob) <= 3 or (len(blob) - 3) % 16:
            raise ValueError('Invalid encrypted token')
        password = cursor_safe_storage_password()
        return decrypt_cursor_token(blob[3:], password)
    except (ValueError, OSError, UnicodeError) as exc:
        raise BridgeError('Could not unlock the local Cursor login. Sign in to the Cursor desktop app and refresh.') from exc


def cursor_safe_storage_password():
    """Read Cursor's own Safe Storage item with authentication UI explicitly disabled."""
    import ctypes as ct
    cf = ct.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    security = ct.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf.CFStringCreateWithCString.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_uint32]
    cf.CFStringCreateWithCString.restype = ct.c_void_p
    cf.CFDictionaryCreateMutable.argtypes = [ct.c_void_p, ct.c_long, ct.c_void_p, ct.c_void_p]
    cf.CFDictionaryCreateMutable.restype = ct.c_void_p
    cf.CFDictionarySetValue.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_void_p]
    cf.CFDictionarySetValue.restype = None
    cf.CFDataGetLength.argtypes = [ct.c_void_p]
    cf.CFDataGetLength.restype = ct.c_long
    cf.CFDataGetBytePtr.argtypes = [ct.c_void_p]
    cf.CFDataGetBytePtr.restype = ct.c_void_p
    cf.CFRelease.argtypes = [ct.c_void_p]
    cf.CFRelease.restype = None
    security.SecItemCopyMatching.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
    security.SecItemCopyMatching.restype = ct.c_int32

    def constant(library, name):
        return ct.c_void_p.in_dll(library, name).value

    for account in ('Cursor Key', 'Cursor'):
        query = cf.CFDictionaryCreateMutable(None, 0, None, None)
        allocated = []
        result = ct.c_void_p()
        try:
            for key, val in [('kSecClass', constant(security, 'kSecClassGenericPassword')),
                             ('kSecReturnData', constant(cf, 'kCFBooleanTrue')),
                             ('kSecMatchLimit', constant(security, 'kSecMatchLimitOne')),
                             ('kSecUseAuthenticationUI', constant(security, 'kSecUseAuthenticationUIFail'))]:
                cf.CFDictionarySetValue(query, constant(security, key), val)
            for key, text in [('kSecAttrService', 'Cursor Safe Storage'), ('kSecAttrAccount', account)]:
                val = cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)
                allocated.append(val)
                cf.CFDictionarySetValue(query, constant(security, key), val)
            if security.SecItemCopyMatching(query, ct.byref(result)) == 0 and result.value:
                size = cf.CFDataGetLength(result)
                if 0 < size < 4096:
                    return ct.string_at(cf.CFDataGetBytePtr(result), size)
        finally:
            if result.value:
                cf.CFRelease(result)
            cf.CFRelease(query)
            for val in allocated:
                cf.CFRelease(val)
    raise BridgeError('Cursor Safe Storage is unavailable without a Keychain prompt. Sign in to the Cursor desktop app and refresh.')


def decrypt_cursor_token(ciphertext, password):
    """Electron macOS v10 AES-CBC decoding; key and plaintext never enter argv or files."""
    import ctypes as ct
    crypto = ct.CDLL('/usr/lib/system/libcommonCrypto.dylib')
    crypto.CCCrypt.argtypes = [ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_void_p, ct.c_size_t,
                              ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p,
                              ct.c_size_t, ct.POINTER(ct.c_size_t)]
    crypto.CCCrypt.restype = ct.c_int32
    key = hashlib.pbkdf2_hmac('sha1', password, b'saltysalt', 1003, dklen=16)
    output = ct.create_string_buffer(len(ciphertext) + 16)
    written = ct.c_size_t()
    # decrypt=1, AES=0, PKCS7Padding=1; the Chromium v10 IV is sixteen spaces.
    status = crypto.CCCrypt(1, 0, 1, key, len(key), b' ' * 16, ciphertext,
                            len(ciphertext), output, len(output), ct.byref(written))
    if status != 0:
        raise ValueError('Safe Storage decryption failed')
    return output.raw[:written.value].decode('utf-8')


def cursor_usage(db_path=None, opener=None):
    if db_path is None:
        base = Path.home() / 'Library/Application Support' if sys.platform == 'darwin' else Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        db_path = base / 'Cursor/User/globalStorage/state.vscdb'
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            row = connection.execute("SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'").fetchone()
        token = row[0] if row else None
        if not isinstance(token, str) or not token.strip():
            raise BridgeError('No local Cursor login found. Sign in to Cursor and refresh.')
    except sqlite3.Error as exc:
        raise BridgeError('Could not read the local Cursor login. Sign in to the Cursor desktop app and refresh.') from exc
    token = cursor_access_token(token)
    request = urllib.request.Request(
        'https://api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus',
        data=b'{}', method='POST', headers={'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json', 'connect-protocol-version': '1'})
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            body = response.read(262145)
            if len(body) > 262144:
                raise BridgeError('Cursor returned an oversized usage response.')
            payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in (401, 403):
            raise BridgeError('The local Cursor login was rejected. Sign in to Cursor again, then refresh.') from exc
        if exc.code == 429:
            raise BridgeError('Cursor rate-limited the usage check. Wait a few minutes and refresh.') from exc
        raise BridgeError('Cursor usage is unavailable. Try again shortly.') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BridgeError('Could not read Cursor usage. Check your connection and refresh.') from exc
    if not isinstance(payload, dict):
        raise BridgeError('Cursor returned an unrecognized usage response.')
    # Export only known quota fields. Credentials and account identities stay in this process.
    fields = ('isUnified', 'usagePercent', 'nextResetTimestampUtc', 'currentPeriodStart',
              'hasAvailableUsage', 'hasNonZeroIncludedLimit', 'includedLimitZero')
    return {key: value for key in fields if isinstance((value := payload.get(key)), (str, int, float, bool))}


def codex_usage(binary=None, timeout=25):
    binary = binary or os.environ.get('HEADROOM_CODEX_BIN') or shutil.which('codex')
    if not binary:
        raise BridgeError('Codex CLI was not found. Install it, then rebuild Headroom.')
    try:
        process = subprocess.Popen([binary, 'app-server'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', cwd=Path.home(), bufsize=1)
    except OSError as exc:
        raise BridgeError('Could not start Codex. Install the CLI, then rebuild Headroom.') from exc
    messages = queue.Queue()

    def read_output():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout

    def send(message):
        process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()

    def request(request_id, method, params):
        send({'id': request_id, 'method': method, 'params': params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError('Codex timed out. Check your connection and CLI login, then retry.')
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise BridgeError('Codex timed out. Check your connection and CLI login, then retry.') from exc
            if message is None:
                raise BridgeError('Codex app-server exited before returning usage. Update the CLI and retry.')
            if not isinstance(message, dict) or message.get('id') != request_id:
                continue
            if 'error' in message:
                # Raw protocol errors can include account information. Keep them local.
                raise BridgeError(f'Codex could not complete {method}. Check codex login and update the CLI.')
            return message.get('result') or {}

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'headroom', 'title': 'Headroom', 'version': '1.0.0'}})
        send({'method': 'initialized', 'params': {}})
        account = request(2, 'account/read', {'refreshToken': False}).get('account')
        if not account:
            raise BridgeError('Codex is signed out. Run codex login with your ChatGPT account, then refresh.')
        if account.get('type') not in ('chatgpt', 'chatgptAuthTokens'):
            raise BridgeError('Codex uses an API-key login. Sign in with ChatGPT to view subscription quotas.')
        result = request(3, 'account/rateLimits/read', {})
        # Return only usage fields, never email, account identifiers, or credentials.
        return {'plan': account.get('planType'),
                'rateLimits': result.get('rateLimits'),
                'rateLimitsByLimitId': result.get('rateLimitsByLimitId')}
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()
