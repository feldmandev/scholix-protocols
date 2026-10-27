"""Ministry of Education (``lgn.edu.gov.il``) sign-in, driven over plain HTTP.

Webtop and Shahaf both federate to this identity provider. The flow is
documented in ``docs/moe-sso.md``; this module is the shared implementation.

The caller supplies the *relying party* side: an entry URL and ``is_done``, a
function that recognizes the final hand-off in the redirect chain and extracts
whatever the relying party issued (a Webtop key, a Shahaf payload, ...).

No CAPTCHA is ever solved. If MOE demands one, :class:`CaptchaRequired` is
raised so the caller can fall back to a real browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

from ._http import BROWSER_USER_AGENT, HttpSession, json_body
from .errors import (
    AuthenticationError,
    CaptchaRequired,
    ProtocolError,
    RateLimited,
    VerificationRequired,
)

MOE_HOST = "lgn.edu.gov.il"
SMS_CONTRACT = "EduCombinedAuthSms"

_AJAX_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}
_NAV_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
_MAX_HOPS = 8


@dataclass
class Page:
    url: str
    body: str
    #: Every response in the redirect chain, oldest first (final response last).
    chain: list[requests.Response]


@dataclass
class CredentialPage:
    page_url: str
    server_url: str
    target: str
    login_type: str


@dataclass
class SmsChallenge:
    """Pending SMS sign-in between :meth:`MoeSignIn.request_sms` and :meth:`MoeSignIn.submit_sms`."""

    credential: CredentialPage
    masked_phone: str


def js_literal(html: str, name: str) -> str | None:
    """Value of a script assignment such as ``_serverUrl = '...'`` (first match)."""
    match = re.search(rf"{re.escape(name)}\s*=\s*(['\"])(.*?)\1", html)
    return match.group(2) if match else None


class MoeSignIn:
    """One sign-in attempt. Create a fresh instance (and session) per attempt."""

    def __init__(
        self,
        session: HttpSession,
        entry_url: str,
        is_done: Callable[[list[requests.Response]], Any],
    ) -> None:
        self.http = session
        self.entry_url = entry_url
        self.is_done = is_done
        if MOE_HOST not in (session.allowed_hosts or ()):
            raise ValueError("The session must allow-list lgn.edu.gov.il")

    # -- public flows -------------------------------------------------------

    def login_password(self, username: str, password: str) -> Any:
        """MOE username + password. Returns whatever ``is_done`` extracted."""
        if not username or not password:
            raise AuthenticationError("Enter your Ministry of Education username and password")
        credential = self._reach_credential_page()
        if "SMSOTP" in credential.login_type.upper():
            raise VerificationRequired("MOE requires SMS verification for this account")
        # The MOE UI always starts the sequence first. The reply to this call is
        # an error on a fresh session; the next call succeeds regardless.
        self._post_ajax(credential, {"initiateLoginSequence": "true"})
        result = self._post_ajax(
            credential,
            {"HIN_USERID": username, "Ecom_Password": password, "g-recaptcha-response": " "},
        )
        self._check_result(result, rejection="MOE did not accept the username and password")
        return self._complete(credential)

    def request_sms(self, user_id: str, mobile: str) -> SmsChallenge:
        """Send a code to the mobile number registered for ``user_id`` (Israeli ID)."""
        digits = re.sub(r"\D", "", mobile)
        if not user_id or len(digits) != 10 or not digits.startswith("05"):
            raise AuthenticationError("Enter your ID number and registered ten-digit mobile number")
        credential = self._open_sms_contract(self._reach_credential_page())
        self._post_ajax(credential, {"initiateLoginSequence": "true"})
        result = self._post_ajax(
            credential,
            {"HIN_ID": user_id, "HIN_MOBILE": digits, "g-recaptcha-response": " "},
        )
        self._check_result(result, rejection="MOE did not accept the ID and mobile number")
        return SmsChallenge(credential, str(result.get("otpDestMasked") or ""))

    def submit_sms(self, challenge: SmsChallenge, code: str) -> Any:
        """Verify the code and finish the hand-off. Returns what ``is_done`` extracted."""
        if not code.strip():
            raise AuthenticationError("Enter the SMS code sent to your mobile number")
        result = self._post_ajax(
            challenge.credential,
            {"Ecom_Token": code.strip(), "g-recaptcha-response": " "},
        )
        if result.get("isCaptchaNeeded"):
            raise CaptchaRequired("MOE requires CAPTCHA verification; complete sign-in in a browser")
        if result.get("isError", True):
            error = str(result.get("errorCode") or "")
            if error in ("TOKEN_TOKEN_EXPIRED", "TOKEN_NOT_FOUND"):
                raise AuthenticationError("The SMS code expired. Request a new code.")
            if error == "INTRUDER_DETECTED":
                raise RateLimited("MOE locked the sign-in after too many attempts. Try again later.")
            raise AuthenticationError(f"MOE did not accept the SMS code{f' ({error})' if error else ''}")
        return self._complete(challenge.credential)

    # -- steps --------------------------------------------------------------

    def _check_result(self, result: dict[str, Any], *, rejection: str) -> None:
        if result.get("isCaptchaNeeded"):
            raise CaptchaRequired("MOE requires CAPTCHA verification; complete sign-in in a browser")
        if result.get("isPasswordExpiring"):
            raise VerificationRequired("Update your password on the MOE website before signing in")
        if result.get("isError", True):
            error = str(result.get("errorCode") or "")
            raise AuthenticationError(f"{rejection}{f' ({error})' if error else ''}")

    def _complete(self, credential: CredentialPage) -> Any:
        page = self._follow_handoff(
            self._post_form(credential.server_url, credential.page_url, {"option": "credential", "target": credential.target})
        )
        result = self.is_done(page.chain)
        if result is None:
            raise ProtocolError("MOE did not complete the relying-party sign-in")
        return result

    def _reach_credential_page(self) -> CredentialPage:
        page = self._get(self.entry_url)
        for _ in range(6):
            parsed = self._parse_credential_page(page)
            if parsed:
                return parsed
            page = self._interstitial(page)
        raise ProtocolError("MOE sign-in exceeded its redirect limit")

    def _open_sms_contract(self, credential: CredentialPage) -> CredentialPage:
        """Same page with ``id=EduCombinedAuthSms`` - exactly what the SPA's SMS tab does."""
        if re.search(r"[?&]id=[^&]*", credential.page_url):
            sms_url = re.sub(r"([?&])id=[^&]*", rf"\1id={SMS_CONTRACT}", credential.page_url, count=1)
        else:
            sep = "&" if "?" in credential.page_url else "?"
            sms_url = f"{credential.page_url}{sep}id={SMS_CONTRACT}"
        page = self._get(sms_url, referer=credential.page_url)
        parsed = self._parse_credential_page(page)
        if not parsed:
            raise ProtocolError("MOE did not return its SMS sign-in page")
        login_type = parsed.login_type.lower()
        if "sms" not in login_type and "otp" not in login_type:
            raise VerificationRequired("MOE did not offer SMS sign-in for this account")
        return parsed

    def _parse_credential_page(self, page: Page) -> CredentialPage | None:
        server = js_literal(page.body, "_serverUrl")
        if not server:
            return None
        server_url = urljoin(page.url, server)
        parts = urlsplit(server_url)
        if parts.scheme != "https" or parts.hostname != MOE_HOST:
            raise ProtocolError("MOE returned an unexpected login address")
        csrt = re.search(r"csrt=([0-9]+)", page.body)
        if csrt and "csrt=" not in parts.query:
            query = parse_qsl(parts.query, keep_blank_values=True) + [("csrt", csrt.group(1))]
            server_url = urlunsplit(parts._replace(query=urlencode(query)))
        return CredentialPage(
            page_url=page.url,
            server_url=server_url,
            target=js_literal(page.body, "_target") or "",
            login_type=js_literal(page.body, "_loginType") or "",
        )

    def _follow_handoff(self, page: Page) -> Page:
        for _ in range(_MAX_HOPS):
            if self.is_done(page.chain) is not None:
                return page
            page = self._interstitial(page)
        raise ProtocolError("MOE sign-in exceeded its redirect limit")

    def _interstitial(self, page: Page) -> Page:
        """One F5 / WS-Federation hop: a JS ``top.location.href`` or an auto-submitting form."""
        redirect = js_literal(page.body, "top.location.href")
        if redirect:
            return self._get(urljoin(page.url, redirect), referer=page.url)
        form = BeautifulSoup(page.body, "html.parser").find("form")
        if form is None or ".submit(" not in page.body:
            raise ProtocolError("MOE sign-in exceeded its redirect limit")
        action = urljoin(page.url, form.get("action", ""))
        # BeautifulSoup decodes attribute entities exactly once. That is required:
        # the signed assertion must reach the relying party un-escaped.
        fields = {i["name"]: i.get("value", "") for i in form.find_all("input", attrs={"name": True})}
        return self._post_form(action, page.url, fields)

    # -- HTTP ---------------------------------------------------------------

    def _wrap(self, response: requests.Response) -> Page:
        return Page(response.url, response.text, [*response.history, response])

    def _get(self, url: str, *, referer: str | None = None) -> Page:
        headers = {"Accept": _NAV_ACCEPT, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"}
        if referer:
            headers["Referer"] = referer
        response = self.http.get(url, headers=headers)
        return self._finish(response)

    def _post_form(self, url: str, referer: str, fields: dict[str, str]) -> Page:
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(referer))
        headers = {
            "Referer": referer,
            "Origin": origin,
            "Accept": _NAV_ACCEPT,
            "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        }
        return self._finish(self.http.post(url, data=fields, headers=headers))

    def _post_ajax(self, credential: CredentialPage, fields: dict[str, str]) -> dict[str, Any]:
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(credential.page_url))
        headers = {
            **_AJAX_HEADERS,
            "Referer": credential.page_url,
            "Origin": origin,
            "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        }
        response = self.http.post(credential.server_url, data={"option": "credential", "isAjax": "true", **fields}, headers=headers)
        if not response.ok:
            raise ProtocolError(f"MOE sign-in failed (HTTP {response.status_code})")
        body = json_body(response, "MOE credential step")
        if not isinstance(body, dict):
            raise ProtocolError("The login service returned an unexpected response")
        return body

    def _finish(self, response: requests.Response) -> Page:
        page = self._wrap(response)
        # A relying-party payload can sit on an intermediate redirect, so a
        # non-success status after we already saw it is not an error.
        if self.is_done(page.chain) is not None:
            return page
        if not response.ok:
            raise ProtocolError(f"Sign-in failed (HTTP {response.status_code})")
        return page


def new_moe_session(extra_hosts: set[str], *, timeout: float = 30.0) -> HttpSession:
    """A session pinned to the MOE host plus the relying party's hosts, with a browser UA."""
    return HttpSession(
        timeout=timeout,
        allowed_hosts={MOE_HOST, *extra_hosts},
        user_agent=BROWSER_USER_AGENT,
    )
