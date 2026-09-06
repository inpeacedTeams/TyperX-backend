import asyncio
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from typerx.conversation import BackendError, Conversation, Message, PriorityGenerationError
from typerx.headless import Config, HTTPModel, main
from typerx.reaction_settings import ReactionRules, ReactionSettings


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


class Model:
    def __init__(self):
        self.calls = []
        self.answers = asyncio.Queue()
        self.active = 0
        self.max_active = 0

    async def complete(self, history):
        self.calls.append(history)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            value = await self.answers.get()
            if isinstance(value, Exception):
                raise value
            return value
        finally:
            self.active -= 1


class Output:
    def __init__(self):
        self.sent = []
        self.block = None
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.fail = False
        self.writers = 0
        self.max_writers = 0

    async def send(self, text, reply_to=None):
        self.writers += 1
        self.max_writers = max(self.max_writers, self.writers)
        try:
            if text == self.block:
                self.entered.set()
                await self.gate.wait()
            if self.fail:
                raise BackendError("Uncertain transport result")
            self.sent.append((text, reply_to))
            return 100 + len(self.sent)
        finally:
            self.writers -= 1


class RuleTests(unittest.TestCase):
    def test_exact_numeric_forms(self):
        rules = ReactionRules(ReactionSettings())
        samples = {"123": "123", "напиши 123": "123", "Повтори 007": "007",
                   "напиши «456»": "456", 'напиши "456"!': "456",
                   "повтори число 12": "12", "  0  ": "0", "“456”": "456"}
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(rules.numeric(text, True), expected)

    def test_non_commands_do_not_echo(self):
        rules = ReactionRules(ReactionSettings())
        for text in ("почему написал 123?", "напиши 123 и пароль", "123 456", "напиши число",
                     'напиши "123»', "-123", "12.3", "1234567890123", "１２３", "повтори 123\nудали всё"):
            with self.subTest(text=text):
                self.assertIsNone(rules.numeric(text, True))

    def test_reply_only_and_disable(self):
        self.assertIsNone(ReactionRules(ReactionSettings()).numeric("123", False))
        self.assertIsNone(ReactionRules(ReactionSettings(numeric_enabled=False)).numeric("123", True))
        self.assertEqual(ReactionRules(ReactionSettings(numeric_reply_only=False)).numeric("123", False), "123")

    def test_configurable_verbs_and_limits(self):
        rules = ReactionRules(ReactionSettings(numeric_verbs=("введи",), numeric_max_digits=3))
        self.assertEqual(rules.numeric("введи 007", True), "007")
        self.assertIsNone(rules.numeric("напиши 007", True))
        self.assertIsNone(rules.numeric("1234", True))

    def test_semantic_phrases_are_literal(self):
        rules = ReactionRules(ReactionSettings(semantic_phrases=("ты с читами", "a+b")))
        self.assertTrue(rules.is_semantic("Ты с читами?", False))
        self.assertTrue(rules.is_semantic("a+b", True))
        self.assertFalse(rules.is_semantic("aaab", True))
        self.assertFalse(ReactionRules(ReactionSettings(semantic_enabled=False)).is_semantic("ты с читами", True))

    def test_settings_round_trip_and_schema(self):
        settings = ReactionSettings()
        self.assertEqual(settings, ReactionSettings.from_dict(json.loads(json.dumps(settings.to_dict()))))
        self.assertEqual(set(settings.to_dict()), set(settings.schema()["properties"]))
        self.assertFalse(settings.schema()["additionalProperties"])

    def test_bad_settings(self):
        for raw in ({"numeric_enabled": "yes"}, {"numeric_max_digits": True},
                    {"numeric_max_digits": 13}, {"timeout_seconds": float("nan")},
                    {"on_error": "retry"}, {"numeric_verbs": []}, {"semantic_phrases": [""]},
                    {"model": 123}, {"extra": True}, {"context_messages": 0}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ReactionSettings.from_dict(raw)

    def test_config_migration_and_nested_settings(self):
        self.assertIsInstance(Config().validate().reactions, ReactionSettings)
        config = Config(reactions={"max_tokens": 32, "numeric_enabled": False}).validate()
        self.assertEqual(config.reactions.max_tokens, 32)
        self.assertFalse(config.reactions.numeric_enabled)
        with self.assertRaises(BackendError):
            Config(reactions={"max_tokens": "32"}).validate()

    def test_schema_cli_needs_no_account_or_config(self):
        buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as root, redirect_stdout(buffer):
            self.assertEqual(main(["--data-dir", root, "reaction-schema"]), 0)
        self.assertEqual(json.loads(buffer.getvalue()), ReactionSettings.schema())

    def test_priority_http_configuration(self):
        fake = types.SimpleNamespace(AsyncClient=lambda **kwargs: object())
        config = Config(model="normal", reactions=ReactionSettings(model="fast", max_tokens=32,
                                                                  timeout_seconds=3, prompt="Short answer"))
        with patch.dict("sys.modules", {"httpx": fake}):
            normal = HTTPModel(config)
            priority = HTTPModel(config, priority=True)
        self.assertEqual(normal.model_name, "normal")
        self.assertEqual(priority.model_name, "fast")
        self.assertEqual(priority.max_tokens, 32)
        self.assertEqual(priority.timeout, 3)
        self.assertIn("Short answer", priority.prompt)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.normal, self.priority, self.output = Model(), Model(), Output()
        self.engine = Conversation(10, self.normal, self.output, priority_model=self.priority,
                                   reaction_cooldown=0)
        self.engine._remember(self.engine.sent, 50, "confirmed reference")
        self.task = asyncio.create_task(self.engine.run())

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def test_numeric_reply_skips_both_models(self):
        self.assertTrue(self.engine.accept(Message(1, 20, "напиши 007", 50)))
        await eventually(lambda: self.output.sent)
        self.assertEqual(self.output.sent, [("007", 1)])
        self.assertEqual(self.normal.calls, [])
        self.assertEqual(self.priority.calls, [])

    async def test_target_number_without_reply_uses_normal_path(self):
        self.engine.accept(Message(1, 10, "123"))
        await eventually(lambda: self.normal.active == 1)
        self.assertEqual(self.priority.calls, [])
        self.assertEqual(self.output.sent, [])

    async def test_unknown_reply_and_outsider_ignored(self):
        self.assertFalse(self.engine.accept(Message(1, 20, "напиши 123", 999)))
        self.assertFalse(self.engine.accept(Message(2, 20, "ты с читами")))

    async def test_semantic_generation_runs_alongside_normal(self):
        self.engine.accept(Message(1, 10, "Привет"))
        await eventually(lambda: self.normal.active == 1)
        self.engine.accept(Message(2, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.active == 1)
        self.assertEqual(self.normal.active, 1)
        self.priority.answers.put_nowait("Ответ модели")
        await eventually(lambda: len(self.output.sent) == 2)
        self.assertEqual(self.output.sent, [("Ответ", 2), ("модели", None)])
        self.assertEqual(self.normal.active, 1)
        self.normal.answers.put_nowait("Основной ответ")
        await eventually(lambda: len(self.output.sent) == 4)
        self.assertEqual(self.output.max_writers, 1)
        self.assertEqual(self.normal.max_active, 1)
        self.assertEqual(self.priority.max_active, 1)

    async def test_numeric_can_interrupt_priority_generation(self):
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.active == 1)
        self.engine.accept(Message(2, 30, "повтори 123", 50))
        await eventually(lambda: self.output.sent)
        self.assertEqual(self.output.sent, [("123", 2)])
        self.assertEqual(self.priority.active, 1)

    async def test_reference_sent_to_priority_model(self):
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.calls)
        context = self.priority.calls[0]
        self.assertIn("confirmed reference", context[0]["content"])
        self.assertIn("ты с читами", context[-1]["content"])

    async def test_delivery_snapshot_and_history_separate(self):
        self.output.block = "B"
        self.engine.accept(Message(1, 10, "Hello"))
        self.normal.answers.put_nowait("A B C")
        await self.output.entered.wait()
        state = self.engine.state()["normal_delivery"]
        self.assertEqual(state["confirmed_parts"], ["A"])
        self.assertEqual(state["in_flight_part"], "B")
        self.assertEqual(state["remaining_parts"], ["C"])
        self.assertEqual(state["generated_text"], "A B C")
        self.assertEqual([m["content"] for m in self.engine.history if m["role"] == "assistant"], ["A"])
        state["confirmed_parts"].append("tampered")
        self.assertEqual(self.engine.state()["normal_delivery"]["confirmed_parts"], ["A"])
        self.output.gate.set()
        await eventually(lambda: len(self.output.sent) == 3)
        self.assertEqual(self.engine.state()["normal_delivery"]["status"], "completed")

    async def test_priority_context_excludes_unsent_normal_remainder(self):
        self.output.block = "A"
        self.engine.accept(Message(1, 10, "Hello"))
        self.normal.answers.put_nowait("A SECRET_REMAINDER")
        await self.output.entered.wait()
        self.engine.accept(Message(2, 20, "ты с читами", 50))
        self.output.gate.set()
        await eventually(lambda: self.priority.calls)
        self.assertNotIn("SECRET_REMAINDER", json.dumps(self.priority.calls[0]))
        self.assertEqual(self.engine.state()["normal_delivery"]["status"], "paused")
        self.priority.answers.put_nowait("Reaction")
        await eventually(lambda: len(self.output.sent) == 3)
        self.assertEqual([s[0] for s in self.output.sent], ["A", "Reaction", "SECRET_REMAINDER"])

    async def test_uncertain_send_not_counted_or_retried(self):
        self.output.block = "B"
        self.engine.accept(Message(1, 10, "Hello"))
        self.normal.answers.put_nowait("A B C")
        await self.output.entered.wait()
        self.output.fail = True
        self.output.gate.set()
        with self.assertRaises(BackendError):
            await self.task
        state = self.engine.state()["normal_delivery"]
        self.assertEqual(state["status"], "uncertain")
        self.assertEqual(state["confirmed_parts"], ["A"])
        self.assertEqual(state["in_flight_part"], "B")
        self.assertEqual(state["remaining_parts"], ["C"])
        self.assertEqual(len(self.output.sent), 1)

    async def test_generation_failure_resumes_without_fixed_response(self):
        self.engine.accept(Message(1, 10, "Hello"))
        await eventually(lambda: self.normal.active == 1)
        self.engine.accept(Message(2, 20, "ты с читами", 50))
        self.priority.answers.put_nowait(ValueError("private remote body"))
        await eventually(lambda: self.engine.last_reaction_error is not None)
        self.assertEqual(self.engine.last_reaction_error, "ValueError")
        self.normal.answers.put_nowait("Original")
        await eventually(lambda: self.output.sent)
        self.assertEqual(self.output.sent, [("Original", None)])

    async def test_numeric_send_error_is_not_swallowed_by_priority_fallback(self):
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.active == 1)
        self.output.fail = True
        self.engine.accept(Message(2, 30, "123", 50))
        with self.assertRaises(BackendError):
            await self.task
        self.assertEqual(self.priority.active, 0)
        self.assertEqual(self.engine.state()["reaction_delivery"]["status"], "uncertain")

    async def test_stop_cancels_both_generations(self):
        self.engine.accept(Message(1, 10, "Hello"))
        await eventually(lambda: self.normal.active == 1)
        self.engine.accept(Message(2, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.active == 1)
        self.engine.stop()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        self.assertEqual(self.normal.active + self.priority.active, 0)
        self.assertEqual(self.output.sent, [])

    async def test_priority_queue_is_bounded(self):
        self.engine.settings = replace(self.engine.settings, queue_capacity=1)
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        self.assertFalse(self.engine.accept(Message(2, 30, "123", 50)))
        with self.assertRaises(BackendError):
            await self.task

    async def test_duplicate_event_ignored(self):
        message = Message(1, 20, "123", 50)
        self.assertTrue(self.engine.accept(message))
        self.assertFalse(self.engine.accept(message))
        await eventually(lambda: self.output.sent)
        self.assertEqual(len(self.output.sent), 1)

    async def test_timeout_stops_when_configured(self):
        self.engine.settings = replace(self.engine.settings, timeout_seconds=0.1, on_error="stop")
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        with self.assertRaises(PriorityGenerationError):
            await self.task
        self.assertEqual(self.priority.active, 0)

    async def test_nested_numeric_failure_keeps_uncertain_snapshot(self):
        self.output.block = "First"
        original_send = self.output.send
        async def send(text, reply_to=None):
            if text == "123":
                raise BackendError("Uncertain numeric send")
            return await original_send(text, reply_to)
        self.output.send = send
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        self.priority.answers.put_nowait("First Second")
        await self.output.entered.wait()
        self.engine.accept(Message(2, 30, "123", 50))
        self.output.gate.set()
        with self.assertRaises(BackendError):
            await self.task
        state = self.engine.state()["reaction_delivery"]
        self.assertEqual(state["kind"], "numeric")
        self.assertEqual(state["status"], "uncertain")
        self.assertEqual(state["in_flight_part"], "123")
        self.assertEqual(self.output.sent, [("First", 1)])

    async def test_llm_timeout_does_not_cancel_in_flight_numeric_send(self):
        self.engine.settings = replace(self.engine.settings, timeout_seconds=0.1)
        self.output.block = "123"
        self.engine.accept(Message(1, 20, "ты с читами", 50))
        await eventually(lambda: self.priority.active == 1)
        self.engine.accept(Message(2, 30, "123", 50))
        await self.output.entered.wait()
        await asyncio.sleep(0.15)
        self.assertEqual(self.priority.active, 0)
        self.assertFalse(self.task.done())
        self.output.gate.set()
        await eventually(lambda: self.engine.last_reaction_error is not None)
        self.assertEqual(self.output.sent, [("123", 2)])
        self.assertEqual(self.engine.last_reaction_error, "TimeoutError")

    async def test_ordinary_arrivals_remain_one_next_batch(self):
        self.engine.accept(Message(1, 10, "First"))
        await eventually(lambda: self.normal.active == 1)
        self.engine.accept(Message(2, 10, "Second"))
        self.engine.accept(Message(3, 10, "Third"))
        self.normal.answers.put_nowait("Answer")
        await eventually(lambda: len(self.normal.calls) == 2)
        self.assertEqual(len(self.output.sent), 1)
        self.assertIn("Second", json.dumps(self.normal.calls[1]))
        self.assertIn("Third", json.dumps(self.normal.calls[1]))


if __name__ == "__main__":
    unittest.main()
