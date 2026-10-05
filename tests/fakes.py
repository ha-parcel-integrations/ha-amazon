"""A scripted stand-in for ``aiohttp.ClientSession`` shared by the HTTP tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import aiohttp


class FakeResponse:
    """Just enough of ``aiohttp.ClientResponse`` for the clients."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://www.amazon.nl/",
    ) -> None:
        """Hold a status, a JSON-or-text body and the final URL."""
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = SimpleNamespace(path=urlparse(url).path)

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._body, ValueError):
            raise self._body
        return self._body

    async def text(self) -> str:
        return str(self._body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Answers requests from a script: ``(method, url fragment) -> responses``."""

    def __init__(self) -> None:
        """Start with no script and no recorded calls."""
        self.script: list[tuple[str, str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.cookie_jar = MagicMock()

    def add(self, method: str, fragment: str, *responses: Any) -> None:
        """Queue responses (or exceptions) for matching requests, in order.

        The most recently added matching script wins, so a test can override
        a default set up by a helper.
        """
        self.script.insert(0, (method, fragment, list(responses)))

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        for scripted_method, fragment, queue in self.script:
            if scripted_method == method and fragment in url and queue:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, BaseException):
                    raise item
                return item
        raise AssertionError(f"unscripted request: {method} {url}")

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("GET", url, kwargs)


def connector_error() -> aiohttp.ClientConnectorError:
    """A connection failure, as raised for an unresolvable host."""
    return aiohttp.ClientConnectorError(MagicMock(), OSError("no route"))
