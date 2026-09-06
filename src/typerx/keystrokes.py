"""Synthetic key-down/key-up timing and cancellable playback.

This is a model, not a measured human biometric profile. WPM is a target;
physical conflicts, pauses, OS overhead and message boundaries add time.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from typerx.conversation import BackendError
from typerx.domain.models import TypingProfile
from typerx.domain.rhythm import RhythmEngine
from typerx.domain.typing_physics import finger_for_key


class BackendRhythm(RhythmEngine):
    def _runtime_compensation(self) -> float:
        # Do not reuse the legacy empirical 2x speed-up for a different executor.
        return 1.0


@dataclass(frozen=True, slots=True)
class KeyEvent:
    at: float
    token: int
    char: str
    down: bool


@dataclass(frozen=True, slots=True)
class KeyPlan:
    events: tuple[KeyEvent, ...]

    @property
    def duration(self) -> float:
        return self.events[-1].at if self.events else 0.0


def layout_group(char: str) -> str | None:
    # Conservative overlap: no modifiers, punctuation or cross-layout chords.
    if "a" <= char <= "z":
        return "en"
    if "а" <= char <= "я" or char == "ё":
        return "ru"
    if "0" <= char <= "9":
        return "digits"
    return None


def physical_finger(char: str) -> str:
    if char == "\n":
        return "rp"
    fingers = {"1": "lp", "2": "lr", "3": "lm", "4": "li", "5": "li",
               "6": "ri", "7": "ri", "8": "rm", "9": "rr", "0": "rp"}
    return fingers.get(char, finger_for_key(char))


class KeyPlanner:
    MAX_HELD = 3

    def __init__(self, wpm: int, seed: int | None = None):
        if not 25 <= wpm <= 600:
            raise ValueError("wpm must be 25..600")
        # Intentionally not normalized(): legacy profiles clamp at 300 WPM.
        self.rhythm = BackendRhythm(TypingProfile(wpm=wpm, typo_rate=0, fix_typos=False),
                                    random.Random(seed))
        self.previous: str | None = None
        self.previous_length = 0

    def plan(self, text: str) -> KeyPlan:
        if not text or len(text) > 8000 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in text):
            raise BackendError("Invalid text for keyboard plan")
        events: list[KeyEvent] = []
        active: list[tuple[float, str]] = []
        onset = (self.rhythm.between_messages_pause(self.previous_length)
                 if self.previous_length else 0.0)
        group = None
        for token, char in enumerate(text):
            dwell, flight = self.rhythm.keystroke_timing(char, self.previous)
            finger = physical_finger(char)
            current_group = layout_group(char)
            if current_group is None or current_group != group:
                onset = max(onset, max((end for end, _ in active), default=onset))
            onset = max(onset, max((end + 0.004 for end, f in active if f == finger),
                                    default=onset))
            active = [(end, f) for end, f in active if end > onset]
            if len(active) >= self.MAX_HELD:
                onset = min(end for end, _ in active) + 0.002
                active = [(end, f) for end, f in active if end > onset]
            events.extend((KeyEvent(onset, token, char, True),
                           KeyEvent(onset + dwell, token, char, False)))
            active.append((onset + dwell, finger))
            onset += max(0.018, dwell + flight)
            self.previous, group = char, current_group
        onset = max(onset, max((end for end, _ in active), default=onset))
        onset += self.rhythm.before_send_pause()
        dwell, _ = self.rhythm.keystroke_timing("\n", self.previous)
        events.extend((KeyEvent(onset, len(text), "\n", True),
                       KeyEvent(onset + dwell, len(text), "\n", False)))
        # Release before pressing when timestamps are equal.
        events.sort(key=lambda event: (event.at, event.down, event.token))
        self.previous, self.previous_length = "\n", len(text)
        return KeyPlan(tuple(events))


def play(plan: KeyPlan, keyboard, check, stopped, *, clock=time.monotonic) -> None:
    """Replay without catch-up bursts; release every acquired key on any exit.

    Keyboard/check/clock/stop are injected so cancellation and scheduling can be
    tested without Windows. Cleanup key-up events remain permitted after stop.
    """
    origin = clock()
    held = {}
    try:
        for event in plan.events:
            deadline = origin + event.at
            while True:
                if stopped.is_set():
                    raise BackendError("Driver stopped; inspect the draft")
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                stopped.wait(min(0.01, remaining))
            if stopped.is_set():
                raise BackendError("Driver stopped; inspect the draft")
            check()
            if stopped.is_set():
                raise BackendError("Driver stopped; inspect the draft")
            # Slow guards/OS scheduling shift future events, never compress them.
            now = clock()
            origin += max(0.0, now - deadline)
            if event.down:
                held[event.token] = (keyboard.press_enter() if event.char == "\n"
                                     else keyboard.press(event.char))
            else:
                keyboard.release(held[event.token])
                del held[event.token]
            origin += max(0.0, clock() - now)
    finally:
        for key in list(held.values()):
            try:
                keyboard.release(key)
            except Exception:
                # Continue cleanup so one failed release cannot strand other keys.
                pass
