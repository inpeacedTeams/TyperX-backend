"""Pure incoming-message routing; no Telegram, network, or keyboard side effects.

Adapters must supply verified sender/chat IDs and IDs of messages actually sent
by this service. Do not treat every outgoing account message as service output.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    URGENT = 0
    DIRECT_REPLY = 1
    TARGET = 2


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    chat_id: int
    message_id: int
    sender_id: int
    text: str
    reply_to_message_id: int | None = None
    outgoing: bool = False
    sender_is_bot: bool = False


@dataclass(frozen=True, slots=True)
class RoutedMessage:
    message: IncomingMessage
    priority: Priority
    reply_to_message_id: int | None
    immediate_text: str | None = None


_CHALLENGE = re.compile(
    r"(?<!\w)(?:ты\s+с\s+софтом|нейронка|гейронка|автотайпер)(?!\w)",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"[0-9]{1,12}\Z")


class MessageRouter:
    """Bounded session-local routing and deduplication.

    Third parties qualify only by replying to a recorded service message in the
    selected chat. Keyword matches alone never expand the audience. Construct a
    new router when changing target/session. This is not a durable delivery queue.
    """

    def __init__(self, chat_id: int, target_sender_id: int, capacity: int = 2048):
        if not chat_id or target_sender_id <= 0 or capacity < 1:
            raise ValueError("A chat, positive target sender, and capacity are required")
        self.chat_id = chat_id
        self.target_sender_id = target_sender_id
        self.capacity = capacity
        self._sent: OrderedDict[int, None] = OrderedDict()
        self._seen: OrderedDict[int, None] = OrderedDict()

    def _remember(self, bucket: OrderedDict[int, None], message_id: int) -> None:
        bucket[message_id] = None
        bucket.move_to_end(message_id)
        while len(bucket) > self.capacity:
            bucket.popitem(last=False)

    def record_sent(self, chat_id: int, message_id: int) -> None:
        """Record only confirmed output; call once per actual sent fragment."""
        if chat_id != self.chat_id or message_id <= 0:
            raise ValueError("Sent message must belong to the selected chat")
        self._remember(self._sent, message_id)

    def route(self, message: IncomingMessage) -> RoutedMessage | None:
        if (
            message.chat_id != self.chat_id
            or message.message_id <= 0
            or message.sender_id <= 0
            or message.outgoing
            or message.sender_is_bot
            or not message.text.strip()
            or message.message_id in self._seen
        ):
            return None
        is_reply = message.reply_to_message_id in self._sent
        if message.sender_id != self.target_sender_id and not is_reply:
            return None
        self._remember(self._seen, message.message_id)
        text = message.text.strip()
        numeric = _DIGITS.fullmatch(text) is not None
        urgent = numeric or _CHALLENGE.search(text) is not None
        priority = (Priority.URGENT if urgent else
                    Priority.DIRECT_REPLY if is_reply else Priority.TARGET)
        return RoutedMessage(
            message=message,
            priority=priority,
            reply_to_message_id=message.message_id if is_reply else None,
            immediate_text=text if numeric else None,
        )
