#!/usr/bin/env python3
"""Read-only live protocol checks; no raw account or credential output."""
import argparse
from host import BridgeError, codex_usage, cursor_usage

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['codex', 'cursor'], default='codex')
    args = parser.parse_args()
    try:
        if args.provider == 'cursor':
            data = cursor_usage()
            confirmed = data.get('isUnified') is True
            reported = isinstance(data.get('usagePercent'), (int, float)) and not isinstance(data.get('usagePercent'), bool)
            print(f'Cursor shared-pool endpoint connected. Unified allowance confirmed: {confirmed}. Usage reported: {reported}.')
            if not confirmed or not reported:
                raise SystemExit(1)
        else:
            data = codex_usage()
            buckets = data.get('rateLimitsByLimitId') or {'codex': data.get('rateLimits')}
            windows = sum(1 for bucket in buckets.values() if bucket for key in ('primary', 'secondary') if bucket.get(key))
            print(f'Codex app-server connected. Plan: {data.get("plan") or "not reported"}. Quota windows: {windows}.')
            if not windows:
                raise SystemExit(1)
    except BridgeError as error:
        print(error)
        raise SystemExit(1)
