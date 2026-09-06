"""Single-writer conversation scheduler, independent of Telegram and Windows."""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Protocol


class BackendError(RuntimeError):
    """Safe diagnostic: never include credentials or message text."""


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    sender: int
    text: str
    reply_to: int | None = None


class Model(Protocol):
    async def complete(self, history: list[dict[str, str]]) -> str: ...


class Output(Protocol):
    async def send(self, text: str, reply_to: int | None = None) -> int: ...


CHALLENGE = re.compile(r"(?<!\w)(ты\s+с\s+софтом|нейронка|гейронка|автотайпер)(?!\w)", re.I)
NUMBER = re.compile(r"[0-9]{1,12}\Z")


def clean_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text).strip()


def fragments(text: str, words: int) -> list[str]:
    if not 1 <= words <= 16:
        raise ValueError("words must be between 1 and 16")
    tokens = clean_text(text).split()
    result = [" ".join(tokens[i:i + words]) for i in range(0, len(tokens), words)]
    if not result or any(len(part) > 4096 for part in result):
        raise BackendError("Empty response or fragment exceeds Telegram size limit")
    return result


class Conversation:
    """One normal generation; later incoming messages form the next batch.

    Reactions interrupt only between confirmed output fragments. A numeric echo
    does not require the LLM. Claims about automation receive an honest fixed
    response. Ordinary direct replies use the LLM after an in-flight generation.
    State is session-local; uncertain sends stop the service, never auto-retry.
    """

    def __init__(self, target: int, model: Model, output: Output, *, words: int = 1,
                 capacity: int = 256, reaction_cooldown: float = 2.0):
        if target <= 0 or not 1 <= words <= 16 or capacity < 1 or reaction_cooldown < 0:
            raise ValueError("Invalid conversation configuration")
        self.target, self.model, self.output, self.words = target, model, output, words
        self.capacity, self.reaction_cooldown = capacity, reaction_cooldown
        self.normal: deque[Message] = deque()
        self.reactions: deque[Message] = deque()
        self.history: deque[dict[str, str]] = deque(maxlen=100)
        self.sent: OrderedDict[int, None] = OrderedDict()
        self.seen: OrderedDict[int, None] = OrderedDict()
        self.wake = asyncio.Event()
        self.closed = False
        self.failure: BackendError | None = None
        self.last_reaction = float("-inf")
        self.generation: asyncio.Task[str] | None = None

    @staticmethod
    def _remember(bucket: OrderedDict[int, None], key: int) -> None:
        bucket[key] = None
        while len(bucket) > 4096:
            bucket.popitem(last=False)

    def accept(self, message: Message) -> bool:
        # Called on the same asyncio loop as run(). Adapter validates chat and sender.
        if (self.closed or message.id <= 0 or message.sender <= 0 or
                not message.text.strip() or message.id in self.seen):
            return False
        direct = message.reply_to is not None and message.reply_to in self.sent
        if message.sender != self.target and not direct:
            return False
        if len(message.text) > 6000:
            self.failure = BackendError("Incoming message exceeds context limit")
            self.stop()
            return False
        urgent = NUMBER.fullmatch(message.text.strip()) or CHALLENGE.search(message.text)
        queue = self.reactions if direct or urgent else self.normal
        if len(self.normal) + len(self.reactions) >= self.capacity:
            self.failure = BackendError("Input queue is full; stopped without dropping silently")
            self.stop()
            return False
        self._remember(self.seen, message.id)
        queue.append(message)
        self.wake.set()
        return True

    def stop(self) -> None:
        self.closed = True
        self.wake.set()

    def _check(self) -> None:
        if self.failure:
            raise self.failure
        if self.closed:
            raise asyncio.CancelledError

    @staticmethod
    def _user(message: Message) -> dict[str, str]:
        return {"role": "user", "content": f"[sender:{message.sender}] {message.text}"}

    @staticmethod
    def _immediate(message: Message) -> str | None:
        text = message.text.strip()
        if NUMBER.fullmatch(text):
            return text
        if CHALLENGE.search(text):
            return "Да, это автоматизированный ответ."
        return None

    async def _send(self, text: str, reply_to: int | None = None) -> None:
        self._check()
        message_id = await self.output.send(text, reply_to)
        # Only the adapter's confirmed sends qualify as service output.
        self._remember(self.sent, message_id)
        self.history.append({"role": "assistant", "content": text})

    async def _react(self, immediate_only: bool = False) -> bool:
        self._check()
        now = asyncio.get_running_loop().time()
        if not self.reactions or now - self.last_reaction < self.reaction_cooldown:
            return False
        candidates = [m for m in self.reactions if self._immediate(m) is not None]
        message = candidates[0] if candidates else self.reactions[0]
        text = self._immediate(message)
        if immediate_only and text is None:
            return False
        self.reactions.remove(message)
        self.history.append(self._user(message))
        if text is None:
            text = await self._generate(list(self.history))
        self._check()
        # The first reaction fragment replies; all subsequent fragments are plain.
        reply_to = message.id if message.reply_to in self.sent else None
        for i, part in enumerate(fragments(text, self.words)):
            await self._send(part, reply_to if i == 0 else None)
        self.last_reaction = asyncio.get_running_loop().time()
        return True

    async def _generate(self, snapshot: list[dict[str, str]]) -> str:
        self.generation = asyncio.create_task(self.model.complete(snapshot))
        try:
            while not self.generation.done():
                self._check()
                await self._react(immediate_only=True)
                # Polling also makes cooldown expiry observable without new input.
                await asyncio.wait({self.generation}, timeout=0.05)
            return self.generation.result()
        finally:
            if not self.generation.done():
                self.generation.cancel()
            await asyncio.gather(self.generation, return_exceptions=True)
            self.generation = None

    async def run(self) -> None:
        try:
            while True:
                self._check()
                self.wake.clear()
                await self._react()
                self._check()
                if self.normal:
                    batch = list(self.normal)
                    self.normal.clear()
                    self.history.extend(self._user(m) for m in batch)
                    # Include every accepted message in this batch even if > history maxlen.
                    snapshot = list(self.history)[:-min(len(batch), len(self.history))]
                    snapshot.extend(self._user(m) for m in batch)
                    text = await self._generate(snapshot)
                    for part in fragments(text, self.words):
                        await self._react()
                        await self._send(part)
                elif self.reactions:
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=0.05)
                    except TimeoutError:
                        pass
                else:
                    await self.wake.wait()
        finally:
            self.closed = True
            if self.generation:
                self.generation.cancel()
                await asyncio.gather(self.generation, return_exceptions=True)
