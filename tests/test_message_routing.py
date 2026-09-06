import unittest

from typerx.message_routing import IncomingMessage, MessageRouter, Priority


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.router = MessageRouter(-100, 10)
        self.router.record_sent(-100, 50)

    def message(self, **kwargs):
        return IncomingMessage(**dict(dict(chat_id=-100, message_id=60,
                                           sender_id=10, text="Привет"), **kwargs))

    def test_target(self):
        result = self.router.route(self.message())
        self.assertEqual(result.priority, Priority.TARGET)
        self.assertIsNone(result.reply_to_message_id)

    def test_third_party_numeric_reply(self):
        result = self.router.route(self.message(sender_id=20, text="123",
                                                 reply_to_message_id=50))
        self.assertEqual(result.priority, Priority.URGENT)
        self.assertEqual(result.reply_to_message_id, 60)
        self.assertEqual(result.immediate_text, "123")

    def test_unrelated_messages_ignored(self):
        for kwargs in [dict(sender_id=20, text="123"),
                       dict(sender_id=20, reply_to_message_id=49),
                       dict(chat_id=-200, reply_to_message_id=50),
                       dict(outgoing=True), dict(sender_is_bot=True),
                       dict(sender_id=-20), dict(text="  ")]:
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.router.route(self.message(**kwargs)))

    def test_duplicate(self):
        self.assertIsNotNone(self.router.route(self.message()))
        self.assertIsNone(self.router.route(self.message()))

    def test_keywords(self):
        for i, text in enumerate(["Ты с софтом?", "НЕЙРОНКА!", "гейронка", "автотайпер"]):
            with self.subTest(text=text):
                result = self.router.route(self.message(message_id=60+i, text=text))
                self.assertEqual(result.priority, Priority.URGENT)
                self.assertIsNone(result.immediate_text)

    def test_no_substring_match(self):
        result = self.router.route(self.message(text="автотайперный"))
        self.assertEqual(result.priority, Priority.TARGET)

    def test_normal_reply(self):
        result = self.router.route(self.message(sender_id=20, reply_to_message_id=50))
        self.assertEqual(result.priority, Priority.DIRECT_REPLY)

    def test_bounded_tracking(self):
        router = MessageRouter(-100, 10, capacity=1)
        router.record_sent(-100, 49)
        router.record_sent(-100, 50)
        self.assertIsNone(router.route(self.message(sender_id=20, reply_to_message_id=49)))
        self.assertIsNotNone(router.route(self.message(sender_id=20, reply_to_message_id=50)))

    def test_sent_validation(self):
        with self.assertRaises(ValueError):
            self.router.record_sent(-200, 50)
        with self.assertRaises(ValueError):
            self.router.record_sent(-100, 0)

    def test_numeric_is_exact_and_bounded(self):
        for i, text in enumerate(["123?", "число 123", "1" * 13]):
            result = self.router.route(self.message(message_id=60+i, text=text))
            self.assertIsNone(result.immediate_text)


if __name__ == "__main__":
    unittest.main()
