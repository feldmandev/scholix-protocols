"""A small ``requests`` wrapper: default timeout, host allow-list, cookie export."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit

import requests

from .errors import NetworkError, ProtocolError

DEFAULT_TIMEOUT = 30.0

#: Desktop Chrome string. The Ministry of Education identity provider treats a
#: missing or non-browser request profile as a bot signal.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


class HttpSession(requests.Session):
    """``requests.Session`` with a default timeout and an optional host allow-list.

    The allow-list is checked on every response, including each redirect hop,
    so a redirect can never carry credentials or cookies to a host the flow
    does not expect.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        allowed_hosts: Iterable[str] | None = None,
        user_agent: str | None = None,
        verify: bool = True,
    ) -> None:
        super().__init__()
        self.timeout = timeout
        self.verify = verify
        self.allowed_hosts = frozenset(allowed_hosts) if allowed_hosts else None
        if user_agent:
            self.headers["User-Agent"] = user_agent
        self.hooks["response"].append(self._check_host)

    def _check_host(self, response: requests.Response, *_: Any, **__: Any) -> requests.Response:
        if self.allowed_hosts is None:
            return response
        parts = urlsplit(response.url)
        if parts.scheme != "https" or parts.hostname not in self.allowed_hosts:
            raise ProtocolError(f"Unexpected address in sign-in flow: {parts.scheme}://{parts.hostname}")
        return response

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        kwargs.setdefault("timeout", self.timeout)
        try:
            return super().request(method, url, **kwargs)
        except requests.RequestException as exc:  # includes timeouts and TLS errors
            raise NetworkError(f"{method} {urlsplit(url).netloc}: {exc.__class__.__name__}") from exc

    # -- cookie persistence -------------------------------------------------

    def export_cookies(self) -> list[dict[str, Any]]:
        """Return cookies as plain dicts with domain and path preserved."""
        return [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
            }
            for c in self.cookies
        ]

    def import_cookies(self, cookies: Iterable[dict[str, Any]]) -> None:
        for c in cookies:
            self.cookies.set(
                c["name"],
                c["value"],
                domain=c.get("domain") or "",
                path=c.get("path") or "/",
                secure=bool(c.get("secure", True)),
                expires=c.get("expires"),
            )


def json_body(response: requests.Response, what: str) -> Any:
    """Parse a JSON response or raise :class:`ProtocolError` without leaking the body."""
    try:
        return response.json()
    except ValueError as exc:
        raise ProtocolError(
            f"{what} returned a non-JSON response (HTTP {response.status_code})"
        ) from exc
