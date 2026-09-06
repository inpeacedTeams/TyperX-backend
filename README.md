# TyperX Backend

Headless Telegram conversation service with an OpenAI-compatible LLM and two output modes: Telethon sending or Windows Interception typing. No frontend and no HTTP listener. Python 3.12/3.13.

## Architecture and behavior

- `headless.py`: CLI, validated settings, Telegram lifecycle and separate normal/priority LLM clients.
- `conversation.py`: bounded incoming batches, priority reactions and a single output writer.
- `reaction_settings.py`: serializable reaction settings, literal rule matching and JSON Schema.
- `delivery_state.py`: confirmed output separated from in-flight and unattempted text.
- `keystrokes.py`: correlated key-down/key-up plans and cancellable playback.
- `backend_outputs.py`: explicit Telethon/driver adapters without automatic fallback.

The first ordinary target message starts one normal generation. Later ordinary messages accumulate for one next batch; they do not cancel the current answer. Priority reactions interrupt at output-fragment boundaries, then the original answer resumes. At most one normal and one priority LLM request run together; they never type concurrently.

Numeric checks in replies (`123`, `напиши 123`, `повтори 123`) can skip the LLM. Semantic challenges now use a configurable priority model, not the earlier fixed response. Through Telethon only the first reaction fragment uses reply_to; the driver types in the current chat without selecting a reply. Only the target or eligible participants replying to confirmed service messages are accepted.

See [reaction settings and delivery-state contract](docs/reactions.md) and [typing rhythm](docs/typing-rhythm.md). `GOAL-AI-MODE.md` was removed. Legacy UI-oriented runtime classes remain for compatibility tests but are not used by the CLI. Old `ai.json`/session settings are not automatically migrated.

## Install

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
typerx init
```

Driver mode requires Windows, the installed Interception driver and a reboot (`install_driver.bat` as administrator). Telethon mode does not need the driver or an open Telegram Desktop window. Windows dependencies have platform markers.

`init` creates `%APPDATA%\TyperX\backend.json` on Windows or `~/.config/TyperX/backend.json` elsewhere and refuses to overwrite an existing file. Use `typerx --data-dir C:\TyperXData init` for a different directory, and use that same option in subsequent commands.

## Credentials and chat selection

Obtain your own Telegram application credentials from my.telegram.org. Provide secrets in the current process environment; do not commit them or paste them into issues. PowerShell hidden input avoids storing actual secret values in command history:

```powershell
$env:TYPERX_TELEGRAM_API_ID = Read-Host "Telegram api_id"
$env:TYPERX_TELEGRAM_API_HASH = [System.Net.NetworkCredential]::new("", (Read-Host "Telegram api_hash" -AsSecureString)).Password
$env:TYPERX_LLM_API_KEY = [System.Net.NetworkCredential]::new("", (Read-Host "LLM API key" -AsSecureString)).Password

typerx login
typerx chats
```

`login` prompts for phone, code and optional 2FA. An unauthenticated local model may omit the LLM key.

Edit backend.json:

1. Set `chat_id` from `chats` (group IDs are negative).
2. Run `typerx history` to list recent message/sender IDs without message text. Set the desired positive `target_sender_id`; in a private chat it must equal chat_id.
3. Choose `output`: `telethon` or `driver`; set `base_url`, `model`, `prompt`, `wpm` and `words`.
4. Set `share_context: true` only after agreeing to send accepted messages, including eligible third-party replies, to that provider.
5. Optionally configure the nested `reactions` object described in [docs/reactions.md](docs/reactions.md). Old configs without it use defaults.

```powershell
typerx check
typerx run
```

`check` validates syntax/settings, not account/network/driver readiness. In driver mode, focus an empty editor in a uniquely named Telegram Desktop chat window, then press F8. Generic Telegram titles, duplicate chat names and unsupported UIA editors are rejected.

Ctrl+C stops the service; F9 stops it on Windows and aborts driver startup. Inspect any unfinished draft before restarting. `typerx logout` revokes the local session. There is no automatic restart or output-mode fallback.

## Configuration defaults and limits

- `wpm`: 350 by default, allowed 25..600. 450 is accepted without the legacy 300-WPM clamp. It is a target, not a throughput or human-biometric guarantee.
- `words`: 1 by default, allowed 1..16; exact grouping without the legacy smart splitter's merging.
- `min_send_interval`: 1 second by default, applies to Telethon. Telethon also waits for simulated typing duration before sending. Driver mode plays physical key events and waits for confirmation.
- `reaction_cooldown`: 2 seconds by default; total `queue_capacity`: 256; normal `request_timeout`: 30 seconds.
- Priority model/context/token/timeout settings are independent within `reactions`; the base URL and API key are shared. Two concurrent model requests can consume additional provider quota.
- Private chats and ordinary groups are supported; broadcast channels/forums are rejected.

One-word messages can hit Telegram restrictions. WPM cannot bypass FloodWait or network delays. Ambiguous sends, disconnects and overflow stop the service without retry. A failed priority generation may resume normal work when `reactions.on_error` is `resume`; output failures cannot use that fallback. Numeric reactions still obey typing and pacing limits.

Conversation history, queues, delivery IDs and resume positions are session-local. Restarting neither restores pending output nor replays old events; previous chat history is not automatically sent to the model. Driver outgoing-text matching is best-effort confirmation, not an identity proof: do not send manually or from another client on the same account during a run.

## Frontend later

```powershell
typerx reaction-schema
```

This exports JSON Schema without credentials or a config file. Future UI controls can consume the schema and the detached `Conversation.state()` delivery snapshots. No HTTP endpoint or frontend is added now; settings apply after stop/save/restart, not by mutating an active service. Snapshots contain private text and must not be published or logged.

## Checks and release gate

```powershell
python -m unittest discover -s tests -p "test_reactions.py" -v
python -m unittest discover -s tests -p "test_headless_backend.py" -v
python -m unittest discover -s tests -p "test_backend_http.py" -v
python -m unittest discover -s tests -p "test_keystrokes.py" -v
ruff check src tests
python -m pytest
python -m compileall -q src
```

The latest reaction increment passed 28 isolated local tests and syntax compilation. These use fake models/transports, not a live account. Existing headless tests were updated for new reaction defaults. Full CI and live Windows/Telegram acceptance must pass before production use; the PR remains under validation.

Acceptance: burst messages form one next batch; numeric reply skips LLM; semantic generation runs alongside normal generation with one writer; normal remainder resumes once; first Telethon reaction fragment is a reply; driver reactions are plain text; F9/focus loss releases keys; unknown sends are not counted as confirmed or retried; second local process is rejected.

## Security and local data

`backend-telegram.session` grants Telegram account access and is not encrypted by DPAPI. Protect the data directory with OS permissions and exclude it from cloud backups. Environment variables are not a secret vault. Settings and the prompt are in backend.json; new CLI settings do not use legacy DPAPI files.

Local-first does not mean offline: Telegram and the configured LLM receive network requests. Application diagnostics omit remote response bodies, keys and chat text. LLM redirects/environment HTTP proxies are disabled; HTTPS is required except for localhost. See [SECURITY.md](SECURITY.md). MIT license.
