import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
import urllib.error
import sys
import base64
from unittest.mock import patch
from contextlib import closing

spec = importlib.util.spec_from_file_location('host', Path(__file__).resolve().parents[1] / 'native/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class NativeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Safe Storage cipher')
    def test_safe_storage_decrypts_known_electron_vector(self):
        ciphertext = base64.b64decode('A8wqOIO5svMmlBfCpvD8MhY8kfOndjhC6BjKSrul8Yo=')
        self.assertEqual(host.decrypt_cursor_token(ciphertext, b'fixture-safe-storage-password'), 'fixture-cursor-access-token')

    def test_safe_storage_never_falls_back_to_sending_encrypted_token(self):
        with patch.object(host.sys, 'platform', 'darwin'):
            with self.assertRaises(host.BridgeError):
                host.cursor_access_token('cursor-safe-storage:v1:not-valid-base64')
            with patch.object(host, 'cursor_safe_storage_password', side_effect=host.BridgeError('No prompt allowed')):
                token = 'cursor-safe-storage:v1:' + base64.b64encode(b'v10' + bytes(32)).decode()
                with self.assertRaisesRegex(host.BridgeError, 'No prompt allowed'):
                    host.cursor_access_token(token)

    def cursor_database(self, directory):
        path = Path(directory) / 'state.vscdb'
        with closing(sqlite3.connect(path)) as c:
            c.execute('CREATE TABLE ItemTable (key TEXT, value TEXT)')
            c.execute('INSERT INTO ItemTable VALUES (?, ?)', ('cursorAuth/accessToken', 'fixture-private-token'))
            c.commit()
        return path

    def test_cursor_query_is_fixed_and_exports_only_quota_fields(self):
        payload = {'isUnified': True, 'usagePercent': 22, 'nextResetTimestampUtc': '2026-12-01T00:00:00Z',
                   'email': 'private@example.com', 'accessToken': 'secret', 'accountId': 'private'}
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return io.BytesIO(json.dumps(payload).encode())
        opener = Opener()
        with tempfile.TemporaryDirectory() as directory:
            db = self.cursor_database(directory)
            before = db.read_bytes()
            result = host.cursor_usage(db, opener)
            self.assertEqual(db.read_bytes(), before)
        self.assertEqual(set(result), {'isUnified', 'usagePercent', 'nextResetTimestampUtc'})
        self.assertEqual(opener.request.full_url, 'https://api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus')
        self.assertEqual(opener.request.method, 'POST')
        self.assertEqual(opener.request.data, b'{}')
        self.assertEqual(opener.request.get_header('Authorization'), 'Bearer fixture-private-token')

    def test_missing_cursor_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            with self.assertRaisesRegex(host.BridgeError, 'local Cursor login'):
                host.cursor_usage(path)
            self.assertFalse(path.exists())

    def test_cursor_http_errors_are_sanitized(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 401, 'private-token', {}, None)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(host.BridgeError) as caught:
                host.cursor_usage(self.cursor_database(directory), Opener())
        self.assertIn('login was rejected', str(caught.exception))
        self.assertNotIn('private-token', str(caught.exception))

    def test_redirect_does_not_forward_bearer_token(self):
        self.assertIsNone(host.NoRedirect().redirect_request(None, None, 302, 'Moved', {}, 'https://other.test'))

    def fake_codex(self, directory, mode='success'):
        path = Path(directory) / 'codex'
        path.write_text('''#!/usr/bin/env python3
import json,sys,time
mode = %r
for line in sys.stdin:
 m=json.loads(line)
 if m['method']=='initialized': continue
 if m['method']=='initialize':
  assert m['params']['clientInfo']['name']=='headroom'
  result={}
 elif m['method']=='account/read':
  result={'account': {'type':'apiKey' if mode=='api' else 'chatgpt', 'planType':'plus','email':'private@example.com'}}
 elif m['method']=='account/rateLimits/read':
  if mode=='timeout': time.sleep(10)
  print(json.dumps({'method':'account/rateLimits/updated','params':{}}),flush=True)
  result={'rateLimits': {'primary': {'usedPercent':25,'windowDurationMins':300,'resetsAt':1800000000}}}
 else: raise Exception('Unexpected method')
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''' % mode)
        path.chmod(0o700)
        return str(path)

    def test_protocol_handshake_and_no_account_identity_export(self):
        with tempfile.TemporaryDirectory() as directory:
            result = host.codex_usage(self.fake_codex(directory))
            self.assertEqual(result['plan'], 'plus')
            self.assertEqual(result['rateLimits']['primary']['usedPercent'], 25)
            self.assertNotIn('private@example.com', json.dumps(result))

    def test_api_key_login_message(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(host.BridgeError, 'API-key login'):
                host.codex_usage(self.fake_codex(directory, 'api'))

    def test_timeout_terminates_child(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(host.BridgeError, 'timed out'):
                host.codex_usage(self.fake_codex(directory, 'timeout'), timeout=0.3)


if __name__ == '__main__':
    unittest.main()
