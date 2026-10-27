"""Mashov (משוב) student and parent web API. See ``docs/mashov.md``.

Example::

    from scholix.mashov import MashovClient, list_schools

    schools = list_schools()                     # [{"semel": "...", "name": "..."}]
    client = MashovClient(semel="123456", year=2027, username="student")
    client.login("password")
    for grade in client.grades():
        print(grade.subject, grade.value)
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from dataclasses import dataclass
from typing import Any

from ._http import HttpSession, json_body
from .errors import (
    AuthenticationError,
    CaptchaRequired,
    ProtocolError,
    RateLimited,
    SessionExpired,
)
from .models import Grade, Lesson

API = "https://web.mashov.info/api"
WEB_APP = "https://web.mashov.info/students/"

#: Values the official web client sends. The server does not appear to check
#: them strictly, but they are part of the documented login body.
APP_NAME = "info.mashov.students"
API_VERSION = "3.20210425"

_HEBREW_GRADES = ["א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט", "י", "יא", "יב"]


def current_school_year(today: _dt.date | None = None) -> int:
    """Mashov's ``year`` is the calendar year in which the school year *ends*."""
    today = today or _dt.date.today()
    return today.year + (1 if today.month >= 7 else 0)


def list_schools(session: HttpSession | None = None) -> list[dict[str, str]]:
    """Public list of Mashov schools (no authentication)."""
    http = session or HttpSession()
    response = http.get(f"{API}/schools", headers=_headers())
    if not response.ok:
        raise ProtocolError(f"Mashov school list failed (HTTP {response.status_code})")
    body = json_body(response, "Mashov school list")
    return [
        {"semel": str(item["semel"]), "name": str(item["name"])}
        for item in body
        if isinstance(item, dict) and item.get("semel") and item.get("name")
    ]


def _headers(csrf: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json, text/plain, */*", "Referer": WEB_APP}
    if csrf:
        headers["X-Csrf-Token"] = csrf
    if extra:
        headers.update(extra)
    return headers


def normalize_grade_code(class_code: str) -> str:
    """Hebrew class letters to a number: ``"יא"`` -> ``"11"``; anything else is returned stripped."""
    cleaned = re.sub(r"[׳״\"']", "", class_code.strip())
    if cleaned in _HEBREW_GRADES:
        return str(_HEBREW_GRADES.index(cleaned) + 1)
    return cleaned


@dataclass
class Student:
    guid: str
    name: str
    grade: str
    class_number: str


@dataclass
class Bell:
    lesson: int
    start: str
    end: str


def _clock(raw: Any) -> str:
    match = re.search(r"\d{1,2}:\d{2}", str(raw or "").strip())
    return match.group(0) if match else ""


class MashovClient:
    """One Mashov session for one account at one school."""

    def __init__(
        self,
        semel: str | int,
        username: str,
        year: int | None = None,
        *,
        session: HttpSession | None = None,
        device_uuid: str | None = None,
    ) -> None:
        self.semel = str(semel)
        self.username = username
        self.year = int(year) if year else current_school_year()
        self.http = session or HttpSession()
        self.device_uuid = device_uuid or f"scholix-{uuid.uuid4()}"
        self.csrf_token: str | None = None
        #: Long-lived "private device" token. Store it to sign in again without
        #: a password or SMS; Mashov rotates it on every login.
        self.device_pass: str | None = None
        self.students: list[Student] = []
        self.student: Student | None = None
        self.display_name = ""

    # -- authentication -----------------------------------------------------

    def _device_fields(self) -> dict[str, Any]:
        return {
            "appName": APP_NAME,
            "apiVersion": API_VERSION,
            "appVersion": API_VERSION,
            "appBuild": API_VERSION,
            "deviceUuid": self.device_uuid,
            "devicePlatform": "android",
            "deviceManufacturer": "generic",
            "deviceModel": "generic",
            "deviceVersion": "14",
        }

    def login(self, password: str, *, private_device: bool = True) -> None:
        """Sign in with the account password (or an SMS code, see :meth:`login_with_otp`)."""
        if not password:
            raise AuthenticationError("A password or one-time code is required")
        payload = {
            "semel": self.semel,
            "year": str(self.year),
            "username": self.username,
            "password": password,
            "isPrivateDevice": private_device,
            "IsBiometric": False,
            **self._device_fields(),
        }
        response = self.http.post(f"{API}/login", json=payload, headers=_headers())
        if not response.ok:
            raise AuthenticationError(f"Mashov rejected the sign-in (HTTP {response.status_code})")
        self._adopt(response)

    def request_otp(self, phone: str, captcha: str | None = None) -> None:
        """Ask Mashov to text a one-time code to the registered mobile number.

        ``phone`` must be the ten-digit Israeli mobile number on the account.
        Raises :class:`RateLimited` (HTTP 429) or :class:`CaptchaRequired`.
        """
        digits = re.sub(r"\D", "", phone)
        if len(digits) != 10 or not digits.startswith("05"):
            raise AuthenticationError("Enter the ten-digit mobile number registered with Mashov")
        payload: dict[str, Any] = {
            "semel": int(self.semel) if self.semel.isdigit() else self.semel,
            "username": self.username,
            "cellphone": digits,
        }
        if captcha:
            payload["captcha"] = captcha
        response = self.http.post(f"{API}/user/otp/request", json=payload, headers=_headers())
        if response.ok:
            return
        if response.status_code == 429:
            raise RateLimited("Mashov is limiting SMS requests for this account")
        if response.status_code == 403 and "captcha" in response.text.lower():
            raise CaptchaRequired("Mashov requires a CAPTCHA before sending an SMS")
        raise AuthenticationError(f"Mashov SMS request failed (HTTP {response.status_code})")

    def login_with_otp(self, code: str) -> None:
        """Verify an SMS code. Mashov takes it through the normal login endpoint."""
        digits = re.sub(r"\D", "", code)
        if not 4 <= len(digits) <= 8:
            raise AuthenticationError("The one-time code must be 4-8 digits")
        self.login(digits)

    def login_with_device_pass(self, device_pass: str) -> None:
        """Re-establish a session from a stored ``device_pass``, with no password or SMS."""
        headers = _headers(extra={"devicePass": device_pass})
        response = self.http.post(f"{API}/loginDevice", json=self._device_fields(), headers=headers)
        if not response.ok:
            raise SessionExpired(f"Mashov device sign-in failed (HTTP {response.status_code})")
        self._adopt(response)

    def _adopt(self, response: Any) -> None:
        body = json_body(response, "Mashov login")
        token = body.get("accessToken") if isinstance(body, dict) else None
        children = token.get("children") if isinstance(token, dict) else None
        if not children:
            raise ProtocolError("Mashov login response has no accessToken.children")
        self.students = [
            Student(
                guid=str(child["childGuid"]),
                name=str(child.get("privateName") or token.get("displayName") or self.username),
                grade=normalize_grade_code(str(child.get("classCode") or "")),
                class_number=str(child.get("classNum") or ""),
            )
            for child in children
            if child.get("childGuid")
        ]
        if not self.students:
            raise ProtocolError("Mashov login response has no childGuid")
        if self.student is None or self.student.guid not in {s.guid for s in self.students}:
            self.student = self.students[0]
        self.display_name = self.student.name
        self.csrf_token = response.headers.get("X-Csrf-Token") or self.csrf_token
        self.device_pass = response.headers.get("devicePass") or self.device_pass

    def select_student(self, guid: str) -> None:
        """Parent accounts list several children; pick whose data later calls return."""
        match = next((s for s in self.students if s.guid == guid), None)
        if match is None:
            raise ValueError("Unknown student GUID for this account")
        self.student = match

    # -- data ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.csrf_token is None:
            raise SessionExpired("Sign in first")
        response = self.http.get(f"{API}/{path}", params=params, headers=_headers(self.csrf_token))
        if response.status_code in (401, 403):
            raise SessionExpired("Mashov session expired")
        if not response.ok:
            raise ProtocolError(f"Mashov {path.split('?')[0]} failed (HTTP {response.status_code})")
        return json_body(response, f"Mashov {path}")

    def _student_path(self, suffix: str) -> str:
        if self.student is None:
            raise SessionExpired("Sign in first")
        return f"students/{self.student.guid}/{suffix}"

    def grades(self) -> list[Grade]:
        """All published grades for the selected student."""
        result = []
        for item in self._get(self._student_path("grades")):
            numeric = item.get("grade")
            value = numeric if numeric is not None else (item.get("textualGrade") or item.get("rangeGrade") or None)
            result.append(
                Grade(
                    subject=item.get("subjectName") or item.get("groupName") or "",
                    name=item.get("gradingEvent") or item.get("gradeType") or "",
                    value=value,
                    date=item.get("eventDate") or "",
                    type=item.get("gradeType") or "",
                    teacher=item.get("teacherName") or "",
                    raw=item,
                )
            )
        return result

    def bells(self) -> list[Bell]:
        """The school's bell schedule (``/api/bells``). Empty when the school publishes none."""
        bells = []
        for item in self._get("bells"):
            number = item.get("lessonNumber")
            start = _clock(item.get("startTime"))
            if number is None or not start:
                continue
            bells.append(Bell(int(number), start, _clock(item.get("endTime"))))
        return bells

    def timetable(self) -> list[Lesson]:
        """The weekly timetable. Times come from :meth:`bells`; entries only carry the period number."""
        times = {bell.lesson: bell for bell in self.bells()}
        lessons = []
        for entry in self._get(self._student_path("timetable"), {"year": self.year}):
            slot = entry.get("timeTable") or {}
            group = entry.get("groupDetails") or {}
            day, period = slot.get("day"), slot.get("lesson")
            if not isinstance(day, int) or not isinstance(period, int) or period <= 0:
                continue
            teachers = group.get("groupTeachers")
            if isinstance(teachers, list):
                names = [t.get("teacherName", "") if isinstance(t, dict) else str(t) for t in teachers]
                teacher = ", ".join(dict.fromkeys(n for n in names if n))
            else:
                teacher = group.get("teacherName") or ""
            bell = times.get(period)
            lessons.append(
                Lesson(
                    day=day - 1,  # Mashov counts Sunday as 1
                    period=period,
                    subject=group.get("subjectName") or group.get("groupName") or "",
                    teacher=teacher,
                    room=slot.get("roomNum") or "",
                    start=bell.start if bell else "",
                    end=bell.end if bell else "",
                    raw=entry,
                )
            )
        return lessons

    def behavior(self, start: _dt.date, end: _dt.date) -> list[dict[str, Any]]:
        """Attendance and behavior events between two dates (inclusive), merged from both endpoints.

        Returns the portal's raw event dicts; each gets ``_source`` set to
        ``"behave"`` (in-lesson) or ``"outBehave"`` (outside lessons).
        """
        params = {"start": start.isoformat(), "end": end.isoformat(), "year": self.year}
        events: list[dict[str, Any]] = []
        for source in ("behave", "outBehave"):
            for item in self._get(self._student_path(source), params):
                events.append({**item, "_source": source})
        return events
