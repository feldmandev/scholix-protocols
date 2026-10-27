"""Offline test harness: a fake transport that records requests and replays canned responses."""

from __future__ import annotations

import json as _json
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

Handler = Callable[[requests.PreparedRequest], "Reply"]


class Reply:
    def __init__(
        self,
        body: Any = "",
        status: int = 200,
        headers: dict[str, str] | None = None,
        cookies: list[str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.cookies = cookies or []


class FakeAdapter(BaseAdapter):
    """Matches ``(METHOD, path)`` (host ignored unless a full URL is registered)."""

    def __init__(self) -> None:
        super().__init__()
        self.routes: list[tuple[str, str, Handler | Reply]] = []
        self.requests: list[requests.PreparedRequest] = []

    def add(self, method: str, target: str, reply: Handler | Reply) -> None:
        self.routes.append((method.upper(), target, reply))

    def send(self, request, **kwargs):  # type: ignore[override]
        self.requests.append(request)
        parts = urlsplit(request.url)
        key_path = parts.path + (f"?{parts.query}" if parts.query else "")
        candidates = []
        for method, target, reply in self.routes:
            if method != request.method:
                continue
            full = target.startswith("http")
            base_match = (full and request.url.split("?")[0] == target.split("?")[0]) or (
                not full and (parts.path == target or key_path == target)
            )
            if not base_match:
                continue
            exact = (request.url == target) if full else (key_path == target)
            candidates.append((exact, reply))
        if candidates:
            # A route whose query matches exactly beats a path-only route.
            _, reply = max(candidates, key=lambda c: c[0])
            result = reply(request) if callable(reply) else reply
            return self._build(request, result)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    @staticmethod
    def _build(request, reply: Reply) -> requests.Response:
        response = requests.Response()
        response.status_code = reply.status
        response.url = request.url
        response.request = request
        body = reply.body
        if isinstance(body, (dict, list)):
            body = _json.dumps(body, ensure_ascii=False)
            response.headers["Content-Type"] = "application/json"
        response._content = body.encode("utf-8") if isinstance(body, str) else body
        response.encoding = "utf-8"
        response.headers = CaseInsensitiveDict({**response.headers, **reply.headers})
        response.raw = _RawCookies(reply.cookies)
        return response

    def close(self) -> None:  # pragma: no cover
        pass


class _RawCookies:
    """Enough of ``urllib3.HTTPResponse`` for ``extract_cookies_to_jar``."""

    def __init__(self, set_cookie: list[str]) -> None:
        self._set_cookie = set_cookie

    @property
    def _original_response(self):
        return None

    def get_all(self, name: str, default=None):  # pragma: no cover
        return self._set_cookie if name.lower() == "set-cookie" else default

    # requests calls these while following redirects.
    def release_conn(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self, *args, **kwargs) -> bytes:
        return b""


def body_of(request: requests.PreparedRequest) -> Any:
    if request.body is None:
        return None
    raw = request.body.decode() if isinstance(request.body, bytes) else request.body
    try:
        return _json.loads(raw)
    except ValueError:
        from urllib.parse import parse_qsl

        return dict(parse_qsl(raw, keep_blank_values=True))


@pytest.fixture
def fake():
    return FakeAdapter()


@pytest.fixture
def mount(fake):
    def _mount(session):
        session.mount("https://", fake)
        return session

    return _mount
