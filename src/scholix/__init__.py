"""Documented protocols and Python clients for Israeli school and university portals.

Import the client for the platform you need::

    from scholix.mashov import MashovClient
    from scholix.webtop import WebtopClient
    from scholix.schooly import SchoolyClient

The protocol write-ups live in the repository's ``docs/`` directory.
"""

from .errors import (  # noqa: F401
    AuthenticationError,
    CaptchaRequired,
    NetworkError,
    ProtocolError,
    RateLimited,
    ScholixError,
    SessionExpired,
    VerificationRequired,
)
from .models import Grade, Lesson  # noqa: F401

__version__ = "0.1.0"
