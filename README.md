# Headroom

A small macOS menu bar app that shows how much of your AI coding allowances you have left: **Codex** (ChatGPT plan), **Claude** (Claude plan via Claude Code), and the shared **Grok** weekly pool.

It has no Dock icon and no main window. The menu bar shows each service's logo with the percentage still remaining:

- **Codex** and **Claude** show their 5-hour allowance. Their weekly allowances are in the dropdown.
- **Grok** shows what is left of the shared weekly pool. The dropdown also shows how much of that pool Grok Bot, Cursor, and Grok Build have each used.

The dropdown lists every window on one line (name, percent left, time until reset). Hover a line for the exact reset time. **Show in Menu Bar** in the dropdown hides or shows each service; at least one always stays visible, and hidden services still appear in the dropdown.

Headroom refreshes every five minutes, when the Mac wakes, and when you choose Refresh (⌘R).

| Symbol | Meaning |
| --- | --- |
| `84%` | Percent remaining |
| `84%!` | Saved reading; the last update failed |
| `…` | First reading in progress |
| `—` | No reading available |

## Requirements

- macOS 13 or later
- Xcode command-line tools (`xcode-select --install`)
- Python 3.10 or later

Then sign in to whichever services you use. Each one is optional; a service you are not signed in to shows `—`.

| Service | Sign in with |
| --- | --- |
| Codex | `codex login` with your ChatGPT account. API-key logins have no subscription quota. |
| Claude | [Claude Code](https://claude.com/claude-code) (`claude`) with your Claude subscription. |
| Grok | The Grok Build CLI (`grok`), or the Cursor desktop app with the account linked to your X subscription. |

## Install

```sh
git clone <this repository> headroom
cd headroom
python3 menubar/build.py --install
open ~/Applications/Headroom.app
```

The build compiles the Swift app, signs it locally for your Mac (it is not notarized), and copies it to `~/Applications`. It bundles its Python helper and records the Python interpreter and Codex CLI paths found at build time, so it works at login without a terminal PATH. Rebuild if you move either one.

The first launch registers Headroom as a login item. **Open at Login** in the dropdown turns that off or on. If macOS asks, allow Headroom in System Settings → General → Login Items.

To uninstall, turn off Open at Login, quit Headroom, and delete `~/Applications/Headroom.app`.

## How it reads usage

All reads are read-only and use logins already saved on your Mac. Headroom never asks for a password and never signs in or refreshes a login itself.

- **Codex:** runs `codex app-server` and asks it for the account's rate limits.
- **Claude:** reads Claude Code's saved login from the macOS Keychain (`Claude Code-credentials`) and calls Anthropic's usage endpoint, the one behind Claude Code's `/usage`. If that login has expired, open Claude Code once.
- **Grok:** reads the Grok Build CLI's login from `~/.grok` and calls its billing endpoint. If that fails, it falls back to the Cursor desktop app's local login and Cursor's usage endpoint. A Cursor login stored encrypted is unlocked without showing a Keychain prompt.

Several of these endpoints are undocumented and can change without notice. When one breaks, only that service shows `—`; the others keep working.

## Privacy

- Credentials stay inside the short-lived Python helper. Only quota percentages and reset times are passed to the menu app.
- Each token is sent only to its own service's fixed HTTPS endpoint, and redirects are refused so a token is never forwarded elsewhere.
- No email addresses, account IDs, or tokens are logged, stored, or shown. Readings stay in memory.
- Headroom has no analytics and contacts no other servers.

## Development

```
menubar/main.swift   AppKit menu bar app
menubar/usage.py     Fetches and normalizes every service's quota
menubar/build.py     Builds, signs, and optionally installs the app
menubar/logos/       Menu bar logos (SVG sources and 128 px template PNGs)
native/host.py       Codex app-server and Cursor login readers
native/check.py      Live connection check for Codex or Cursor
tests/               Python unit tests
```

```sh
python3 -m unittest discover -s tests -p '*_test.py'   # unit tests
python3 menubar/build.py                                 # build dist/Headroom.app only
~/Applications/Headroom.app/Contents/MacOS/Headroom --check
```

`--check` reads live usage, builds the real menu, prints it as JSON, and exits. Run it while the app is closed.

## License

[MIT](LICENSE).

Headroom is an independent project. It is not affiliated with or endorsed by OpenAI, Anthropic, xAI, or Anysphere (Cursor). Codex, Claude, Grok, and Cursor are trademarks of their owners; their logos (from [svgl](https://svgl.app/)) are used only to identify each service.
