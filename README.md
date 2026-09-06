# TyperX Backend

Headless Telegram conversation service with an OpenAI-compatible LLM and two explicit output modes. No frontend and no HTTP server. Python 3.12/3.13; driver mode requires Windows and Interception.

## Active architecture

- `headless.py`: CLI, validated configuration, account lifecycle, cancellable HTTP client, single-process lock.
- `conversation.py`: single-writer scheduler, batched context, priority reactions, exact word splitting, bounded queues and deduplication.
- `backend_outputs.py`: Telethon sending or Windows driver typing; no automatic fallback.
- `platform/interception_keyboard.py`: existing physical keyboard adapter.

The CLI no longer uses the old UI-oriented `Runtime`/`SenderRuntime` hierarchy. Those modules and their regression tests remain as legacy compatibility code, not as the active service. `ai.json`, its presets and the old Telegram session are not automatically migrated. `GOAL-AI-MODE.md` has been removed.

## Conversation semantics

1. The first ordinary target message starts one LLM generation.
2. Later messages are buffered; they neither start parallel generations nor invalidate the current answer.
3. The current answer is sent in exact groups of `words` tokens. `words: 1` means one word per message, without the legacy smart splitter's merging.
4. Priority reactions can interrupt between confirmed fragments. The remaining original answer then resumes without regeneration or replay.
5. Buffered ordinary messages form one next batch, with the in-memory conversation context.

Only the selected target or a participant replying to a **confirmed service message in the selected chat** can trigger an answer. Messages from bots, anonymous/channel senders and unrelated participants are ignored. Replies to old/manual account messages do not qualify. Confirmation IDs and deduplication are bounded to the latest 4096 entries per session.

Exact numeric messages of 1..12 ASCII digits receive an immediate echo without an LLM call. The phrases `ты с софтом`, `нейронка`, `гейронка`, `автотайпер` receive the fixed honest response `Да, это автоматизированный ответ.` The service does not falsely claim to be a human. Other direct replies use the model; numeric reactions can interrupt their generation too. Reaction processing is rate-limited; an already sending reaction is completed before another reaction.

- **Telethon:** the first fragment of a reaction to a reply uses Telegram's `reply_to`; later fragments are plain messages. No Desktop window needed.
- **Driver:** every fragment is physically typed and sent with Enter in the selected Desktop chat. Reaction reply metadata is intentionally ignored. Interruption occurs after the current fragment is confirmed sent, not in the middle of an unsent word.

## Install

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Driver mode additionally needs the Interception driver: run `install_driver.bat` as administrator and reboot. Do not install the driver merely to use Telethon mode. Windows-only dependencies have platform markers; the isolated headless tests also run on Linux.

## Configure and run

```powershell
typerx init
```

This creates `%APPDATA%\TyperX\backend.json` on Windows (`~/.config/TyperX/backend.json` elsewhere). `init` refuses to overwrite an existing file. A custom location can be selected with `typerx --data-dir C:\TyperXData init`; use the same option for subsequent commands.

Provide credentials in the current process environment; never commit them or paste them into an issue. PowerShell can collect the secrets without saving their values in command history:

```powershell
$env:TYPERX_TELEGRAM_API_ID = Read-Host "Telegram api_id"
$env:TYPERX_TELEGRAM_API_HASH = [System.Net.NetworkCredential]::new("", (Read-Host "Telegram api_hash" -AsSecureString)).Password
$env:TYPERX_LLM_API_KEY = [System.Net.NetworkCredential]::new("", (Read-Host "LLM API key" -AsSecureString)).Password

typerx login
typerx chats
```

`login` prompts for phone, code and optional 2FA password. Obtain your own Telegram application credentials through my.telegram.org. An unauthenticated local LLM may omit `TYPERX_LLM_API_KEY`.

Edit `backend.json`:

- Set `chat_id` from `typerx chats` (groups use a negative ID).
- Run `typerx history` to list the last 50 message IDs and sender IDs for that chat, without printing message text. Set `target_sender_id` to the desired positive user ID. In a private chat it must equal `chat_id`.
- Choose `output`: `telethon` or `driver`.
- Set `base_url`, `model` and `prompt` for your provider/personality.
- Set `share_context: true` only after agreeing to transmit the accepted conversation messages, including qualifying third-party replies, to that provider.

Key defaults:

```json
{
  "output": "telethon",
  "chat_id": 0,
  "target_sender_id": 0,
  "wpm": 350,
  "words": 1,
  "min_send_interval": 1.0,
  "reaction_cooldown": 2.0,
  "queue_capacity": 256,
  "request_timeout": 30.0,
  "share_context": false
}
```

Zero IDs and `share_context: false` intentionally prevent running until configured. Omitted configuration fields use defaults. Unknown fields and invalid types are rejected.

```powershell
typerx check
typerx run
```

`check` validates configuration syntax only, not credentials, network access or the driver. In driver mode, open the selected chat in a uniquely named Telegram Desktop window, put focus in an empty message editor and press **F8**. A generic `Telegram` window title, duplicate chat names, an unavailable UIA editor or a non-Telegram process is rejected.

**Ctrl+C stops the service. F9 stops it on Windows**, including cancellation of pending generation. F9 also aborts the driver startup wait. There is no automatic restart after errors or focus loss. Inspect/clear any unfinished draft before restarting. `typerx logout` revokes the local Telegram session.

## Rates and failure policy

- `wpm` accepts 25..600; 350 is not clamped by the legacy 300-WPM configuration. In Telethon mode it is a pacing target using the conventional 5 characters per word, not physical typing. Actual delivery is also bounded by `min_send_interval` (default 1 second), network latency and Telegram limits.
- Driver timing uses physical key intervals, jitter and outgoing-message confirmation. Actual WPM depends on Windows UIA, keyboard layout changes and Telegram Desktop. It is not a guaranteed throughput.
- One-word messages can hit Telegram limits even at seemingly modest typing speeds. No rate setting guarantees freedom from FloodWait or account restrictions. Test with consenting participants; do not use the service for spam.
- No automatic retry of ambiguous sends, FloodWait, LLM failures or disconnects. The process stops with a safe diagnostic. This deliberately favors avoiding duplicates over uninterrupted availability.
- Accepted queues are bounded. Overflow and oversized incoming context stop the service rather than silently discarding messages.
- Conversation context, pending batches and resume position are in memory only. Restarting does not replay old events or restore pending output. The service starts fresh and does not automatically feed previous chat history to the model.
- Driver acknowledgements match outgoing text observed through Telethon. This is best-effort confirmation, not an identity proof: do not type manually or use another sending client on the same account while it runs.
- Private chats and ordinary groups are supported; broadcast channels and forum topics are rejected.

## Tests and current readiness

```powershell
python -m unittest discover -s tests -p "test_headless_backend.py" -v
python -m unittest discover -s tests -p "test_backend_http.py" -v
ruff check src tests
python -m pytest
python -m compileall -q src
```

The 26 new isolated scheduler/configuration/adapter/HTTP tests passed on Python 3.13 in a sandbox. New source modules passed syntax compilation. These tests use fake Telegram/model/HTTP transports; they are not a live integration test. The complete legacy suite, Ruff, real httpx/Telethon installation, Windows COM/Interception and account-level delivery still need CI and controlled Windows acceptance testing before calling this production-ready. The PR remains a draft until those checks pass.

Windows acceptance checklist: successful login; target-only dialogue; messages arriving during generation and typing form one next batch; numeric reply from a second participant; first Telethon fragment has the correct reply target; driver reaction is plain text; remaining answer resumes once; F9 during generation and typing; focus/editor change; unsupported character; FloodWait/disconnect; no automatic duplicate sends; second process rejected.

## Local data and security

`backend.json` contains non-secret settings and the prompt. `backend-telegram.session` grants access to the Telegram account and is **not encrypted by DPAPI**. Protect the whole data directory with OS permissions; exclude it from cloud backups. Environment variables are not a secret vault and are accessible to sufficiently privileged processes. Legacy DPAPI files are not used by this new CLI.

The service communicates with Telegram and the configured LLM provider. Local-first does not mean offline. No application HTTP listener, analytics or updater is started. Application error messages omit remote response bodies, keys and chat text. LLM redirects and environment-provided HTTP proxies are disabled; HTTPS is required except for localhost.

See [SECURITY.md](SECURITY.md). MIT license.
