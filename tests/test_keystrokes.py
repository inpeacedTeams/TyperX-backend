import asyncio
import threading
import types
import unittest
from unittest.mock import AsyncMock, patch

from typerx.backend_outputs import DriverOutput, TelegramOutput
from typerx.conversation import BackendError
from typerx.keystrokes import KeyPlanner, layout_group, physical_finger, play


class Clock:
    def __init__(self):
        self.now = 0.0
        self.cancelled = False

    def time(self):
        return self.now

    def is_set(self):
        return self.cancelled

    def wait(self, seconds):
        self.now += seconds
        return self.cancelled


class Keyboard:
    def __init__(self, clock):
        self.clock = clock
        self.downs = []
        self.ups = []
        self.active = set()
        self.closed = False

    def press(self, char):
        token = len(self.downs)
        self.downs.append((token, char, self.clock.now))
        self.active.add(token)
        return token

    def press_enter(self):
        return self.press("\n")

    def release(self, token):
        self.active.remove(token)
        self.ups.append((token, self.clock.now))

    def _resolve_char(self, char):
        if char == "☃":
            raise BackendError("Unsupported character")

    def close(self):
        self.closed = True


class PlannerTests(unittest.TestCase):
    def test_reproducible_and_balanced(self):
        a = KeyPlanner(450, seed=7).plan("привет как дела")
        b = KeyPlanner(450, seed=7).plan("привет как дела")
        self.assertEqual(a, b)
        self.assertEqual([e.char for e in a.events if e.down], list("привет как дела\n"))
        self.assertEqual(len(a.events), 2 * len("привет как дела\n"))
        self.assertEqual(list(a.events), sorted(a.events, key=lambda e: (e.at, e.down, e.token)))

    def test_physical_constraints_across_seeds(self):
        text = "asdf jkl привет Привет aa qaz 123455 РФ !? abcабв"
        for seed in range(20):
            active = {}
            for event in KeyPlanner(450, seed=seed).plan(text).events:
                if event.down:
                    finger = physical_finger(event.char)
                    self.assertNotIn(finger, [physical_finger(c) for c in active.values()])
                    if active:
                        self.assertIsNotNone(layout_group(event.char))
                        self.assertTrue(all(layout_group(c) == layout_group(event.char)
                                            for c in active.values()))
                    active[event.token] = event.char
                    self.assertLessEqual(len(active), 3)
                else:
                    self.assertEqual(active.pop(event.token), event.char)
            self.assertFalse(active)

    def test_overlap_is_possible(self):
        active = 0
        max_active = 0
        for event in KeyPlanner(450, seed=1).plan("ajskdlfjaskdlf").events:
            active += 1 if event.down else -1
            max_active = max(max_active, active)
        self.assertGreater(max_active, 1)

    def test_holds_are_not_intervals(self):
        plan = KeyPlanner(450, seed=5).plan("ajskdlfjaskdlf")
        starts = {e.token: e.at for e in plan.events if e.down}
        holds = [e.at - starts[e.token] for e in plan.events if not e.down]
        self.assertTrue(all(0.0449 <= hold <= 0.1801 for hold in holds))
        self.assertGreater(len({round(hold, 5) for hold in holds}), 3)

    def test_450_is_not_clamped(self):
        self.assertEqual(KeyPlanner(450).rhythm.profile.wpm, 450)
        self.assertEqual(KeyPlanner(450).rhythm._runtime_compensation(), 1.0)

    def test_state_persists_between_fragments(self):
        planner = KeyPlanner(450, seed=5)
        planner.plan("привет")
        next_plan = planner.plan("брат")
        self.assertGreater(next_plan.events[0].at, 0)
        self.assertNotEqual(next_plan, KeyPlanner(450, seed=5).plan("брат"))

    def test_enter_after_all_character_releases(self):
        plan = KeyPlanner(450, seed=0).plan("hello")
        enter = next(e.at for e in plan.events if e.down and e.char == "\n")
        self.assertGreater(enter, max(e.at for e in plan.events if not e.down and e.char != "\n"))

    def test_invalid_input(self):
        for value in ("", "a\nb", "a\tb", "a" * 8001):
            with self.subTest(value=value[:10]), self.assertRaises(BackendError):
                KeyPlanner(450).plan(value)
        for speed in (0, 601):
            with self.assertRaises(ValueError):
                KeyPlanner(speed)


class PlaybackTests(unittest.TestCase):
    def test_replay_and_cleanup(self):
        clock = Clock()
        keyboard = Keyboard(clock)
        plan = KeyPlanner(450, seed=2).plan("ajsk")
        play(plan, keyboard, lambda: None, clock, clock=clock.time)
        self.assertEqual([c for _, c, _ in keyboard.downs], list("ajsk\n"))
        self.assertFalse(keyboard.active)
        self.assertGreaterEqual(clock.now, plan.duration)

    def test_stop_releases_keys_and_does_not_send_enter(self):
        clock = Clock()
        keyboard = Keyboard(clock)
        def check():
            if keyboard.active:
                clock.cancelled = True
                raise BackendError("Stopped")
        with self.assertRaises(BackendError):
            play(KeyPlanner(450, seed=1).plan("ajsk"), keyboard, check, clock, clock=clock.time)
        self.assertFalse(keyboard.active)
        self.assertNotIn("\n", [c for _, c, _ in keyboard.downs])

    def test_already_stopped_does_not_press(self):
        clock = Clock()
        clock.cancelled = True
        keyboard = Keyboard(clock)
        with self.assertRaises(BackendError):
            play(KeyPlanner(450).plan("a"), keyboard, lambda: None, clock, clock=clock.time)
        self.assertEqual(keyboard.downs, [])

    def test_slow_guard_does_not_compress_scheduled_events(self):
        clock = Clock()
        keyboard = Keyboard(clock)
        def check():
            clock.now += 0.04
        plan = KeyPlanner(450, seed=2).plan("ajsk")
        play(plan, keyboard, check, clock, clock=clock.time)
        starts = {token: at for token, _, at in keyboard.downs}
        planned_starts = {e.token: e.at for e in plan.events if e.down}
        planned_holds = {e.token: e.at - planned_starts[e.token] for e in plan.events if not e.down}
        for token, at in keyboard.ups:
            self.assertGreaterEqual(at - starts[token] + 1e-9, planned_holds[token])

    def test_driver_preflight_and_real_planner_wiring(self):
        clock = Clock()
        keyboard = Keyboard(clock)
        driver = DriverOutput.__new__(DriverOutput)
        driver.hwnd = 1
        driver.stopped = clock
        driver.planner = KeyPlanner(450, seed=1)
        driver._check = lambda empty=False: None
        module = types.SimpleNamespace(InterceptionKeyboard=lambda hwnd: keyboard)
        def playback(plan, keys, check, stop):
            play(plan, keys, check, stop, clock=clock.time)
        with patch.dict("sys.modules", {"typerx.platform.interception_keyboard": module}), \
                patch("typerx.backend_outputs.play", side_effect=playback):
            driver._write("ajsk")
        self.assertEqual([c for _, c, _ in keyboard.downs], list("ajsk\n"))
        self.assertTrue(keyboard.closed)

    def test_driver_unsupported_character_types_nothing(self):
        keyboard = Keyboard(Clock())
        driver = DriverOutput.__new__(DriverOutput)
        driver.hwnd = 1
        driver.stopped = threading.Event()
        driver.planner = KeyPlanner(450)
        driver._check = lambda empty=False: None
        module = types.SimpleNamespace(InterceptionKeyboard=lambda hwnd: keyboard)
        with patch.dict("sys.modules", {"typerx.platform.interception_keyboard": module}), \
                self.assertRaises(BackendError):
            driver._write("a☃")
        self.assertEqual(keyboard.downs, [])
        self.assertTrue(keyboard.closed)


class TelegramTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_simulated_typing_before_send(self):
        client = AsyncMock()
        client.send_message.return_value.id = 10
        output = TelegramOutput(client, 1, 450, 0)
        output.planner = KeyPlanner(450, seed=1)
        expected = KeyPlanner(450, seed=1).plan("123").duration
        with patch("typerx.backend_outputs.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await output.send("123", 7)
        sleep.assert_awaited_once_with(expected)
        self.assertEqual(client.send_message.call_args.kwargs["reply_to"], 7)

    async def test_cancel_during_typing_delay_does_not_send(self):
        client = AsyncMock()
        output = TelegramOutput(client, 1, 450, 0)
        with patch("typerx.backend_outputs.asyncio.sleep", side_effect=asyncio.CancelledError), \
                self.assertRaises(asyncio.CancelledError):
            await output.send("123")
        client.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
