"""Exception hierarchy shared by every platform client."""

from __future__ import annotations


class ScholixError(Exception):
    """Base class for everything this library raises on purpose."""


class NetworkError(ScholixError):
    """The portal could not be reached or returned a transport-level failure."""


class AuthenticationError(ScholixError):
    """The portal rejected the credentials or the one-time code."""


class SessionExpired(AuthenticationError):
    """A previously valid session was rejected. Sign in again."""


class CaptchaRequired(AuthenticationError):
    """The portal demands a CAPTCHA. This library never solves or bypasses one.

    Complete the sign-in in a browser and pass the resulting session in.
    """


class VerificationRequired(AuthenticationError):
    """The portal requires a second factor the called method cannot supply."""


class RateLimited(ScholixError):
    """The portal refused further requests for now. Do not retry in a loop."""


class ProtocolError(ScholixError):
    """The response did not have the shape this library documents.

    Usually the portal changed. A sanitized description is attached as
    ``details`` to make an issue report useful; it never holds credentials.
    """

    def __init__(self, message: str, details: object = None) -> None:
        super().__init__(message)
        self.details = details
