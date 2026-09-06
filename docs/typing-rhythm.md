# Headless typing rhythm

The active backend now uses `KeyPlanner` in `typerx.keystrokes` for both output adapters. This replaces the simple per-character jitter timer in the first headless implementation. No additional configuration keys are required: set `wpm` in `backend.json`, for example `450`.

## Model and physical playback

- Reuses the existing `RhythmEngine`: correlated tempo/touch state, short phrase-level changes, punctuation/hesitation pauses, separate dwell and signed flight times.
- Keeps that state across fragments instead of reseeding/resetting for each word.
- Uses a validated 25..600 WPM profile directly; does not pass through the legacy 300-WPM normalizer.
- Disables the legacy executor-specific speed compensation. It is not a measured calibration for this new executor.
- Constructs paired key-down/key-up events, including Enter. Every fragment releases its character keys before the Enter press.
- Permits up to three overlapping character keys, but never overlapping presses assigned to the same physical finger. Standard Russian/English finger buckets are reused; number-row and Enter assignments are handled explicitly.
- Conservatively serializes uppercase, punctuation, spaces and layout-group transitions so pending modifiers/layout changes are not combined with other character presses.
- Performs whole-fragment character preflight before any typing. Unsupported text stops without silently replacing characters.
- Executes the plan in the dedicated COM/keyboard thread. Stop/window checks remain active; cleanup attempts to release all acquired keys even after an error.
- Late OS or UIA checks shift future events instead of creating a catch-up burst of rapid presses.

For the driver, the events are real Interception key presses/releases. For Telethon, only the **total simulated typing duration** is used as a cancellable pre-send delay; no keyboard is touched and no real key-down/key-up biometric trace is produced. The configured minimum send interval still applies. The previous response/resume/reply semantics are unchanged.

A numeric reaction still skips the LLM but now observes its short modeled typing duration before delivery. It cannot bypass Telegram pacing limits. Reactions remain fragment-boundary interruptions, not insertion into an unsent word.

## What this does not claim

This is a synthetic timing model, not a recording of a particular person and not a demonstrated indistinguishable human keyboard signature. It has not been calibrated on an empirical typing dataset. It does not include a learned vocabulary/digraph-frequency model. Typo injection is intentionally disabled in this iteration, including numeric reactions; configurable mistakes/corrections are not implemented by the new planner.

450 WPM is supported as a target, not a guaranteed wall-clock result or a claim that it is typical human typing. Same-finger constraints, conservative modifier handling, UIA checks, Enter, network confirmation and message boundaries can lower observed throughput substantially. Do not shorten physical constraints merely to force a throughput number.

## Validation

`tests/test_keystrokes.py` adds 16 tests: seeded reproducibility, paired events, overlap/finger/layout constraints, dwell variability, 450-WPM preservation, rhythm continuity, Enter ordering, input validation, playback/stop cleanup, no late-event compression, driver wiring/preflight, and Telethon delay/cancellation.

Together with the existing new headless tests, 42 isolated tests passed locally on Python 3.13 with fake clocks/keyboards/network transports. The CI matrix includes these tests on Linux and Windows, Python 3.12/3.13. This is not a live Windows driver test, a measured achieved-WPM test or proof of human-like biometric distributions.

Before unattended use, check actual down/up events on Windows, release behavior under F9/focus loss, Russian/English layout transitions, message confirmation and the real service's end-to-end latency. The overall PR remains under validation.
