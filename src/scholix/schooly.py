"""Schooly (סקולי) - national-ID sign-in with an OTP, grades and weekly timetable.

See ``docs/schooly.md``. Example::

    from scholix.schooly import SchoolyClient

    client = SchoolyClient()
    contacts = client.identify("012345678")        # masked phone/e-mail choices
    client.send_code(contacts[0])
    schools = client.verify_code("123456")         # [] when signed in, else choose a school
    if schools:
        client.verify_code("123456", school_id=schools[0]["_id"])  # same code, now scoped
    print(client.grades())
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ._http import HttpSession
from .errors import AuthenticationError, ProtocolError, SessionExpired
from .models import Grade, Lesson

BASE = "https://api.schooly.co.il/"


def _data(obj: Any) -> dict[str, Any]:
    """Schooly wraps most payloads as ``{"success": true, "data": {...}}``; some are bare."""
    if isinstance(obj, dict):
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj
    return {}


def _objects(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _present(value: Any) -> bool:
    return value is not None and value is not False and str(value).strip() != ""


class SchoolyClient:
    def __init__(self, session: HttpSession | None = None) -> None:
        self.http = session or HttpSession()
        self.token = ""
        self.identity = ""
        self.profile: dict[str, Any] = {}
        self.students: list[dict[str, Any]] = []
        self.student_id = ""
        self._periods: list[dict[str, Any]] | None = None

    # -- transport ----------------------------------------------------------

    def request(self, path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Origin": "https://app.schooly.co.il",
            "X-Client-Platform": "android",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = BASE + path.lstrip("/")
        response = (
            self.http.post(url, params=params, json=body, headers=headers)
            if body is not None
            else self.http.get(url, params=params, headers=headers)
        )
        if response.status_code == 401 and not path.lstrip("/").startswith("api/auth/"):
            raise SessionExpired("Schooly session expired. Sign in again.")
        try:
            result = response.json()
        except ValueError:
            raise ProtocolError(f"Schooly returned an unreadable response (HTTP {response.status_code})") from None
        if not isinstance(result, dict):
            raise ProtocolError("Schooly returned an unexpected response shape")
        if not response.ok or result.get("success") is False:
            message = result.get("message") or result.get("error") or f"Schooly request failed (HTTP {response.status_code})"
            raise AuthenticationError(str(message)) if path.startswith("api/auth/") else ProtocolError(str(message))
        return result

    # -- authentication -----------------------------------------------------

    def identify(self, national_id: str) -> list[dict[str, Any]]:
        """Return the registered contact methods: dicts with ``type`` (``phone``/``email``) and ``masked``."""
        national_id = national_id.strip()
        if not re.fullmatch(r"\d{5,9}", national_id):
            raise AuthenticationError("Enter your national ID number")
        self.identity = national_id
        response = self.request("api/auth/identify", body={"userId": national_id})
        options = _objects(response.get("contactOptions") or _data(response).get("contactOptions"))
        contacts = [o for o in options if o.get("type") in ("email", "phone")]
        if not contacts:
            raise AuthenticationError("Schooly has no supported contact method for this ID")
        return contacts

    def send_code(self, contact: dict[str, Any]) -> None:
        """Send the one-time code to one of the contacts returned by :meth:`identify`."""
        self.request(
            "api/auth/send-otp",
            body={
                "userId": self.identity,
                "contactType": contact["type"],
                "contactValue": contact.get("masked") or contact.get("value") or "",
            },
        )

    def verify_code(self, code: str, school_id: str | None = None) -> list[dict[str, Any]]:
        """Verify the six-digit code.

        Returns ``[]`` once signed in. If the account belongs to several schools
        and no ``school_id`` was given, returns the schools to choose from and
        the client is *not* yet signed in; call again with ``school_id``.
        """
        if not re.fullmatch(r"\d{6}", code):
            raise AuthenticationError("Enter the six-digit verification code")
        body: dict[str, Any] = {"userId": self.identity, "otpCode": code, "otp": code}
        if school_id:
            body["school_id"] = school_id
        response = self.request("api/auth/verify-otp", body=body)
        nested = response.get("data")
        envelope = nested if isinstance(nested, dict) and "data" in nested and ("token" in nested or "signedToken" in nested) else response
        self.token = str(envelope.get("signedToken") or envelope.get("token") or "")
        if not self.token:
            raise ProtocolError("Schooly did not return a session token")
        schools = _objects(envelope.get("schoolEntities") or response.get("schoolEntities"))
        if len(schools) > 1 and not school_id:
            return schools
        if school_id:
            self.request("api/schools/switch-school", body={"school_id": school_id})
        self._load_profile()
        return []

    def _load_profile(self) -> None:
        profile = _data(self.request("api/users/me"))
        if not str(profile.get("student_login_id") or ""):
            raise ProtocolError("Schooly did not return an account profile")
        self.profile = profile
        user = profile.get("current_user") or profile
        children = user.get("childrenPupils") or user.get("children")
        self.students = list(children) if isinstance(children, list) and children else [profile]
        if self.student_id not in {s.get("student_login_id") for s in self.students}:
            self.student_id = str(self.students[0].get("student_login_id"))

    def select_student(self, student_login_id: str) -> None:
        if student_login_id not in {s.get("student_login_id") for s in self.students}:
            raise ValueError("Choose a student from this account")
        self.student_id = student_login_id

    def export_session(self) -> dict[str, Any]:
        return {"token": self.token, "cookies": self.http.export_cookies(), "identity": self.identity}

    def restore_session(self, session: dict[str, Any]) -> None:
        """Restore a stored session and refresh the profile; raises :class:`SessionExpired` if rejected."""
        self.token = session.get("token", "")
        self.identity = session.get("identity", "")
        self.http.import_cookies(session.get("cookies", []))
        self._load_profile()

    # -- data ---------------------------------------------------------------

    def periods(self) -> list[dict[str, Any]]:
        if self._periods is None:
            self._periods = _objects(_data(self.request("v1/schools/periods")).get("periods"))
        return self._periods

    def grades(self, year: int | None = None) -> list[Grade]:
        """Published grades of the selected student for an academic year.

        Duplicate assessment ids across periods are dropped. Deleted, draft and
        unpublished assessments, and other students' rows, are excluded.
        """
        if not self.student_id:
            raise SessionExpired("Sign in first")
        today = _dt.date.today()
        year = year or today.year + (1 if today.month >= 9 else 0)
        period_ids = [
            str(p.get("_id") or p.get("id"))
            for p in self.periods()
            if p.get("academicYear") == year and (p.get("_id") or p.get("id"))
        ] or [""]
        seen: set[str] = set()
        grades: list[Grade] = []
        for period in period_ids:
            params = {"student_login_id": self.student_id, "year": year}
            if period:
                params["period"] = period
            card = _data(self.request("v1/academic/pupil-card", params))
            for grade in pupil_card_grades(card, self.student_id):
                key = str(grade.raw.get("_id") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                grades.append(grade)
        return grades

    def timetable(self, week_start: _dt.date | None = None, *, original: bool = False) -> list[Lesson]:
        """Lessons of the week beginning on ``week_start`` (a Sunday; default: this week).

        By default published changes are applied (moved lessons dropped,
        cancellations flagged). ``original=True`` returns the base timetable.
        """
        if not self.student_id:
            raise SessionExpired("Sign in first")
        if week_start is None:
            today = _dt.date.today()
            week_start = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
        response = self.request(
            "v1/schedule/weeklyTimeTable",
            {
                "ttDate": week_start.strftime("%Y%m%d"),
                "pupilId": self.student_id,
                "includeOverlay": "true",
                "overlayEventsMode": "published",
            },
        )
        first = (_objects(response.get("data")) or [_data(response)])[0]
        scope = first.get("scope") or {}
        scope_id = str(scope.get("_id") or first.get("scope_id") or "")
        hours = (
            _objects(_data(self.request("v1/schedule/lesson-hours", {"scope_id": scope_id})).get("lessonHours"))
            if scope_id
            else []
        )
        return weekly_timetable(response, hours, original=original)


def pupil_card_grades(card: dict[str, Any], student_id: str) -> list[Grade]:
    result = []
    for detail in _objects(card.get("assessmentDetails")):
        event = detail.get("event")
        if not isinstance(event, dict):
            continue
        owner = detail.get("studentId", detail.get("student_login_id"))
        if owner is not None and str(owner) != student_id:
            continue
        if event.get("_deleted") or event.get("draft") or not event.get("published", True):
            continue
        value = detail.get("value")
        text = str(detail.get("text") or detail.get("freeText") or "")
        if value is None and not text:
            continue
        raw_date = str(event.get("dt") or "")
        date = f"{raw_date[6:8]}/{raw_date[4:6]}/{raw_date[:4]}" if re.fullmatch(r"\d{8}", raw_date) else raw_date[:10]
        result.append(
            Grade(
                subject=event.get("subjectName") or event.get("name") or "",
                name=event.get("name") or "",
                value=value if value is not None else text,
                date=date,
                type=event.get("typeName") or "Grade",
                weight=event.get("weight"),
                max_value=event.get("maxValue", 100),
                notes=text,
                raw=detail,
            )
        )
    return result


def weekly_timetable(response: dict[str, Any], lesson_hours: list[dict[str, Any]], *, original: bool = False) -> list[Lesson]:
    data = _data(response)
    blocks = _objects(response.get("data")) or _objects(data.get("byDate")) or [data]
    meta = [data, *blocks]

    subject_names = {
        s.get("_id"): s.get("shortName") or s.get("name") or ""
        for m in meta
        for s in _objects(m.get("subjects"))
    }
    groups = {g.get("_id"): g for m in meta for g in _objects(m.get("groups"))}
    teachers = {
        t.get("_id"): t.get("name") or f"{t.get('student_f_name', '')} {t.get('student_l_name', '')}".strip()
        for m in meta
        for t in _objects(m.get("teachers"))
    }

    # Bell times: the hour set that applies to no specific class, else the first.
    chosen = next((h for h in lesson_hours if not h.get("class_ids")), lesson_hours[0] if lesson_hours else {})
    periods = _objects(chosen.get("periods"))

    lessons: list[Lesson] = []
    for block in blocks:
        updated = _objects(block.get("ttSubjects"))
        base = _objects(block.get("ttGroups"))
        source = base if original or not updated else updated
        first_hour = data.get("firstHour")
        if first_hour is None:
            scope = data.get("scope") or {}
            first_hour = scope.get("firstHour", (scope.get("blob") or {}).get("firstHour", block.get("firstHour", 0)))
        for item in source:
            day, slot = item.get("day"), item.get("slot")
            if not isinstance(day, int) or not isinstance(slot, int) or slot < 0:
                continue
            if not original and _present(item.get("movedTo")):
                continue  # the lesson was moved elsewhere
            group = groups.get(item.get("group_id")) or {}
            subject = item.get("group_name") or item.get("subjectName") or subject_names.get(item.get("subject")) or ""
            teacher_ids = group.get("teachers") if isinstance(group.get("teachers"), list) else []
            teacher = item.get("teacherNames") or ", ".join(t for t in (teachers.get(i) for i in teacher_ids) if t)
            period = periods[slot] if slot < len(periods) else {}
            cancelled = bool(item.get("isVoided")) or _present(item.get("voidReason"))
            lessons.append(
                Lesson(
                    day=day,
                    period=slot + int(first_hour or 0),
                    subject=subject,
                    teacher=teacher,
                    room=item.get("effectiveRoom_name") or "",
                    start=str(period.get("start") or ""),
                    end=str(period.get("end") or ""),
                    note=item.get("note") or "",
                    cancelled=cancelled and not original,
                    raw=item,
                )
            )
    return sorted(lessons, key=lambda l: (l.day, l.period or 0))
