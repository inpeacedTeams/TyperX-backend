import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typerx.backend_outputs import TelegramOutput
from typerx.conversation import BackendError, Conversation, Message, fragments
from typerx.headless import Config, main, session_lock


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


class FakeModel:
    def __init__(self):
        self.calls = []
        self.results = asyncio.Queue()
        self.active = 0
        self.max_active = 0

    async def complete(self, history):
        self.calls.append(history)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            return await self.results.get()
        finally:
            self.active -= 1


class FakeOutput:
    def __init__(self):
        self.sent = []
        self.hook = None

    async def send(self, text, reply_to=None):
        self.sent.append((text, reply_to))
        if self.hook:
            self.hook(text)
        return len(self.sent) + 100


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model, self.output = FakeModel(), FakeOutput()
        self.engine = Conversation(10, self.model, self.output, reaction_cooldown=0)
        self.task = asyncio.create_task(self.engine.run())

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def test_batch_arrivals_do_not_cancel_first_generation(self):
        self.engine.accept(Message(1, 10, "Привет"))
        await eventually(lambda: len(self.model.calls) == 1)
        self.engine.accept(Message(2, 10, "Как ты?"))
        self.engine.accept(Message(3, 10, "Что делаешь?"))
        self.assertEqual(len(self.model.calls), 1)
        self.model.results.put_nowait("Привет брат")
        await eventually(lambda: len(self.model.calls) == 2)
        self.assertEqual(self.output.sent, [("Привет", None), ("брат", None)])
        context = self.model.calls[1]
        self.assertTrue(any("Как ты?" in m["content"] for m in context))
        self.assertTrue(any("Что делаешь?" in m["content"] for m in context))
        self.model.results.put_nowait("Нормально")
        await eventually(lambda: len(self.output.sent) == 3)
        self.assertEqual(self.model.max_active, 1)

    async def test_numeric_interrupt_then_resume_without_duplicate_words(self):
        self.engine.accept(Message(1, 10, "Привет"))
        def hook(text):
            if text == "А":
                self.engine.accept(Message(2, 10, "123"))
        self.output.hook = hook
        self.model.results.put_nowait("А Б В")
        await eventually(lambda: len(self.output.sent) == 4)
        self.assertEqual([s[0] for s in self.output.sent], ["А", "123", "Б", "В"])

    async def test_numeric_while_llm_is_pending(self):
        self.engine.accept(Message(1, 10, "Привет"))
        await eventually(lambda: self.model.active == 1)
        self.engine.accept(Message(2, 10, "123"))
        await eventually(lambda: self.output.sent)
        self.assertEqual(self.output.sent, [("123", None)])
        self.assertEqual(self.model.active, 1)

    async def test_third_party_first_fragment_reply_rest_plain(self):
        self.engine._remember(self.engine.sent, 50)
        self.engine.accept(Message(2, 20, "автотайпер", 50))
        await eventually(lambda: len(self.output.sent) == 4)
        self.assertEqual(self.output.sent[0][1], 2)
        self.assertTrue(all(reply is None for _, reply in self.output.sent[1:]))
        self.assertEqual(len(self.model.calls), 0)

    async def test_other_sender_without_reply_ignored(self):
        self.assertFalse(self.engine.accept(Message(1, 20, "123")))
        self.assertFalse(self.engine.accept(Message(2, 20, "Привет", 999)))
        self.assertFalse(self.engine.accept(Message(3, -1, "Привет")))

    async def test_duplicate_ignored(self):
        self.assertTrue(self.engine.accept(Message(1, 10, "Привет")))
        self.assertFalse(self.engine.accept(Message(1, 10, "Привет")))

    async def test_overflow_fails_closed(self):
        self.engine.capacity = 1
        self.engine.accept(Message(1, 10, "А"))
        self.assertFalse(self.engine.accept(Message(2, 10, "Б")))
        with self.assertRaises(BackendError):
            await self.task

    async def test_stop_cancels_pending_generation(self):
        self.engine.accept(Message(1, 10, "Привет"))
        await eventually(lambda: self.model.active == 1)
        self.engine.stop()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        self.assertEqual(self.model.active, 0)
        self.assertEqual(self.output.sent, [])

    async def test_failed_send_not_retried(self):
        async def fail(text, reply_to=None):
            raise BackendError("Uncertain delivery")
        self.output.send = fail
        self.engine.accept(Message(1, 10, "Привет"))
        self.model.results.put_nowait("Ответ")
        with self.assertRaises(BackendError):
            await self.task
        self.assertEqual(len(self.model.calls), 1)

    async def test_direct_reply_uses_model_but_numeric_can_interrupt_it(self):
        self.engine._remember(self.engine.sent, 50)
        self.engine.accept(Message(1, 20, "Как дела?", 50))
        await eventually(lambda: self.model.active == 1)
        self.engine.accept(Message(2, 30, "123", 50))
        await eventually(lambda: len(self.output.sent) == 1)
        self.assertEqual(self.output.sent[0], ("123", 2))
        self.model.results.put_nowait("Все хорошо")
        await eventually(lambda: len(self.output.sent) == 3)
        self.assertEqual(self.output.sent[1:], [("Все", 1), ("хорошо", None)])
        self.assertEqual(self.model.max_active, 1)

    async def test_target_message_during_send_enters_next_batch(self):
        self.engine.accept(Message(1, 10, "Привет"))
        self.output.hook = lambda text: self.engine.accept(Message(2, 10, "Потом"))
        self.model.results.put_nowait("Ответ сейчас")
        await eventually(lambda: len(self.model.calls) == 2)
        self.assertEqual([s[0] for s in self.output.sent], ["Ответ", "сейчас"])
        self.assertTrue(any("Потом" in m["content"] for m in self.model.calls[1]))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_reply_and_no_markdown(self):
        from unittest.mock import AsyncMock
        client = AsyncMock()
        client.send_message.return_value.id = 42
        output = TelegramOutput(client, 10, 350, 0)
        self.assertEqual(await output.send("123", 9), 42)
        client.send_message.assert_awaited_once_with(10, "123", reply_to=9,
                                                    parse_mode=None, link_preview=False)

    async def test_telegram_failure_is_not_retried(self):
        from unittest.mock import AsyncMock
        client = AsyncMock()
        client.send_message.side_effect = TimeoutError
        with self.assertRaises(BackendError):
            await TelegramOutput(client, 10, 350, 0).send("А")
        self.assertEqual(client.send_message.await_count, 1)


class ConfigurationTests(unittest.TestCase):
    def test_exact_word_splitting(self):
        self.assertEqual(fragments("Привет как дела твои брат", 1),
                         ["Привет", "как", "дела", "твои", "брат"])
        self.assertEqual(fragments("а б в", 2), ["а б", "в"])

    def test_control_characters_and_empty(self):
        self.assertEqual(fragments("а\nб\tв", 1), ["а", "б", "в"])
        with self.assertRaises(BackendError):
            fragments("\n", 1)
        with self.assertRaises(BackendError):
            fragments("a" * 4097, 1)

    def test_350_wpm_not_clamped(self):
        self.assertEqual(Config(wpm=350).validate().wpm, 350)

    def test_invalid_config(self):
        for kwargs in [dict(wpm=True), dict(words=0), dict(wpm=601),
                       dict(request_timeout=float("nan")), dict(base_url="http://example.com"),
                       dict(base_url="https://secret@example.com"), dict(share_context="yes"),
                       dict(output="fallback"), dict(min_send_interval=0)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(BackendError):
                Config(**kwargs).validate()

    def test_local_http(self):
        Config(base_url="http://localhost:8000/v1").validate()

    def test_cli_init_check_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as root, patch("sys.stdout"), patch("sys.stderr"):
            self.assertEqual(main(["--data-dir", root, "init"]), 0)
            self.assertEqual(main(["--data-dir", root, "check"]), 0)
            self.assertEqual(main(["--data-dir", root, "init"]), 1)
            self.assertTrue((Path(root) / "backend.json").exists())

    @unittest.skipIf(__import__("os").name == "nt", "POSIX lock test")
    def test_single_instance(self):
        with (
            tempfile.TemporaryDirectory() as root,
            session_lock(Path(root)),
            self.assertRaises(BackendError),
            session_lock(Path(root)),
        ):
            pass


if __name__ == "__main__":
    unittest.main()
