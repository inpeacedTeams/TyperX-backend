# Configurable reactions and delivery state

## Implemented behavior

The headless backend now distinguishes local numeric checks from generative replies.

- By default, `123`, `напиши 123`, `повтори 123`, and `напиши число 123` are echoed locally **only when replying to a confirmed service message in the selected chat**. Paired straight/curly/angle quotes and one terminal `.`, `!` or `?` are accepted. Leading zeros are preserved. Only 1..12 ASCII digits are accepted by default.
- Sentences such as `почему ты написал 123?`, multiple numbers, mismatched quotes or extra instructions are not local numeric commands. They may follow the ordinary LLM reply path if eligible; they are not executed as application commands.
- Semantic challenges such as `ты с читами`, `ты с софтом`, `нейронка` and other configured literal phrases now use the priority LLM. The previous fixed automation response is removed from the active conversation loop.
- Other participants still need a reply to a service message; a keyword alone does not expand the audience. The target's configured semantic phrases may trigger priority generation without a reply unless `semantic_reply_only` is true. Other direct replies use the same priority model when semantic reactions are enabled.
- At most one normal and one priority model request can run concurrently. There is still only one output writer. A numeric reply can be sent while either model is generating, or at a boundary between semantic-response fragments. WPM, typing duration and send-rate limits still apply.
- The original response resumes after a reaction. Discarding/replacing its remainder is NOT added by this change.
- Through Telethon, the first reaction fragment is an addressable reply; later fragments are plain messages. Driver mode continues to type plain messages in the current guarded chat.

## Settings in backend.json

Add a `reactions` object; omitted fields use defaults. Existing configs without this object continue to load. `typerx init` includes the defaults in new configs. Unknown fields, invalid types and invalid ranges are rejected.

```json
{
  "reactions": {
    "numeric_enabled": true,
    "numeric_reply_only": true,
    "numeric_max_digits": 12,
    "numeric_verbs": ["напиши", "повтори"],
    "semantic_enabled": true,
    "semantic_reply_only": false,
    "semantic_phrases": ["ты с читами", "ты с софтом", "нейронка", "гейронка", "автотайпер", "ты бот"],
    "model": "",
    "prompt": "Кратко ответь на последнее обращение в стиле основного пресета. Не отрицай использование автоматизации, если это неправда.",
    "max_tokens": 80,
    "context_messages": 12,
    "timeout_seconds": 8.0,
    "queue_capacity": 32,
    "on_error": "resume"
  }
}
```

- `model`: blank inherits the normal model. A different model ID must be available from the same configured base URL and key; this does not add a second provider/account.
- `prompt`: added to the main personality prompt; system safety and plain-text-output constraints remain.
- `max_tokens`: 16..256. `context_messages`: 1..50 recent history entries, plus the referenced confirmed service message when available. This is not a full transcript of the chat.
- `timeout_seconds`: 0.1..60, for the priority LLM only. Its expiry does not cancel an already-started numeric send.
- `queue_capacity`: 1..256; the overall `queue_capacity` also applies. Overflow stops rather than silently dropping accepted events.
- `on_error`: `resume` skips a failed/empty/timed-out priority generation and resumes normal work without a canned reply or automatic retry. `stop` stops the service. Neither option swallows output failures: ambiguous sends always stop.
- Numeric verbs and semantic phrases are escaped literal strings, not executable regex or commands. Lists contain 1..30 nonempty strings of at most 100 characters.
- Disabling `numeric_enabled` disables the local echo shortcut, not the ordinary LLM reply path. Disabling `semantic_enabled` disables generative replies to other participants; target messages can still enter the normal batch. Setting `numeric_reply_only=false` allows local checks from the target without reply; it does not allow unrelated group participants.
- Existing top-level `reaction_cooldown` (default 2 seconds) limits completed reaction frequency. WPM remains a separate setting; there is no automatic 3x multiplier.

## Frontend integration contract

No frontend or HTTP API is introduced. The stable configuration representation is `ReactionSettings.to_dict()` / `ReactionSettings.from_dict()`. Obtain the JSON Schema, including defaults and ranges, with:

```powershell
typerx reaction-schema
```

This command needs no credentials or config file. A later frontend can render controls from the schema, validate the nested object and save it in `backend.json`. Settings are applied at service creation: stop, validate/save and restart. Hot editing an active loop is not implemented.

`Conversation.state()` returns a detached JSON-compatible snapshot on the conversation's asyncio loop. It includes generation flags, queue lengths, the last safe priority-error type and the current normal/reaction delivery states. Each delivery includes:

- `generated_text`: complete planned response, reconstructed from output fragments.
- `confirmed_parts`, `confirmed_text`, `confirmed_message_ids`: acknowledged output only.
- `in_flight_part`: the fragment currently being submitted; remains visible after an uncertain result.
- `remaining_parts`: fragments not yet attempted, excluding the in-flight fragment.
- `status`: `ready`, `sending`, `paused`, `completed`, `cancelled`, or `uncertain`.
- `source_message_ids` and `kind` (`normal`, `semantic`, `numeric`).

For example, if `A` is acknowledged, `B` is being sent and `C` is untouched, the state is confirmed `[A]`, in-flight `B`, remaining `[C]`. If sending B times out, status becomes `uncertain`, NOT completed or safe-to-retry.

Only confirmed fragments enter assistant history. Generated/remainder snapshots are never fed back as messages already said. These snapshots contain private text: expose them only to the authorized local operator, not logs or public dashboards. State is in-memory and is not a durable message audit trail or automatic restart recovery mechanism. Accounting is a correctness invariant, not a toggle to turn off.

## Validation

`tests/test_reactions.py` contains 28 isolated tests for parsing, settings/schema, migration, model selection, concurrent generation, one writer, reference context, receipt state, resume, unknown delivery, cancellation and timeout isolation. They passed locally with fake models/transports. Existing headless regressions were adjusted explicitly for reply-only defaults and generative semantic replies; the cross-platform CI includes the new tests. Live Telegram and Windows acceptance, plus a green full repository CI, are still release gates.
