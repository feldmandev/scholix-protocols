"""Small normalized records. Every one keeps the portal's untouched payload in ``raw``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Grade:
    subject: str
    name: str
    value: str | float | None
    date: str = ""
    type: str = ""
    teacher: str = ""
    weight: float | None = None
    max_value: float | None = None
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Lesson:
    #: 0 = Sunday ... 6 = Saturday (the Israeli school week).
    day: int
    subject: str
    #: School period number when the portal has one, otherwise ``None``.
    period: int | None = None
    start: str = ""  # "HH:MM"
    end: str = ""
    teacher: str = ""
    room: str = ""
    note: str = ""
    cancelled: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
