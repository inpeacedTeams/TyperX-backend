# Security

TyperX is local-first, not offline. The headless backend connects to Telegram and the configured LLM provider. It does not start a frontend, HTTP API server, analytics client or updater.

## Data and credentials

- Settings and Telegram session files default to `%APPDATA%\TyperX` on Windows.
- `backend-telegram.session` is a Telethon SQLite session granting account access. It is not protected by DPAPI. Restrict OS access and exclude it from sharing/cloud backups.
- The new CLI reads Telegram application credentials and the LLM key from environment variables. Environment variables are not a vault; processes with sufficient privileges can read them. Do not commit them or attach them to bug reports.
- `share_context=true` authorizes sending accepted target messages and qualifying third-party replies to the chosen LLM provider. Check that provider's privacy policy and obtain any necessary participant consent.
- Conversation history and pending output remain in memory, not application logs. Exceptions are reported without remote body text or secrets. The `chats` command intentionally prints chat names and IDs; `history` prints message/sender IDs, not message content.

## Output and stop behavior

- There is one output executor and no automatic switch between Telegram sending and keyboard input.
- Ambiguous sends, disconnects, overflow and FloodWait stop the service; no automatic replay is attempted.
- Ctrl+C stops the process; F9 is an emergency stop on Windows. Already sent messages cannot be recalled by cancellation. Inspect partial driver drafts before restarting.
- Driver mode checks Telegram process, window title and UIA editor identity. This is a fail-closed best-effort guard, not a cryptographic chat identity guarantee. Generic window titles and duplicate chat names are rejected. Do not use the same account concurrently from another sending client or type manually during driver output.
- LLM text is data, never code or application commands. Control characters are removed. HTTP redirects are refused; external endpoints require HTTPS. The application does not load HTTP proxy settings from the environment.

The new headless implementation is under validation. Mock-based unit tests do not establish live Windows/Telegram safety or performance. Complete the acceptance checklist in README before unattended use.

Report vulnerabilities privately through GitHub Security Advisories. Do not open a public issue containing session files, credentials, private conversation text or exploit details.
