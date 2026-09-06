"""Explicit output adapters. No automatic fallback or ambiguous-send retries."""
from __future__ import annotations

import asyncio
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor

from typerx.conversation import BackendError


class TelegramOutput:
    def __init__(self, client, peer, wpm: int, min_interval: float):
        self.client, self.peer = client, peer
        self.wpm, self.min_interval = wpm, min_interval
        self.next_send = 0.0

    async def send(self, text: str, reply_to: int | None = None) -> int:
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0.0, self.next_send - loop.time()))
        try:
            message = await self.client.send_message(
                self.peer, text, reply_to=reply_to, parse_mode=None, link_preview=False)
        except Exception as exc:
            # Includes FloodWait: stop, don't retry uncertain sends or bypass limits.
            raise BackendError(f"Telegram send failed ({type(exc).__name__}); no retry") from None
        self.next_send = loop.time() + max(self.min_interval, 60 * len(text) / (5 * self.wpm))
        return message.id

    async def close(self):
        pass


def _initialize_com():
    import pythoncom
    pythoncom.CoInitialize()


class DriverOutput:
    """Single COM/keyboard thread, exact fragment boundaries, outgoing confirmation.

    The desktop must expose a unique chat title and a UIA edit control. Generic
    titles are rejected. Matching outgoing text is a best-effort acknowledgement,
    not proof against concurrent manual use of the same Telegram account.
    """

    def __init__(self, client, peer, title: str, wpm: int):
        if os.name != "nt":
            raise BackendError("Driver output requires Windows")
        self.client, self.peer, self.title, self.wpm = client, peer, title, wpm
        self.stopped = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, initializer=_initialize_com)
        self.hwnd = None
        self.editor_id = None
        self.expected: str | None = None
        self.confirmation: asyncio.Future[int] | None = None

    def _field(self):
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uia_controls import EditWrapper
        element = IUIA().iuia.GetFocusedElement()
        if element.CurrentControlType != 50004:
            raise BackendError("Focus is not in the message editor")
        return tuple(element.GetRuntimeId() or ()), EditWrapper(UIAElementInfo(element)).get_value()

    def _check(self, empty: bool = False):
        import win32gui
        if self.stopped.is_set():
            raise BackendError("Driver stopped; inspect the draft before restarting")
        if (win32gui.GetForegroundWindow() != self.hwnd or
                win32gui.GetWindowText(self.hwnd) != self.window_title):
            raise BackendError("Window or chat changed; driver stopped")
        field_id, value = self._field()
        if field_id != self.editor_id or (empty and value.strip()):
            raise BackendError("Message editor changed or contains a draft")

    def _capture(self):
        import win32api
        import win32gui
        import win32process
        import re
        self.hwnd = win32gui.GetForegroundWindow()
        self.window_title = win32gui.GetWindowText(self.hwnd)
        title = re.sub(r"^\(\d+\)\s*", "", self.window_title).strip()
        allowed = {self.title, self.title + " – Telegram", self.title + " — Telegram",
                   self.title + " - Telegram", self.title + " - Telegram Desktop"}
        if title not in allowed:
            raise BackendError("Open the selected chat in a window exposing its unique title")
        _, pid = win32process.GetWindowThreadProcessId(self.hwnd)
        handle = win32api.OpenProcess(0x1000, False, pid)
        try:
            import ctypes
            from ctypes import wintypes
            query = ctypes.windll.kernel32.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                              ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query(int(handle), 0, buffer, ctypes.byref(size)):
                raise BackendError("Cannot verify Telegram process")
            if buffer.value.replace("\\", "/").rsplit("/", 1)[-1].lower() != "telegram.exe":
                raise BackendError("Foreground process is not Telegram Desktop")
        finally:
            win32api.CloseHandle(handle)
        self.editor_id, value = self._field()
        if not self.editor_id or value.strip():
            raise BackendError("Start with an empty Telegram message editor")
        self._check(empty=True)

    async def prepare(self):
        await asyncio.get_running_loop().run_in_executor(self.executor, self._capture)

    async def check(self):
        await asyncio.get_running_loop().run_in_executor(self.executor, self._check)

    def observe(self, message):
        if self.confirmation is None or self.confirmation.done():
            return
        if message.raw_text == self.expected and not message.reply_to_msg_id:
            self.confirmation.set_result(message.id)
        else:
            self.stopped.set()
            self.confirmation.set_exception(BackendError("Unexpected outgoing message during driver send"))

    def _write(self, text: str):
        from typerx.platform.interception_keyboard import InterceptionKeyboard
        self._check(empty=True)
        keyboard = InterceptionKeyboard(self.hwnd)
        try:
            # Validate the entire fragment before typing. No silent Unicode replacement.
            for char in set(text):
                self._check()
                keyboard._resolve_char(char)
            interval = 60 / (5 * self.wpm)
            for char in text:
                self._check()
                key = keyboard.press(char)
                try:
                    self.stopped.wait(min(0.018, interval / 2))
                finally:
                    keyboard.release(key)
                if self.stopped.wait(interval * random.uniform(0.85, 1.15) / 2):
                    raise BackendError("Driver stopped; inspect the draft")
            self._check()
            key = keyboard.press_enter()
            try:
                self.stopped.wait(0.018)
            finally:
                keyboard.release(key)
        finally:
            keyboard.close()

    async def send(self, text: str, reply_to: int | None = None) -> int:
        # reply_to intentionally ignored: driver reacts in the current chat.
        self.expected = text
        self.confirmation = asyncio.get_running_loop().create_future()
        worker = asyncio.get_running_loop().run_in_executor(self.executor, self._write, text)
        try:
            await asyncio.shield(worker)
            return await asyncio.wait_for(self.confirmation, timeout=10)
        except BaseException:
            self.stopped.set()
            await asyncio.gather(worker, return_exceptions=True)
            if self.confirmation.done() and not self.confirmation.cancelled():
                self.confirmation.exception()
            raise
        finally:
            if not self.confirmation.done():
                self.confirmation.cancel()
            self.confirmation = None
            self.expected = None

    async def close(self):
        self.stopped.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
