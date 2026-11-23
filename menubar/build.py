#!/usr/bin/env python3
"""Build the Headroom menu bar app and optionally install it for this user."""
import argparse
import os
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys


def codex_path():
    found = shutil.which('codex')
    if found:
        return str(Path(found).expanduser())
    for candidate in (Path.home() / '.local/bin/codex', Path('/opt/homebrew/bin/codex'), Path('/usr/local/bin/codex')):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return ''


def remove_ours(target, bundle_id):
    if not target.exists():
        return
    try:
        with (target / 'Contents/Info.plist').open('rb') as file:
            existing = plistlib.load(file)
    except Exception:
        raise SystemExit(f'An unrelated {target.name} already exists in ~/Applications.')
    if existing.get('CFBundleIdentifier') != bundle_id:
        raise SystemExit(f'An unrelated {target.name} already exists in ~/Applications.')
    shutil.rmtree(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Copy the app to ~/Applications')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This menu bar app requires macOS.')
    root = Path(__file__).resolve().parent.parent
    app = root / 'dist/Headroom.app'
    contents = app / 'Contents'
    resources = contents / 'Resources'
    executable = contents / 'MacOS/Headroom'
    if resources.exists():
        shutil.rmtree(resources)
    resources.mkdir(parents=True, exist_ok=True)
    executable.parent.mkdir(parents=True, exist_ok=True)
    for source in (root / 'native/host.py', root / 'menubar/usage.py'):
        shutil.copy2(source, resources / source.name)
    for name in ('codex.png', 'claude.png', 'grok.png'):
        shutil.copy2(root / 'menubar/logos' / name, resources / name)
    shutil.copy2(root / 'menubar/Headroom.icns', resources / 'Headroom.icns')
    info = {
        'CFBundleName': 'Headroom',
        'CFBundleDisplayName': 'Headroom',
        'CFBundleIdentifier': 'com.headroom.usage',
        'CFBundleExecutable': 'Headroom',
        'CFBundlePackageType': 'APPL',
        'CFBundleIconFile': 'Headroom.icns',
        'CFBundleVersion': '2',
        'CFBundleShortVersionString': '1.1.0',
        'CFBundleInfoDictionaryVersion': '6.0',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '13.0',
        'LSApplicationCategoryType': 'public.app-category.utilities',
        'NSHighResolutionCapable': True,
        'HeadroomPythonPath': sys.executable,
        'HeadroomCodexPath': codex_path(),
    }
    with (contents / 'Info.plist').open('wb') as file:
        plistlib.dump(info, file)
    subprocess.run(['xcrun', 'swiftc', '-O', '-target', f'{platform.machine()}-apple-macosx13.0',
                    '-framework', 'AppKit', '-framework', 'ServiceManagement',
                    '-module-cache-path', str(root / 'dist/.swift-module-cache'),
                    str(root / 'menubar/main.swift'), '-o', str(executable)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
    if args.install:
        applications = Path.home() / 'Applications'
        applications.mkdir(exist_ok=True)
        remove_ours(applications / 'Headroom.app', info['CFBundleIdentifier'])
        target = applications / 'Headroom.app'
        shutil.copytree(app, target)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(target)], check=True)
        app = target
    print(app)


if __name__ == '__main__':
    main()
