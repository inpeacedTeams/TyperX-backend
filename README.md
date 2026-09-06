# TyperX Backend

Natural typing automation engine with AI integration for Windows.

## Architecture

- `domain` — splitter, rhythm, typing physics, typos
- `services` — typing service, Monkeytype service and pacing
- `platform` — Win32/Interception keyboard driver, window management
- `persistence` — local state storage
- `ai.py` — AI configuration, LLM client, Telegram gateway, reply loop
- `ai_runtime.py` — runtime orchestration, cancellation, input protection
- `llm_provider.py` — provider-aware HTTP diagnostics
- `llm_stream.py` — SSE streaming generation
- `targeting.py` — sender targeting and context filtering
- `studio_hotkeys.py` — F8/F9 hotkey polling

## Installation

Windows 10/11, Python 3.12, installed Interception driver (run `install_driver.bat` as administrator, then reboot Windows).

```powershell
git clone https://github.com/inpeacedTeams/TyperX-backend.git
cd TyperX-backend
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Driver Installation

Run `install_driver.bat` as administrator. Reboot Windows after installation.

## Checks

```powershell
ruff check src tests
python -m pytest
python -m compileall -q src
```

Autonomous regression tests:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -p "test_ai*.py" -v
```

## Local Data

In `%APPDATA%\TyperX`: `ai.json` — settings and presets; `ai-secrets.bin` — LLM key and Telegram API hash under Windows DPAPI; `telegram.session` — Telethon SQLite session (not encrypted by DPAPI). Do not publish it or include it in cloud backups.

History and responses are not logged to disk. LLM redirects are prohibited. DPAPI does not protect against programs running as the same Windows user.

## Security

TyperX is local-first. It has no network client, analytics, updater or account system. Settings are stored in `%APPDATA%\TyperX`; logs contain lifecycle errors only and never message text.

The typing engine locks onto the foreground window captured at launch. If focus changes, input stops before the next key. `F9` is a global emergency stop.

Report vulnerabilities privately through GitHub Security Advisories.

## License

MIT
