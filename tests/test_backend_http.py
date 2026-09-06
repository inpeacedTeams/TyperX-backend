import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from typerx.conversation import BackendError
from typerx.headless import Config, HTTPModel


class Response:
    def __init__(self, status=200, data=None, delay=0):
        self.status_code = status
        self.data = data or b'{"choices":[{"message":{"content":"Hello"}}]}'
        self.delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aiter_bytes(self):
        await asyncio.sleep(self.delay)
        yield self.data


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def stream(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return self.response

    async def aclose(self):
        self.closed = True


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    def model(self, response, timeout=30):
        client = FakeHTTP(response)
        with patch.dict(sys.modules, {"httpx": types.SimpleNamespace(AsyncClient=lambda **kw: client)}):
            model = HTTPModel(Config(request_timeout=timeout))
        return model, client

    async def test_success_and_close(self):
        model, client = self.model(Response())
        self.assertEqual(await model.complete([]), "Hello")
        await model.close()
        self.assertTrue(client.closed)

    async def test_redirect_and_rate_limit_no_retry(self):
        for status in (302, 401, 429, 503):
            model, client = self.model(Response(status=status))
            with self.assertRaises(BackendError):
                await model.complete([])
            self.assertEqual(len(client.requests), 1)

    async def test_size_limit(self):
        model, _ = self.model(Response(data=b"x" * 1_000_001))
        with self.assertRaises(BackendError):
            await model.complete([])

    async def test_bad_content(self):
        for data in (b"not json", b"{}", json.dumps({"choices": [{"message": {"content": " "}}]}).encode()):
            model, _ = self.model(Response(data=data))
            with self.assertRaises(BackendError):
                await model.complete([])

    async def test_deadline(self):
        model, _ = self.model(Response(delay=1), timeout=0.01)
        with self.assertRaises(BackendError):
            await model.complete([])

    async def test_cancellation_propagates(self):
        model, _ = self.model(Response(delay=10))
        task = asyncio.create_task(model.complete([]))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
