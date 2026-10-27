"""Webtop (Smart School) student portal. See ``docs/webtop.md``.

Example::

    from scholix.webtop import WebtopClient

    client = WebtopClient()
    client.login_password("username", "password")   # Webtop's own accounts
    # or: client.login_moe("moe-username", "moe-password")
    for grade in client.grades():
        print(grade.subject, grade.name, grade.value)
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ._http import HttpSession, json_body
from .errors import (
    AuthenticationError,
    ProtocolError,
    SessionExpired,
    VerificationRequired,
)
from .models import Grade, Lesson
from .moe import MoeSignIn, new_moe_session

SERVER = "https://webtopserver.smartschool.co.il/server/api/"
SITE = "https://webtop.smartschool.co.il"
MOE_ENTRY = "https://www.webtop.co.il/applications/loginMOENew/default.aspx"
HOSTS = {
    "www.webtop.co.il",
    "webtop.smartschool.co.il",
    "webtopserver.smartschool.co.il",
}

# Constants of the website's ``encryptStringToServer`` (main.878d6d935b432b30.js,
# captured 2026-10-17). They ship in the public JavaScript bundle.
_AES_KEY = b"01234567890000000150778345678901"
_AES_IV = b"6543210987654321"

_DEVICE_PASSWORD_LOGIN = json.dumps({"isMobile": True, "os": "Android", "browser": "Chrome", "cookies": True})
_DEVICE_MOE_LOGIN = json.dumps({"isMobile": False, "isTablet": False, "isDesktop": True})


def encrypt_login_field(value: str) -> str:
    """Reproduce the site's ``encryptStringToServer``: AES-256-CBC over the JSON-quoted string."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(json.dumps(value, ensure_ascii=False).encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode("ascii")


def study_year(today: _dt.date | None = None) -> int:
    """Webtop's ``studyYear``: the calendar year in which the school year ends (turns over in September)."""
    today = today or _dt.date.today()
    return today.year + (1 if today.month >= 9 else 0)


def current_semester(today: _dt.date | None = None) -> str:
    """``"a"`` from September through January, otherwise ``"b"``."""
    month = (today or _dt.date.today()).month
    return "a" if month >= 9 or month <= 1 else "b"


def extract_handoff_key(chain: list[requests.Response]) -> str | None:
    """The ``key`` query parameter of Webtop's ``/account/loginMoe`` landing URL, if present in the chain.

    ``parse_qs`` turns a literal ``+`` into a space and keeps ``%2B`` as ``+`` - the
    same as Webtop's Angular query parser, which is what the key is minted for.
    """
    for response in chain:
        parts = urlsplit(response.url)
        if parts.hostname == "webtop.smartschool.co.il" and parts.path.lower() == "/account/loginmoe":
            values = parse_qs(parts.query).get("key")
            if values and values[0]:
                return values[0]
    return None


def _parse_hour_time(hour_name: str) -> tuple[str, str]:
    times = re.findall(r"\d{1,2}:\d{2}", hour_name or "")
    if len(times) >= 2:
        first, second = sorted(times[:2], key=lambda t: tuple(int(p) for p in t.split(":")))
        return first, second
    return (times[0], "") if times else ("", "")


class WebtopClient:
    def __init__(self, session: HttpSession | None = None) -> None:
        self.http = session or new_moe_session(HOSTS)
        #: The login response's ``data`` object (user id, class, institution, names).
        self.profile: dict[str, Any] = {}
        self._settings: dict[int, dict[str, Any]] = {}

    # -- authentication -----------------------------------------------------

    def login_password(self, username: str, password: str) -> None:
        """Sign in with a Webtop username and password (not Ministry credentials)."""
        if not username or not password:
            raise AuthenticationError("Enter your Webtop username and password")
        payload = {
            "Data": encrypt_login_field(username + "0"),
            "UserName": username,
            "Password": password,
            "deviceDataJson": _DEVICE_PASSWORD_LOGIN,
        }
        response = self.http.post(
            SERVER + "user/LoginByUserNameAndPassword",
            json=payload,
            headers={"language": "he", "rememberMe": "0"},
        )
        if not response.ok:
            raise ProtocolError(f"Webtop sign-in is unavailable (HTTP {response.status_code})")
        body = json_body(response, "Webtop sign-in")
        if not isinstance(body, dict):
            raise ProtocolError("Webtop sign-in returned an unexpected response")
        data = body.get("data")
        if not body.get("status"):
            raise AuthenticationError("Webtop did not accept the sign-in")
        if body.get("message") == "get2FA" or (isinstance(data, dict) and data.get("mustChangePassword")):
            raise VerificationRequired("Webtop requires extra verification or a password change; use the website")
        if not isinstance(data, dict) or not str(data.get("userId") or ""):
            raise ProtocolError("Webtop sign-in response did not include a user id")
        self._adopt(data)

    def login_moe(self, username: str, password: str) -> None:
        """Sign in with Ministry of Education credentials (headless; no browser).

        Raises :class:`~scholix.errors.CaptchaRequired` or
        :class:`~scholix.errors.VerificationRequired` when MOE demands more.
        """
        flow = MoeSignIn(self.http, MOE_ENTRY, extract_handoff_key)
        key = flow.login_password(username, password)
        self.login_with_key(key)

    def login_with_key(self, key: str) -> None:
        """Exchange the hand-off ``key`` from ``/account/loginMoe?key=...`` for a Webtop session."""
        payload = {
            "rememberMe": "false",
            "key": key,
            "UniqueId": str(uuid.uuid4()),
            "deviceDataJson": _DEVICE_MOE_LOGIN,
        }
        response = self.http.post(
            SERVER + "user/LoginMoe",
            json=payload,
            headers={
                "Origin": SITE,
                "Referer": SITE + "/",
                "language": "he",
                "rememberMe": "0",
                "X-XSRF-TOKEN": "",
            },
        )
        body = json_body(response, "Webtop LoginMoe")
        data = body.get("data") if isinstance(body, dict) else None
        if not response.ok or not isinstance(body, dict) or not body.get("status") or not isinstance(data, dict):
            raise AuthenticationError("Webtop rejected the MOE sign-in")
        if not any(c.name == "webToken" and c.value for c in self.http.cookies):
            raise ProtocolError("Webtop did not issue a webToken cookie")
        self._adopt(data)

    def _adopt(self, data: dict[str, Any]) -> None:
        self.profile = data
        self._settings.clear()

    # -- profile accessors --------------------------------------------------

    @property
    def user_id(self) -> str:
        return str(self.profile.get("userId") or "")

    @property
    def institution_code(self) -> int | None:
        value = self.profile.get("institutionCode")
        return int(value) if str(value or "").isdigit() else None

    @property
    def class_code(self) -> int | None:
        value = self.profile.get("classCode")
        return int(value) if str(value or "").isdigit() else None

    @property
    def class_selector(self) -> str | None:
        """The ``"<classCode>|<classNumber>"`` value Webtop uses to address the class timetable."""
        code, number = self.profile.get("classCode"), self.profile.get("classNumber")
        return f"{code}|{number}" if str(code or "") and str(number or "") else None

    @property
    def name(self) -> str:
        return f"{self.profile.get('firstName', '')} {self.profile.get('lastName', '')}".strip()

    # -- raw access ---------------------------------------------------------

    def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        """POST JSON to ``<server>/api/<endpoint>`` and return the full response object.

        Raises :class:`SessionExpired` on HTTP 401/403 or an empty body.
        """
        response = self.http.post(
            SERVER + endpoint,
            json=payload or {},
            headers={"language": "he", "rememberMe": "0"},
        )
        if response.status_code in (401, 403) or (response.ok and not response.content):
            raise SessionExpired("Webtop session expired")
        if not response.ok:
            raise ProtocolError(f"Webtop {endpoint} failed (HTTP {response.status_code})")
        return json_body(response, f"Webtop {endpoint}")

    def data(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        """Like :meth:`post` but returns the ``data`` member and requires ``status: true``."""
        body = self.post(endpoint, payload)
        if not isinstance(body, dict) or not body.get("status"):
            raise ProtocolError(f"Webtop {endpoint} did not report success")
        return body.get("data")

    # -- pupil card ---------------------------------------------------------

    def pupil_card_settings(self, year: int | None = None) -> dict[str, Any]:
        """Division (semester) ids, the *encrypted* student id and the resolved study year."""
        year = year or study_year()
        if year in self._settings:
            return self._settings[year]
        data = self.data("PupilCard/GetSettingsList", {"id": year})
        divisions: dict[str, int] = {}
        for item in (data or {}).get("divisionsList") or []:
            name = str(item.get("division_name") or "")
            if item.get("year_division") == 1 or "א" in name:
                divisions["a"] = item["division_id"]
            elif item.get("year_division") == 2 or "ב" in name:
                divisions["b"] = item["division_id"]
        children = (data or {}).get("children") or []
        encrypted = str(children[0].get("id")) if children and children[0].get("id") else None
        resolved = (data or {}).get("currentStudyYear") or year
        result = {"divisions": divisions, "student_id": encrypted, "study_year": int(resolved), "raw": data}
        self._settings[year] = result
        return result

    def _period_and_student(self, year: int, semester: str) -> tuple[int, str]:
        settings = self.pupil_card_settings(year)
        if semester == "ab":
            period = 0
        else:
            if semester not in ("a", "b"):
                raise ValueError("semester must be 'a', 'b' or 'ab'")
            try:
                period = settings["divisions"][semester]
            except KeyError:
                raise ProtocolError(f"Webtop lists no semester '{semester}' for study year {year}") from None
        student = settings["student_id"] or self.user_id
        if not student:
            raise SessionExpired("Sign in first")
        return period, student

    def grades(self, year: int | None = None, semester: str | None = None) -> list[Grade]:
        year = year or study_year()
        period, student = self._period_and_student(year, (semester or current_semester()).lower())
        data = self.data(
            "PupilCard/GetPupilGrades",
            {"studyYear": year, "moduleID": 6, "periodID": period, "studentID": student},
        )
        return [
            Grade(
                subject=item.get("subject") or "",
                name=item.get("title") or "",
                value=item.get("grade"),
                date=item.get("date") or "",
                raw=item,
            )
            for item in data or []
            if isinstance(item, dict)
        ]

    def discipline_events(self, year: int | None = None, semester: str = "ab") -> list[dict[str, Any]]:
        """Attendance and discipline events (raw dicts; ``eventType`` groups them)."""
        year = year or study_year()
        period, student = self._period_and_student(year, semester.lower())
        data = self.data(
            "PupilCard/GetPupilDiciplineEvents",
            {"studentID": student, "moduleID": 4, "periodID": period, "studyYear": year},
        )
        data = data or {}
        return list(data.get("diciplineEvents") or data.get("disciplineEvents") or [])

    def pupil_schedule(self, week_index: int = 0, year: int | None = None) -> dict[str, Any]:
        """The student's own weekly schedule (resolves parallel groups). Returns the raw response."""
        year = year or study_year()
        settings = self.pupil_card_settings(year)
        if self.class_code is None:
            raise ProtocolError("The profile has no class code")
        return self.post(
            "PupilCard/GetPupilScheduale",
            {
                "weekIndex": week_index,
                "viewType": 0,
                "studyYear": settings["study_year"],
                "studentID": settings["student_id"] or self.user_id,
                "classCode": self.class_code,
                "moduleID": 10,
            },
        )

    # -- class timetable (Shotef) -------------------------------------------

    def class_schedule(self, institution: int | None = None, selected_value: str | None = None) -> list[dict[str, Any]]:
        """Raw per-day records of the class timetable (``ShotefSchedualeData``)."""
        institution = institution or self.institution_code
        selected = selected_value or self.class_selector
        if institution is None or not selected:
            raise ProtocolError("The profile has no institution or class")
        body = self.post(
            "shotef/ShotefSchedualeData",
            {"institutionCode": institution, "selectedValue": selected, "typeView": 1},
        )
        return list(body.get("data") or [])

    def timetable(self, institution: int | None = None, selected_value: str | None = None) -> list[Lesson]:
        """The *original* class timetable as :class:`Lesson` objects.

        Changes, cancellations and additions are on the raw ``changes`` array of each
        hour (see ``class_schedule``); this method does not apply them.
        """
        lessons: list[Lesson] = []
        for day in self.class_schedule(institution, selected_value):
            index = day.get("dayIndex", day.get("day", day.get("dayId")))
            if not isinstance(index, int):
                continue
            for hour in day.get("hoursData") or []:
                start, end = _parse_hour_time(hour.get("hourName", ""))
                for item in hour.get("scheduale") or hour.get("schedule") or []:
                    teacher = f"{item.get('teacherPrivateName', '')} {item.get('teacherLastName', '')}".strip()
                    room = item.get("room")
                    lessons.append(
                        Lesson(
                            day=index - 1,  # Webtop counts Sunday as 1
                            period=item.get("hour", hour.get("hour")),
                            subject=(item.get("subject") or "").strip(),
                            teacher=teacher,
                            room="" if room in (None, "null") else str(room),
                            start=start,
                            end=end,
                            raw=item,
                        )
                    )
        return lessons

    def events_tests_board(
        self,
        selected: _dt.date | None = None,
        view: int = 3,
        load_filters: bool = True,
        year: int | None = None,
    ) -> Any:
        """The Events & Tests board of the timetable page (raw response)."""
        selected = selected or _dt.date.today()
        if self.institution_code is None:
            raise ProtocolError("The profile has no institution code")
        year = year or study_year(selected)
        settings = self.pupil_card_settings(year)
        instant = _dt.datetime.combine(selected, _dt.time()).astimezone().astimezone(_dt.timezone.utc)
        return self.post(
            "shotef/EventsTestsBoard",
            {
                "institutionCode": self.institution_code,
                "selectedDate": instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "selectedView": view,
                "loadFilters": load_filters,
                "studyYear": settings["study_year"],
            },
        )

    # -- messages -----------------------------------------------------------

    def inbox(self, page: int = 1, query: str = "", has_read: bool | None = None) -> list[dict[str, Any]]:
        """One page of the inbox (raw dicts: ``messageId``, ``subject``, ``sendingDate``, ...).

        ``has_read`` is sent as the request's ``HasRead`` filter. ``None`` (the only
        value Scholix has been observed to send) means "no filter"; the meaning of
        ``True``/``False`` has not been verified against a live account.
        """
        data = self.data(
            "messageBox/GetMessagesInbox",
            {"PageId": page, "LabelId": 0, "HasRead": has_read, "SearchQuery": query},
        )
        return list(data or [])

    def message(self, message_id: str) -> dict[str, Any]:
        """Full inbox message: ``messageData`` holds ``messageContent`` (HTML) and ``filesList``."""
        data = self.data(
            "messageBox/GetMessagesInboxData",
            {"MessageId": message_id, "FilterId": 0, "IsInbox": True, "hasRead": None},
        )
        return dict(data or {})
