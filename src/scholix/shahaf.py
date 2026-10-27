"""Shahaf (שחף) published timetables. See ``docs/shahaf.md``.

Shahaf publishes *presentation* rows (text, links, tabs), not structured lessons,
so this client returns the published view tree as-is.

Example::

    from scholix.shahaf import ShahafClient

    client = ShahafClient()
    client.login_moe_password("moe-user", "moe-pass")
    school = client.schools()[0]
    classes = client.classes(school["semel"], school["code"])
    view = client.app_data(school["semel"], school["code"], classes[0]["Id"])
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from ._http import HttpSession
from .errors import (
    AuthenticationError,
    ProtocolError,
    SessionExpired,
)
from .moe import MoeSignIn, SmsChallenge, new_moe_session

BASE = "https://view.shahaf.info/api/"
SIGN_IN_PAGE = "https://timetable.shahaf.info/ShfSignIn.aspx"
HOSTS = {"timetable.shahaf.info", "view.shahaf.info"}


def decode_sign_in_payload(s: str) -> dict[str, Any]:
    """Decode the ``s`` query value of ``SignInSuccess.aspx?s=...`` into the auth response.

    The alphabet is Base64 with ``.`` for ``=``, ``_`` for ``/`` and ``-`` for ``+``.
    Observed keys: ``Name``, ``Schools``, ``Student``, ``Token``, ``Tz``, ``VCode``.
    """
    standard = s.replace(".", "=").replace("_", "/").replace("-", "+")
    standard += "=" * (-len(standard) % 4)
    try:
        value = json.loads(base64.b64decode(standard).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("Shahaf returned an invalid sign-in payload") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Shahaf returned an invalid sign-in payload")
    return value


def extract_auth_response(chain: list[requests.Response]) -> dict[str, Any] | None:
    """The decoded auth response if any hop of the redirect chain is the success URL."""
    for response in chain:
        parts = urlsplit(response.url)
        if (
            parts.scheme == "https"
            and parts.hostname == "timetable.shahaf.info"
            and parts.path.lower().endswith("/signinsuccess.aspx")
        ):
            values = parse_qs(parts.query).get("s")
            if values:
                return decode_sign_in_payload(values[0])
    return None


class ShahafClient:
    def __init__(self, token: str | None = None, session: HttpSession | None = None) -> None:
        self.http = session or new_moe_session(HOSTS)
        self.token = token
        #: The full auth response from sign-in (``Name``, ``Student``, ``Schools``, ...).
        self.auth: dict[str, Any] = {}
        self.device_id = str(uuid.uuid4())

    # -- authentication -----------------------------------------------------

    def _entry_url(self) -> str:
        return f"{SIGN_IN_PAGE}?sid=shfapp&cid={self.device_id}"

    def _adopt(self, auth: dict[str, Any]) -> None:
        if not auth.get("Token"):
            raise AuthenticationError("Shahaf did not return a session token")
        self.auth = auth
        self.token = str(auth["Token"])

    def login_password(self, username: str, password: str) -> None:
        """Shahaf's own account login (``user/authstudent/``).

        Recovered from the APK; **not** verified against a live Shahaf account.
        The password travels in the query string, as the official app sends it.
        """
        body = self._get("user/authstudent/", {"usr": username, "pwd": password}, authenticated=False)
        if not isinstance(body, dict):
            raise ProtocolError("Shahaf sign-in returned an unexpected response")
        self._adopt(body)

    def login_moe_password(self, username: str, password: str) -> None:
        """Ministry of Education username + password."""
        flow = MoeSignIn(self.http, self._entry_url(), extract_auth_response)
        self._adopt(flow.login_password(username, password))

    def request_moe_sms(self, user_id: str, mobile: str) -> SmsChallenge:
        """Text a code to the mobile number registered with MOE. Pass the result to :meth:`submit_moe_sms`."""
        return MoeSignIn(self.http, self._entry_url(), extract_auth_response).request_sms(user_id, mobile)

    def submit_moe_sms(self, challenge: SmsChallenge, code: str) -> None:
        flow = MoeSignIn(self.http, self._entry_url(), extract_auth_response)
        self._adopt(flow.submit_sms(challenge, code))

    def login_with_payload(self, s: str) -> None:
        """Use the ``s`` value captured from ``SignInSuccess.aspx?s=...`` in a browser."""
        self._adopt(decode_sign_in_payload(s))

    # -- data ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None, *, authenticated: bool = True) -> Any:
        query = dict(params or {})
        if authenticated:
            if not self.token:
                raise SessionExpired("Sign in first")
            query["token"] = self.token
        response = self.http.get(BASE + path, params=query)
        try:
            body = response.json()
        except ValueError:
            raise ProtocolError(f"Shahaf returned an invalid response (HTTP {response.status_code})") from None
        if isinstance(body, dict) and body.get("Status") == "failed":
            error = str(body.get("Error") or "")
            if error in ("NO_TOKEN", "INVALID_TOKEN"):
                raise SessionExpired("Your Shahaf session expired. Sign in again.")
            if error in ("BAD_CREDENTIALS", "NO_USER_OR_PASSWORD"):
                raise AuthenticationError("Incorrect Shahaf username or password")
            raise ProtocolError(f"Shahaf rejected the request: {error}")
        if not response.ok:
            raise ProtocolError(f"Shahaf request failed (HTTP {response.status_code})")
        return body

    def schools(self) -> list[dict[str, Any]]:
        """Schools linked to the account: ``name``, ``semel``, ``code``, ``status``.

        An empty list is normal for an account whose school does not use Shahaf.
        """
        return list(self._get("school/student/"))

    def classes(self, semel: str | int, code: str | int) -> list[dict[str, Any]]:
        """Classes of a school: ``Id``, ``Name``, ``Grade``, ``Number``."""
        body = self._get(f"student/{semel}/{code}/classes/")
        return list(body["Classes"])

    def app_parameters(self, semel: str | int, code: str | int, class_id: str | int) -> Any:
        return self._get(f"view/{semel}/{code}/appprms/", {"clsid": class_id})

    def app_data(
        self,
        semel: str | int,
        code: str | int,
        class_id: str | int,
        view_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Published views for a class: ``{"version": ..., "views": [...]}``.

        ``view_parameters`` are the ``ViewParameter`` ids/options a view declares
        (e.g. a week or teacher picker), passed through as query parameters.
        """
        params = {**(view_parameters or {}), "clsid": class_id}
        return self._get(f"view/{semel}/{code}/appdata/", params)
