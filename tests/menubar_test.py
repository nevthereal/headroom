import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'native'))
spec = importlib.util.spec_from_file_location('menubar_usage', root / 'menubar/usage.py')
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class MenuBarTests(unittest.TestCase):
    def data(self, **changes):
        return {'isUnified': True, 'usagePercent': 25, 'nextResetTimestampUtc': '2026-12-01T00:00:00Z', **changes}

    def test_only_usage_and_reset_are_exported(self):
        value = usage.normalize(self.data(email='private@example.com', accessToken='secret'))
        self.assertEqual(value, {'usedPercent': 25, 'resetsAt': 1796083200.0})

    def test_requires_confirmed_shared_allowance(self):
        for flag in (None, False, 'true'):
            with self.subTest(flag=flag), self.assertRaises(usage.BridgeError):
                usage.normalize(self.data(isUnified=flag))

    def test_bad_usage_cannot_appear_as_available(self):
        for value in (None, True, -1, float('nan'), float('inf'), '25'):
            with self.subTest(value=value), self.assertRaises(usage.BridgeError):
                usage.normalize(self.data(usagePercent=value))
        self.assertEqual(usage.normalize(self.data(usagePercent=0))['usedPercent'], 0)

    def test_reset_requires_a_timezone(self):
        for value in (None, '', 'bad-date', '2026-12-01T00:00:00'):
            with self.subTest(value=value), self.assertRaises(usage.BridgeError):
                usage.normalize(self.data(nextResetTimestampUtc=value))

    def test_zero_limit_rejected(self):
        with self.assertRaises(usage.BridgeError):
            usage.normalize(self.data(includedLimitZero=True))

    def test_unexpected_errors_do_not_export_raw_details(self):
        with patch.object(usage, 'grok_billing', side_effect=usage.BridgeError('skip')), \
             patch.object(usage, 'cursor_usage', side_effect=RuntimeError('private-secret')), \
             patch.object(usage, 'codex_usage', side_effect=RuntimeError('secret-token')), \
             patch.object(usage, 'claude_usage', side_effect=RuntimeError('claude-secret')):
            result = usage.snapshot()
        self.assertFalse(result['claude']['ok'])
        self.assertNotIn('claude-secret', result['claude']['error'])
        self.assertFalse(result['grok']['ok'])
        self.assertFalse(result['codex']['ok'])
        self.assertNotIn('private-secret', result['grok']['error'])
        self.assertNotIn('secret-token', result['codex']['error'])

    def test_codex_exports_only_quota_windows(self):
        value = usage.normalize_codex({
            'plan': 'plus', 'email': 'private@example.com',
            'rateLimits': {'primary': {'usedPercent': 17, 'windowDurationMins': 10080, 'resetsAt': 1800000000}, 'secondary': None},
        })
        self.assertEqual(value, [{'label': 'Weekly', 'usedPercent': 17.0, 'resetsAt': 1800000000.0}])

    def test_codex_keeps_separate_windows_and_buckets(self):
        bucket = {'limitId': 'codex', 'primary': {'usedPercent': 0, 'windowDurationMins': 300}}
        value = usage.normalize_codex({
            'rateLimits': bucket,
            'rateLimitsByLimitId': {'codex': bucket, 'spark': {'limitName': 'Spark', 'secondary': {'usedPercent': 99, 'windowDurationMins': 10080}}},
        })
        self.assertEqual([window['label'] for window in value], ['5-hour window', 'Spark · Weekly'])
        self.assertEqual(value[0]['usedPercent'], 0)

    def test_codex_rejects_missing_windows(self):
        with self.assertRaises(usage.BridgeError):
            usage.normalize_codex({'rateLimits': {'primary': {'usedPercent': None}}})

    def test_one_provider_failing_does_not_hide_the_other(self):
        with patch.object(usage, 'grok_billing', side_effect=usage.BridgeError('skip')), \
             patch.object(usage, 'codex_usage', side_effect=usage.BridgeError('Codex is signed out.')), \
             patch.object(usage, 'cursor_usage', return_value=self.data()), \
             patch.object(usage, 'claude_usage', return_value={'five_hour': {'utilization': 12.0, 'resets_at': '2026-11-23T17:09:59.95+00:00'}}):
            result = usage.snapshot()
        self.assertTrue(result['claude']['ok'])
        self.assertFalse(result['codex']['ok'])
        self.assertEqual(result['codex']['error'], 'Codex is signed out.')
        self.assertTrue(result['grok']['ok'])
        self.assertEqual(result['grok']['windows'][0]['label'], 'Weekly')
        self.assertEqual(result['grok']['windows'][0]['usedPercent'], 25)
        self.assertNotIn('email', str(result))

    def test_claude_exports_only_quota_windows(self):
        windows = usage.normalize_claude({
            'five_hour': {'utilization': 1.0, 'resets_at': '2026-11-23T17:09:59.950276+00:00', 'used_dollars': None},
            'seven_day': {'utilization': 0.0, 'resets_at': '2026-11-24T14:59:59.950297+00:00'},
            'seven_day_opus': None, 'seven_day_sonnet': {'utilization': None},
            'account': {'email': 'private@example.com'},
        })
        self.assertEqual([(window['label'], window['usedPercent']) for window in windows],
                         [('5-hour window', 1.0), ('Weekly', 0.0)])
        self.assertIsNotNone(windows[0]['resetsAt'])
        self.assertNotIn('private@example.com', str(windows))

    def test_claude_rejects_missing_windows(self):
        for payload in (None, {}, {'five_hour': None}, {'five_hour': {'utilization': -1}}):
            with self.subTest(payload=payload), self.assertRaises(usage.BridgeError):
                usage.normalize_claude(payload)

    def billing(self, **products):
        return {'creditUsagePercent': 11, 'isUnifiedBillingUser': True,
                'currentPeriod': {'type': 'USAGE_PERIOD_TYPE_WEEKLY', 'end': '2026-11-23T10:00:30.028+00:00'},
                'productUsage': [{'product': name, 'usagePercent': used} for name, used in products.items()],
                'email': 'private@example.com', 'key': 'secret-token'}

    def test_shared_pool_splits_into_grok_bot_cursor_and_grok_build(self):
        windows = usage.normalize_billing(self.billing(GrokBot=6, Cursor=5))
        self.assertEqual([(window['label'], window['usedPercent']) for window in windows],
                         [('Weekly', 11.0), ('Grok Bot', 6.0), ('Cursor', 5.0), ('Grok Build', 0.0)])
        self.assertIsNotNone(windows[0]['resetsAt'])
        self.assertNotIn('display', windows[0])
        self.assertEqual(windows[1]['display'], 'used')
        self.assertIsNone(windows[1]['resetsAt'])
        self.assertNotIn('secret-token', str(windows))
        self.assertNotIn('private@example.com', str(windows))

    def test_explicit_grok_build_usage_is_kept(self):
        windows = usage.normalize_billing(self.billing(GrokBot=4, Cursor=3, GrokBuild=4))
        self.assertEqual([(window['label'], window['usedPercent']) for window in windows],
                         [('Weekly', 11.0), ('Grok Bot', 4.0), ('Cursor', 3.0), ('Grok Build', 4.0)])

    def test_unexplained_gap_does_not_invent_grok_build(self):
        windows = usage.normalize_billing(self.billing(GrokBot=2))
        self.assertEqual([window['label'] for window in windows], ['Weekly', 'Grok Bot'])
