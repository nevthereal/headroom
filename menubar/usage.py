#!/usr/bin/env python3
"""Read-only Codex, Claude, and shared Grok quotas for the menu bar app."""
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from host import BridgeError, NoRedirect, codex_usage, cursor_usage

PRODUCT_LABELS = {'GrokBot': 'Grok Bot', 'Cursor': 'Cursor', 'GrokBuild': 'Grok Build'}
PRODUCT_ORDER = {'GrokBot': 0, 'Cursor': 1, 'GrokBuild': 2}
CLAUDE_WINDOWS = (('five_hour', '5-hour window'), ('seven_day', 'Weekly'),
                  ('seven_day_opus', 'Weekly · Opus'), ('seven_day_sonnet', 'Weekly · Sonnet'))


def normalize(data):
    if data.get('isUnified') is not True:
        raise BridgeError('Shared Grok allowance not confirmed. Check your account at grok.com/dashboard.')
    percent = data.get('usagePercent')
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or percent < 0:
        raise BridgeError('Grok did not report a valid usage percentage.')
    if data.get('includedLimitZero') is True or data.get('hasNonZeroIncludedLimit') is False:
        raise BridgeError('Your account reports no included Grok allowance.')
    try:
        reset = datetime.fromisoformat(data['nextResetTimestampUtc'].replace('Z', '+00:00'))
        if reset.tzinfo is None:
            raise ValueError('Timezone missing')
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise BridgeError('Grok did not report a valid reset time.') from exc
    return {'usedPercent': percent, 'resetsAt': reset.timestamp()}


def duration_label(minutes):
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        return 'Usage window'
    if minutes % 1440 == 0:
        return 'Weekly' if minutes == 10080 else f'{int(minutes // 1440)}-day window'
    if minutes >= 60:
        hours = minutes / 60
        text = f'{hours:.1f}'.rstrip('0').rstrip('.')
        return f'{text}-hour window'
    return f'{int(minutes)}-minute window'


def normalize_codex(data):
    grouped = data.get('rateLimitsByLimitId')
    limits = list(grouped.values()) if isinstance(grouped, dict) and grouped else [data.get('rateLimits')]
    windows = []
    for bucket in limits:
        if not isinstance(bucket, dict):
            continue
        prefix = bucket.get('limitName') if isinstance(bucket.get('limitName'), str) else ''
        limit_id = bucket.get('limitId')
        if not prefix and isinstance(limit_id, str) and limit_id not in ('', 'codex'):
            prefix = limit_id
        for key in ('primary', 'secondary'):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get('usedPercent')
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
                continue
            resets = window.get('resetsAt')
            resets_at = None
            if not isinstance(resets, bool) and isinstance(resets, (int, float)) and math.isfinite(resets) and resets > 0:
                resets_at = float(resets)
            label = ' · '.join(part for part in (prefix, duration_label(window.get('windowDurationMins'))) if part)
            windows.append({'label': label, 'usedPercent': float(used), 'resetsAt': resets_at})
    if not windows:
        raise BridgeError('Codex did not report quota windows. Check that the CLI uses a ChatGPT subscription login.')
    return windows


def _finite_percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _period_end(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        reset = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if reset.tzinfo is None:
        return None
    return reset.timestamp()


def normalize_billing(config):
    """Shared weekly pool, plus each product's share of that same pool."""
    if not isinstance(config, dict):
        raise BridgeError('Grok did not report a valid usage percentage.')
    total = _finite_percent(config.get('creditUsagePercent'))
    if total is None:
        raise BridgeError('Grok did not report a valid usage percentage.')
    period = config.get('currentPeriod') if isinstance(config.get('currentPeriod'), dict) else {}
    resets_at = _period_end(period.get('end')) or _period_end(config.get('billingPeriodEnd'))
    if resets_at is None:
        raise BridgeError('Grok did not report a valid reset time.')
    windows = [{'label': 'Weekly', 'usedPercent': total, 'resetsAt': resets_at}]
    products = []
    for item in config.get('productUsage') or []:
        if not isinstance(item, dict) or not isinstance(item.get('product'), str):
            continue
        used = _finite_percent(item.get('usagePercent'))
        if used is None:
            continue
        products.append((item['product'], used))
    seen = {name for name, _ in products}
    # A missing Grok Build row is zero use when the other products already add up to the pool.
    if config.get('isUnifiedBillingUser') is True and 'GrokBuild' not in seen and products:
        if abs(sum(used for _, used in products) - total) <= 0.6:
            products.append(('GrokBuild', 0.0))
    products.sort(key=lambda item: (PRODUCT_ORDER.get(item[0], 9), item[0]))
    for name, used in products:
        label = PRODUCT_LABELS.get(name)
        if label is None:
            label = ''.join(f' {character}' if character.isupper() else character for character in name).strip()
        windows.append({'label': label, 'usedPercent': used, 'resetsAt': None, 'display': 'used'})
    return windows


def grok_billing():
    """Read Grok Build's local session and ask only for the credits billing record."""
    try:
        auth = json.loads((Path.home() / '.grok' / 'auth.json').read_text())
        version = json.loads((Path.home() / '.grok' / 'version.json').read_text()).get('version')
    except (OSError, ValueError, AttributeError) as exc:
        raise BridgeError('Grok Build is not signed in.') from exc
    token = next((value.get('key') for value in auth.values()
                  if isinstance(value, dict) and isinstance(value.get('key'), str) and value['key'].strip()), None) if isinstance(auth, dict) else None
    if not token:
        raise BridgeError('Grok Build is not signed in.')
    request = urllib.request.Request(
        'https://cli-chat-proxy.grok.com/v1/billing?format=credits',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                 'x-grok-client-version': version if isinstance(version, str) and version else '0.0.0',
                 'x-grok-client-surface': 'grok-build'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            body = response.read(262145)
            if len(body) > 262144:
                raise BridgeError('Grok returned an oversized usage response.')
            payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise BridgeError('Grok Build usage is unavailable. Sign in with grok and refresh.') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BridgeError('Could not read Grok Build usage. Check your connection and refresh.') from exc
    config = payload.get('config') if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        raise BridgeError('Grok did not report a valid usage percentage.')
    return config


def normalize_claude(payload):
    if not isinstance(payload, dict):
        raise BridgeError('Claude returned an unrecognized usage response.')
    windows = []
    for key, label in CLAUDE_WINDOWS:
        window = payload.get(key)
        if not isinstance(window, dict):
            continue
        used = _finite_percent(window.get('utilization'))
        if used is None:
            continue
        windows.append({'label': label, 'usedPercent': used, 'resetsAt': _period_end(window.get('resets_at'))})
    if not windows:
        raise BridgeError('Claude did not report quota windows. Check that Claude Code uses a Claude subscription login.')
    return windows


def claude_credentials():
    """Claude Code's own login: the macOS Keychain item it writes, or its credentials file."""
    text = None
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                text = result.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
    if text is None:
        try:
            text = (Path.home() / '.claude' / '.credentials.json').read_text()
        except OSError as exc:
            raise BridgeError('Claude Code is not signed in. Run claude and log in, then refresh.') from exc
    try:
        oauth = json.loads(text).get('claudeAiOauth')
    except (ValueError, AttributeError) as exc:
        raise BridgeError('Could not read the Claude Code login. Run claude and log in again.') from exc
    token = oauth.get('accessToken') if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise BridgeError('Claude Code is not signed in with a Claude subscription. Run claude and log in.')
    expires = oauth.get('expiresAt')
    if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires / 1000 < time.time():
        # Refreshing here would rotate Claude Code's refresh token, so leave that to Claude Code.
        raise BridgeError('The Claude Code login has expired. Open Claude Code to refresh it, then refresh.')
    return token


def claude_usage():
    request = urllib.request.Request(
        'https://api.anthropic.com/api/oauth/usage',
        headers={'Authorization': f'Bearer {claude_credentials()}', 'Accept': 'application/json',
                 'anthropic-beta': 'oauth-2025-04-20'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            body = response.read(262145)
            if len(body) > 262144:
                raise BridgeError('Claude returned an oversized usage response.')
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in (401, 403):
            raise BridgeError('The Claude Code login was rejected. Open Claude Code to sign in again, then refresh.') from exc
        if exc.code == 429:
            raise BridgeError('Claude rate-limited the usage check. Wait a few minutes and refresh.') from exc
        raise BridgeError('Claude usage is unavailable. Try again shortly.') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BridgeError('Could not read Claude usage. Check your connection and refresh.') from exc


def _grok_windows():
    try:
        return normalize_billing(grok_billing())
    except BridgeError:
        usage = normalize(cursor_usage())
        return [{'label': 'Weekly', 'usedPercent': usage['usedPercent'], 'resetsAt': usage['resetsAt']}]


def snapshot():
    """Fetch every quota together. One failure does not hide the other."""
    results = {}
    lock = threading.Lock()

    def run(name, fn, fallback):
        try:
            value = {'ok': True, 'windows': fn()}
        except BridgeError as error:
            value = {'ok': False, 'error': str(error)}
        except Exception:
            value = {'ok': False, 'error': fallback}
        with lock:
            results[name] = value

    threads = (
        threading.Thread(target=run, args=('codex', lambda: normalize_codex(codex_usage(timeout=20)),
                                            'Could not read Codex usage. Check codex login and refresh.')),
        threading.Thread(target=run, args=('claude', lambda: normalize_claude(claude_usage()),
                                            'Could not read Claude usage. Check your Claude Code login and refresh.')),
        threading.Thread(target=run, args=('grok', _grok_windows,
                                            'Could not read Grok usage. Sign in to Cursor and refresh.')),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


if __name__ == '__main__':
    json.dump(snapshot(), sys.stdout, allow_nan=False)
    sys.stdout.write('\n')
