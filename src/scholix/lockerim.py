"""Lockerim (לוקרים) school locker service: cloud API and PEM frame codec. See ``docs/lockerim.md``.

The cloud API needs a reCAPTCHA token for every SMS request and verification.
This library cannot produce one; obtain it from a browser or WebView using the
public site key documented in ``docs/lockerim.md`` and pass it in.

The Bluetooth transport is deliberately *not* implemented here. :func:`command`,
:func:`enrollment_frame`, :func:`decrypt_frame` and the reply helpers build and
parse the frames so any BLE library can carry them.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ._http import HttpSession
from .errors import AuthenticationError, ProtocolError, SessionExpired

BASE = "https://rbapi.lockerim.co.il"

# -- Bluetooth (PEM lock) constants -------------------------------------------

SERVICE_UUID = "6e406060-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

_BASE_KEY = bytes.fromhex("ABC532744BBF456532833D368682D560")

_PEM_ERRORS = {
    "<PIS>": "Your locker rental has not started yet.",
    "<PES>": "Your locker rental has ended.",
    "<PLF>": "Push the lock into its latch, then try locking again.",
    "<PCB>": "The locker battery needs replacing.",
    "<PIU>": "Your permission to use this locker is unavailable or has been used up.",
    "<PIP>": "Your permission to use this locker is unavailable or has been used up.",
}
_PEM_REJECTED = {"<PIL>", "<PUF>", "<PUA>", "<PDF>", "<ECR>"}


def advertised_name(mac: str) -> str:
    """Locks advertise ``PEM`` followed by the MAC address without colons."""
    return "PEM" + mac.replace(":", "").upper()


def _lock_key(skda: str) -> bytes:
    """AES-128 key: the fixed base key with the 3 ``skda`` bytes overlaid at ``(last byte % 12)``."""
    if not re.fullmatch(r"[0-9a-fA-F]{6}", skda):
        raise ValueError("skda must be 6 hex digits (from the subscription)")
    key = bytearray(_BASE_KEY)
    offset = int(skda[-2:], 16) % 12
    key[offset : offset + 3] = bytes.fromhex(skda)
    return bytes(key)


def _ecb(skda: str, data: bytes, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(_lock_key(skda)), modes.ECB())
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def command(skda: str, user_ec_id: int, counter: int, unlock: bool) -> bytes:
    """The ``[<16 encrypted bytes>]`` frame that opens (``unlock=True``) or locks.

    ``counter`` (1..65535) must increase on every command for this user and lock.
    A ``<PIC..>`` reply carries the value the lock expects next (see :func:`next_counter`).
    """
    if not 0 <= user_ec_id <= 0xFFFFFFFF or not 1 <= counter <= 0xFFFF:
        raise ValueError("Invalid user id or command counter")
    message = f"{counter:04x}{user_ec_id:08x}{'01' if unlock else '00'}{skda.lower()}000000000000"
    return b"[" + _ecb(skda, bytes.fromhex(message), True) + b"]"


def decrypt_frame(frame: bytes, skda: str) -> str:
    """Hex of the decrypted body of a ``[...]`` / ``^...`` / ``%...`` frame (first and last byte are framing)."""
    return _ecb(skda, frame[1:-1], False).hex()


def enrollment_frame(ec: str) -> bytes:
    """First-contact frame ``<ec>`` sent with the account's ``ec`` value before the lock is recognized."""
    return b"<" + bytes.fromhex(ec) + b">"


def pem_error(response: bytes) -> str | None:
    """Human-readable meaning of an error reply, or ``None`` if it is not one."""
    text = response.decode("ascii", errors="replace")
    if text.startswith("<PIC"):
        return "Locker counter synchronized. Please try again."
    if text in _PEM_ERRORS:
        return _PEM_ERRORS[text]
    if text in _PEM_REJECTED:
        return f"The locker rejected the command ({text}). Try again."
    return None


def next_counter(response: bytes) -> int | None:
    """From a ``<PIC..>`` reply: the counter to use next (big-endian bytes 4-5, plus one)."""
    if response.startswith(b"<PIC") and len(response) >= 6:
        return ((response[4] << 8) | response[5]) + 1
    return None


def succeeded(response: bytes, unlock: bool) -> bool:
    """``<PUS>`` confirms an unlock, ``<PLS>`` a lock."""
    return response == (b"<PUS>" if unlock else b"<PLS>")


# -- cloud API -----------------------------------------------------------------


class LockerimError(ProtocolError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class LockerimClient:
    def __init__(self, session: HttpSession | None = None, base_url: str = BASE) -> None:
        self.http = session or HttpSession(timeout=60)
        self.base_url = base_url.rstrip("/")
        self.token = ""
        self.refresh_token = ""

    def _request(self, path: str, body: dict[str, Any] | None = None, *, auth: bool = True) -> Any:
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.http.post(url, json=body, headers=headers) if body is not None else self.http.get(url, headers=headers)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        errors = payload.get("errors") if isinstance(payload, dict) else None
        failed = (
            not response.ok
            or (isinstance(payload, dict) and payload.get("isSucceeded") is False)
            or bool(errors)
        )
        if failed:
            message = (errors[0].get("message") if errors and isinstance(errors[0], dict) else "") or (
                "Your Lockerim session expired. Reconnect to continue."
                if response.status_code == 401
                else f"Lockerim request failed ({response.status_code})."
            )
            raise LockerimError(response.status_code, message)
        content = payload.get("content") if isinstance(payload, dict) else None
        if content is None:
            raise ProtocolError("Lockerim returned an empty response")
        return content

    @staticmethod
    def _challenge(content: Any) -> str:
        # JSON null and the literal string "null" are both invalid challenges;
        # accepting them would show a code-entry screen for an SMS never sent.
        token = content.get("otpToken") if isinstance(content, dict) else None
        if not isinstance(token, str) or not token.strip() or token.strip().lower() == "null":
            raise ProtocolError("Lockerim did not return a valid SMS verification request")
        return token

    def send_code(self, phone: str, recaptcha: str) -> str:
        """Request an SMS for a registered mobile number. Returns the ``otpToken`` challenge."""
        return self._challenge(self._request("Account/Login", {"phoneNumber": phone, "RecaptchaKey": recaptcha}, auth=False))

    def resend_code(self, otp_token: str, recaptcha: str) -> str:
        return self._challenge(self._request("Account/ResendLoginOtp", {"otpToken": otp_token, "RecaptchaKey": recaptcha}, auth=False))

    def verify_code(self, otp_token: str, code: str, recaptcha: str) -> None:
        session = self._request(
            "Account/ValidateLoginOtp", {"otpToken": otp_token, "otp": code, "RecaptchaKey": recaptcha}, auth=False
        )
        self._accept(session)

    def _accept(self, session: Any) -> None:
        if not isinstance(session, dict) or not session.get("token") or not session.get("refreshToken"):
            raise AuthenticationError("Lockerim did not return a session")
        self.token, self.refresh_token = session["token"], session["refreshToken"]

    def refresh(self) -> None:
        """Rotate the token pair. The refresh token is single-use: store the new one."""
        if not self.refresh_token:
            raise SessionExpired("No refresh token; sign in again")
        self._accept(self._request("Account/RefreshToken", {"refreshToken": self.refresh_token}, auth=False))

    def subscriptions(self) -> list[dict[str, Any]]:
        """School PEM-locker subscriptions (each has ``skda``, ``ec``, ``subscriptionId``, ``cellNumber``, ...).

        A 401 triggers one token refresh and a retry.
        """
        try:
            content = self._request("Subscription/GetMySubscriptions")
        except LockerimError as error:
            if error.status != 401:
                raise
            self.refresh()
            content = self._request("Subscription/GetMySubscriptions")
        return list(content.get("subscriptions") or [])

    def user_ec_id(self) -> int:
        """The ``UserECId`` claim of the access token - the user id embedded in lock commands."""
        try:
            segment = self.token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
            return int(claims["UserECId"])
        except (IndexError, ValueError, KeyError, TypeError):
            raise ProtocolError("The access token has no UserECId claim") from None

    def report_recognition(self, report: dict[str, Any]) -> Any:
        """``Subscription/ECRecognition`` - sent once after a lock's first-contact handshake."""
        return self._request("Subscription/ECRecognition", report)
