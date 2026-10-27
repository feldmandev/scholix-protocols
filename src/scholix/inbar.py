"""Inbar - the Bar-Ilan University student portal (ASP.NET Web Forms). See ``docs/inbar.md``.

Sign-in is SMS-only: the portal texts a code to the mobile number registered
with the university. There is no password.

Example::

    from scholix.inbar import InbarClient

    client = InbarClient()
    client.request_sms("012345678", "0501234567")
    page = client.verify_sms(input("code: "))      # also returns the newest year's grades
    for course in client.grades(2026).courses:
        print(course.name, [g.value for g in course.grades])
    lessons = client.schedule().lessons
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ._http import HttpSession
from .errors import (
    AuthenticationError,
    NetworkError,
    ProtocolError,
    RateLimited,
    SessionExpired,
)
from .models import Grade, Lesson

HOST = "inbar.biu.ac.il"
BASE = f"https://{HOST}"
LOGIN = f"{BASE}/Live/Login.aspx?ReturnUrl=%2fLive%2fStudentGradesList.aspx"
GRADES = f"{BASE}/Live/StudentGradesList.aspx"
SCHEDULE = f"{BASE}/Live/StudentPeriodSchedule.aspx"

_DATA_PATHS = {"/live/studentgradeslist.aspx", "/live/studentperiodschedule.aspx"}
_AUTH_PATHS = {"/live/login.aspx", "/live/authenticate.aspx"}
_RESTRICTION_TEXT = "לא ניתן לשלוח עוד הודעות/לאמת קוד בשלב זה"
_REDIRECTS = {301, 302, 303, 307, 308}
_POSTBACK = re.compile(r"__doPostBack\('([^']*gvGradesList)',\s*'(Page\$(?:[0-9]+|Next|Previous|First|Last))'\)")
_NO_DATA = {"אין נתונים", "No data", "No records"}


class InbarLayoutChanged(ProtocolError):
    """The portal's table no longer matches the documented layout."""


@dataclass
class InbarCourse:
    id: str  # course-group code
    key: str  # "<year>:<code>"
    name: str
    year: int
    semester: str  # "a", "b", "c" (summer) or ""
    teacher: str = ""
    period: str = ""
    credits: str = ""
    passing_grade: str = ""
    remark: str = ""
    pass_request_remark: str = ""
    grades: list[Grade] = field(default_factory=list)
    #: Other teaching groups of the same course merged into this one (e.g. a tutorial).
    related_groups: list[dict[str, str]] = field(default_factory=list)


@dataclass
class GradesPage:
    year: int
    years: list[int]
    courses: list[InbarCourse]
    page: int = 1
    next_postback: tuple[str, str] | None = None


@dataclass
class SchedulePage:
    year: int
    years: list[int]
    period: str
    periods: dict[str, str]
    lessons: list[Lesson]


@dataclass
class _Page:
    url: str
    html: str

    @property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html, "html.parser")


def _label(text: str) -> str:
    return re.sub(r"[\s ]+", " ", text).strip()


def successful_controls(form: Tag) -> list[tuple[str, str]]:
    """The name/value pairs a browser would submit for ``form`` (excluding all buttons)."""
    fields: list[tuple[str, str]] = []
    for element in form.select("input[name], select[name], textarea[name]"):
        if element.has_attr("disabled"):
            continue
        kind = (element.get("type") or "").lower()
        if kind in ("submit", "button", "image", "reset", "file"):
            continue
        if kind in ("radio", "checkbox") and not element.has_attr("checked"):
            continue
        name = element["name"]
        if element.name == "select":
            chosen = element.select("option[selected]")
            if not chosen and not element.has_attr("multiple"):
                chosen = element.select("option")[:1]
            for option in chosen:
                fields.append((name, option["value"] if option.has_attr("value") else option.get_text()))
        elif element.name == "textarea":
            fields.append((name, element.get_text()))
        elif element.has_attr("value"):
            fields.append((name, element["value"]))
        else:
            fields.append((name, "on" if kind in ("checkbox", "radio") else ""))
    return fields


def _own_rows(table: Tag) -> list[Tag]:
    """Rows that belong to ``table`` itself, not to a table nested inside one of its cells."""
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _cells(row: Tag, names: tuple[str, ...] = ("td",)) -> list[Tag]:
    return row.find_all(list(names), recursive=False)


def _selected_value(select: Tag) -> str:
    option = select.select_one("option[selected]") or select.select_one("option")
    return option.get("value", "") if option else ""


def _years(select: Tag) -> list[int]:
    return [int(o["value"]) for o in select.select("option") if str(o.get("value", "")).isdigit()]


# -- parsing --------------------------------------------------------------------


def parse_grades(html: str, *, combine_groups: bool = True) -> GradesPage:
    doc = BeautifulSoup(html, "html.parser")
    table = doc.select_one("table#ContentPlaceHolder1_gvGradesList")
    if table is None:
        raise SessionExpired("Inbar session expired. Sign in again with an SMS code.")
    selector = doc.select_one("select#cmbActiveYear")
    if selector is None:
        raise InbarLayoutChanged("Inbar year selector missing")
    selected = _selected_value(selector)
    if not selected.isdigit():
        raise InbarLayoutChanged("Invalid Inbar academic year")
    year, years = int(selected), _years(selector)

    rows = _own_rows(table)
    header_row = next((r for r in rows if _cells(r, ("th",))), None)
    headers = [_label(c.get_text()) for c in _cells(header_row, ("th",))] if header_row else []

    def changed(cells: list[Tag]) -> InbarLayoutChanged:
        spans = [c.get("colspan", "") for c in cells]
        return InbarLayoutChanged(
            "Signed in to Inbar, but its grade table could not be read.",
            {"year": year, "headers": headers, "cells": len(cells), "colspans": spans},
        )

    courses: list[InbarCourse] = []
    current_page = 1
    next_postback: tuple[str, str] | None = None
    for row in rows:
        cells = _cells(row)
        if not cells:
            continue

        links = []
        for link in row.select("a[href]"):
            match = _POSTBACK.search(link["href"])
            if match:
                links.append((match.group(1), match.group(2)))
        span = cells[0].get("colspan", "1")
        if len(cells) == 1 and span.isdigit() and int(span) > 1 and links:
            numbers = {int(s.get_text().strip()) for s in row.select("span") if s.get_text().strip().isdigit()}
            if len(numbers) != 1:
                raise changed(cells)
            current_page = numbers.pop()
            next_postback = next((l for l in links if l[1] == "Page$Next"), None) or next(
                (l for l in links if l[1] == f"Page${current_page + 1}"), None
            )
            if next_postback is None and any(
                l[1].split("$")[1].isdigit() and int(l[1].split("$")[1]) > current_page for l in links
            ):
                raise changed(cells)
            continue

        # Web Forms renders an empty year as one spanning cell with no headers.
        if all(not c.get_text().strip() for c in cells) or (
            _label(cells[0].get_text()) in _NO_DATA and all(not c.get_text().strip() for c in cells[1:])
        ):
            continue
        if len(cells) != len(headers) or any(c.get("colspan", "1") != "1" for c in cells):
            raise changed(cells)

        def cell(hebrew: str, english: str, required: bool = False) -> Tag | None:
            for index, header in enumerate(headers):
                if header == hebrew or header.lower() == english.lower():
                    return cells[index]
            if required:
                raise changed(cells)
            return None

        def text(hebrew: str, english: str) -> str:
            found = cell(hebrew, english)
            return found.get_text().strip() if found else ""

        code = cell("קוד קבוצת קורס", "Code", True).get_text().strip()
        name = cell("שם", "Name", True).get_text().strip()
        if not code or not name:
            raise InbarLayoutChanged("Inbar grade course is missing")
        date = text("ת.עדכון", "Updated")
        key = f"{year}:{code}"
        grades = _assignments(cell("מטלות", "Assignments"), key, name, date)
        final_cell = cell("ציון סופי", "Final grade", True)
        final = final_cell.select_one("span[id*=lblRowFinalGrade]")
        if final and final.get_text().strip():
            grades.append(
                Grade(subject=name, name="Final grade", type="final", value=final.get_text().strip(), date=date, raw={"id": f"{key}:final"})
            )
        period = cell("תקופה", "Period", True).get_text().strip()
        courses.append(
            InbarCourse(
                id=code,
                key=key,
                name=name,
                year=year,
                semester=_semester(period),
                teacher=text("מרצה", "Lecturer"),
                period=period,
                credits=text('נ"ז', "Credits"),
                passing_grade=text("ציון עובר", "Passing grade"),
                remark=text("הערה", "Remark"),
                pass_request_remark=text("בקשה לציון עובר", "Pass request"),
                grades=grades,
            )
        )
    return GradesPage(year, years, combine_teaching_groups(courses) if combine_groups else courses, current_page, next_postback)


def _semester(period: str) -> str:
    if "קיץ" in period:
        return "c"
    letter = re.search(r"[אב][׳'\"]?", period.partition("סמסטר")[2])
    if letter and letter.group(0).startswith("א"):
        return "a"
    if "סמסטר" in period and "ב" in period:
        return "b"
    return ""


def _assignments(cell: Tag | None, key: str, subject: str, date: str) -> list[Grade]:
    grades: list[Grade] = []
    table = cell.find("table") if cell else None
    if table is None:
        return grades
    current: Grade | None = None
    for index, row in enumerate(table.find_all("tr")):
        values = [_label(c.get_text()) for c in _cells(row, ("td", "th"))]
        classes = row.get("class") or []
        if "AssignmentText" in classes and len(values) == 3:
            current = Grade(
                subject=subject,
                name=values[0],
                value=values[2] or None,
                date=date,
                weight=_number(values[1]),
                raw={"id": f"{key}:assignment:{len(grades)}", "weight": values[1], "submissions": []},
            )
            grades.append(current)
        elif current is not None and "AssignmentHeader" not in classes and len(values) == 2:
            current.raw["submissions"].append({"type": values[0], "date": values[0], "grade": values[1]})
            current.date = values[0].split(" ")[0]
    return grades


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def combine_teaching_groups(courses: list[InbarCourse]) -> list[InbarCourse]:
    """Merge a lecture and its ungraded companions (tutorials) that share a course-code prefix.

    Independently graded groups stay separate; empty companions are recorded in
    ``related_groups`` instead of appearing as empty courses.
    """
    families: dict[tuple[str, str, str, str], list[InbarCourse]] = {}
    for course in courses:
        prefix = course.id.rpartition("-")[0] or course.id
        families.setdefault((str(course.year), course.name, course.period, prefix), []).append(course)
    result: list[InbarCourse] = []
    for group in families.values():
        graded = [c for c in group if c.grades]
        shown = graded or [max(group, key=lambda c: _number(c.credits) or 0.0)]
        for course in shown:
            if len(group) > 1:
                course.related_groups = [
                    {"id": o.id, "teacher": o.teacher, "credits": o.credits} for o in group if o is not course
                ]
            result.append(course)
    return result


_DAYS = [
    {"א", "ראשון", "sunday", "sun", "1"},
    {"ב", "שני", "monday", "mon", "2"},
    {"ג", "שלישי", "tuesday", "tue", "3"},
    {"ד", "רביעי", "wednesday", "wed", "4"},
    {"ה", "חמישי", "thursday", "thu", "5"},
    {"ו", "שישי", "friday", "fri", "6"},
    {"ש", "שבת", "saturday", "sat", "7"},
]


def _day_index(raw: str) -> int | None:
    day = re.sub(r"[\s'\"׳״]", "", raw.lower().replace("יום", ""))
    return next((i for i, names in enumerate(_DAYS) if day in names), None)


def parse_schedule(html: str) -> SchedulePage:
    doc = BeautifulSoup(html, "html.parser")
    if doc.select_one("input[name=edtUsername], input[name=edtCode]"):
        raise SessionExpired("Inbar session expired. Sign in again with an SMS code.")
    grids = doc.select("table[id$=_gvPeriodSchedule]")
    if not grids:
        raise InbarLayoutChanged("Inbar schedule table is missing")
    year_select = doc.select_one("select#cmbActiveYear")
    period_select = doc.select_one("select[id$=ddlPeriodTypeFilter2]")
    if year_select is None:
        raise InbarLayoutChanged("Inbar year selector missing")
    if period_select is None:
        raise InbarLayoutChanged("Inbar semester selector missing")
    year_value = _selected_value(year_select)
    if not year_value.isdigit():
        raise InbarLayoutChanged("Invalid schedule year")
    year = int(year_value)

    lessons: list[Lesson] = []
    for grid in grids:
        headers = [_label(th.get_text()) for th in grid.find_all("th")]

        def column(label: str) -> int:
            if label not in headers:
                raise InbarLayoutChanged("Inbar schedule columns changed")
            return headers.index(label)

        day_c, time_c, subject_c = column("יום"), column("שעה"), column("שם קבוצת קורס")
        code_c, period_c, teacher_c, room_c = column("קוד קבוצת קורס"), column("תקופה"), column("מרצה"), column("חדר")
        extra = {
            key: headers.index(label)
            for label, key in (("שעות", "hours"), ('נ"ז', "credits"), ('ש"ש', "weekly_hours"), ("עזרים", "accessories"), ("סוג מפגש", "meeting_type"))
            if label in headers
        }
        for row in _own_rows(grid):
            cells = _cells(row)
            if not cells:
                continue
            if all(not c.get_text().strip() for c in cells) or (
                cells[0].get_text().strip() in _NO_DATA and all(not c.get_text().strip() for c in cells[1:])
            ):
                continue
            if len(cells) != len(headers):
                raise InbarLayoutChanged("Inbar schedule row changed")
            subject = cells[subject_c].get_text().strip()
            if not subject:
                raise InbarLayoutChanged("Inbar schedule course is missing")
            day = _day_index(cells[day_c].get_text())
            if day is None:
                raise InbarLayoutChanged("Unrecognized Inbar schedule day")
            times = []
            for match in re.finditer(r"(?<![0-9])([0-9]{1,2}):([0-9]{2})(?![0-9])", cells[time_c].get_text()):
                hour, minute = int(match.group(1)), int(match.group(2))
                if hour > 23 or minute > 59:
                    raise InbarLayoutChanged("Invalid Inbar lesson time")
                times.append(f"{hour:02d}:{minute:02d}")
            if not times or len(times) > 2:
                raise InbarLayoutChanged("Unrecognized Inbar lesson time")
            times.sort()  # Hebrew directionality can reverse the visual order of the endpoints
            lessons.append(
                Lesson(
                    day=day,
                    subject=subject,
                    teacher=cells[teacher_c].get_text().strip(),
                    room=cells[room_c].get_text().strip(),
                    start=times[0],
                    end=times[1] if len(times) > 1 else "",
                    raw={
                        "course_code": cells[code_c].get_text().strip(),
                        "year": year,
                        "period": cells[period_c].get_text().strip(),
                        **{k: cells[i].get_text().strip() for k, i in extra.items()},
                    },
                )
            )
    lessons.sort(key=lambda l: (l.day, l.start))
    return SchedulePage(
        year,
        _years(year_select),
        _selected_value(period_select),
        {o.get("value", ""): o.get_text().strip() for o in period_select.select("option")},
        lessons,
    )


# -- client ---------------------------------------------------------------------


class InbarClient:
    """One Inbar session. Not thread-safe."""

    #: Seconds to wait between SMS requests on one challenge (the portal's own resend cooldown).
    RESEND_COOLDOWN = 45.0

    def __init__(self, session: HttpSession | None = None) -> None:
        self.http = session or HttpSession(allowed_hosts={HOST})
        self._challenge: _Page | None = None
        self._sms_time = 0.0
        self._grade_page: _Page | None = None
        self._grade_year: int | None = None

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, url: str, *, data: list[tuple[str, str]] | None = None,
                 headers: dict[str, str] | None = None, allow_auth_redirect: bool = True) -> _Page:
        for _ in range(10):
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname != HOST or (parts.port not in (None, 443)):
                raise ProtocolError("Inbar redirected outside its portal; use the portal's browser sign-in")
            response = self.http.request(method, url, data=data, headers=headers, allow_redirects=False)
            if response.status_code in _REDIRECTS:
                location = response.headers.get("Location")
                if not location:
                    raise ProtocolError("Inbar redirect missing Location")
                destination = urljoin(url, location)
                if not allow_auth_redirect and urlsplit(destination).path.lower() in _AUTH_PATHS:
                    # Reading data must never visit an endpoint that can create another SMS challenge.
                    raise SessionExpired("Inbar session expired. Sign in again with an SMS code.")
                if response.status_code == 303 or (response.status_code in (301, 302) and method == "POST"):
                    method, data = "GET", None
                url = destination
                continue
            if not response.ok:
                raise NetworkError(f"Inbar returned HTTP {response.status_code}")
            return _Page(response.url, response.text)
        raise ProtocolError("Too many Inbar redirects")

    def _get(self, url: str) -> _Page:
        return self._request("GET", url, allow_auth_redirect=urlsplit(url).path.lower() not in _DATA_PATHS)

    def _post(self, page: _Page, overrides: dict[str, str]) -> _Page:
        form = page.soup.select_one("form#form1")
        if form is None:
            raise ProtocolError("Inbar form is missing")
        data = [(k, v) for k, v in successful_controls(form) if k not in overrides] + list(overrides.items())
        return self._request(
            "POST",
            urljoin(page.url, form.get("action", "")),
            data=data,
            headers={"Referer": page.url, "Origin": BASE},
            allow_auth_redirect=urlsplit(page.url).path.lower() not in _DATA_PATHS,
        )

    @staticmethod
    def _check_restriction(page: _Page) -> None:
        # The portal reports the restriction in a JavaScript alert while still rendering the code box.
        for script in page.soup.find_all("script"):
            if _RESTRICTION_TEXT in re.sub(r"\s+", " ", script.get_text()):
                raise RateLimited("Inbar has temporarily blocked sending and verifying SMS codes. Try again later.")

    # -- authentication -----------------------------------------------------

    def request_sms(self, identity: str, mobile: str) -> None:
        """Submit the ID/passport and registered mobile number; the portal texts a code.

        Call at most once per sign-in attempt. Raises :class:`RateLimited` if the
        portal refuses (it gives no expiry for that restriction).
        """
        if not identity or not mobile:
            raise AuthenticationError("Enter ID/passport and registered mobile")
        page = self._get(LOGIN)
        doc = page.soup
        if doc.select_one("input[name=edtUsername]") is None or doc.select_one("input[name=edtMobile]") is None:
            raise ProtocolError("Inbar login has changed")
        button = doc.select_one("input[name=btnLogin]")
        if button is None:
            raise ProtocolError("Inbar login button missing")
        nxt = self._post(page, {"edtUsername": identity, "edtMobile": mobile, "btnLogin": button.get("value", "")})
        self._check_restriction(nxt)
        if nxt.soup.select_one("input[name=edtCode]") is None:
            raise AuthenticationError("Inbar did not accept the login. Check the ID and registered mobile.")
        self._challenge, self._sms_time = nxt, time.monotonic()

    def resend_sms(self) -> None:
        page = self._challenge
        if page is None:
            raise AuthenticationError("Request an SMS first")
        if time.monotonic() - self._sms_time < self.RESEND_COOLDOWN:
            raise RateLimited(f"Wait {self.RESEND_COOLDOWN:.0f} seconds before requesting another SMS")
        button = page.soup.select_one("input[name=btnSendSmsCode]")
        if button is None:
            raise ProtocolError("Inbar resend button missing")
        nxt = self._post(page, {"btnSendSmsCode": button.get("value", "")})
        self._check_restriction(nxt)
        self._challenge, self._sms_time = nxt, time.monotonic()

    def verify_sms(self, code: str) -> GradesPage:
        """Submit the code. On success returns the grades page the portal lands on."""
        if not re.fullmatch(r"[0-9]{4,10}", code):
            raise AuthenticationError("Enter the verification code")
        page = self._challenge
        if page is None:
            raise AuthenticationError("Request an SMS first")
        button = page.soup.select_one("input[name=btnVerify]")
        if button is None:
            raise ProtocolError("Inbar verify button missing")
        nxt = self._post(page, {"edtCode": code, "btnVerify": button.get("value", "")})
        self._check_restriction(nxt)
        if nxt.soup.select_one("input[name=edtCode]") is not None:
            self._challenge = nxt  # fresh form state for a corrected code
            raise AuthenticationError("The SMS code was not accepted")
        self._challenge = None
        if urlsplit(nxt.url).path.lower() == "/live/studentgradeslist.aspx":
            return self._read_grade_pages(nxt, parse_grades(nxt.html, combine_groups=False))
        return self.grades()

    # -- data ---------------------------------------------------------------

    def grades(self, year: int | None = None) -> GradesPage:
        """Grades for an academic year (default: the portal's current year), all result pages combined."""
        page = self._grade_page if year is not None and self._grade_year != year and self._grade_page else None
        page = page or self._get(GRADES)
        result = parse_grades(page.html, combine_groups=False)
        if year is not None and result.year != year:
            if year not in result.years:
                raise ProtocolError(f"Academic year {year} is not available in Inbar")
            select = page.soup.select_one("select#cmbActiveYear")
            name = select["name"]
            page = self._post(page, {name: str(year), "__EVENTTARGET": name, "__EVENTARGUMENT": ""})
            result = parse_grades(page.html, combine_groups=False)
            if result.year != year:
                raise ProtocolError(f"Inbar did not select year {year}")
        return self._read_grade_pages(page, result)

    def _read_grade_pages(self, page: _Page, first: GradesPage) -> GradesPage:
        courses = list(first.courses)
        visited = {first.page}
        parsed = first
        while parsed.next_postback is not None:
            if len(visited) >= 100:
                raise ProtocolError("Inbar grade pagination did not finish")
            target, argument = parsed.next_postback
            page = self._post(page, {"__EVENTTARGET": target, "__EVENTARGUMENT": argument})
            loaded = parse_grades(page.html, combine_groups=False)
            if loaded.year != first.year or loaded.page != parsed.page + 1 or loaded.page in visited:
                raise ProtocolError("Inbar did not advance the grades page")
            visited.add(loaded.page)
            courses += loaded.courses
            parsed = loaded
        self._grade_page, self._grade_year = page, first.year
        return GradesPage(first.year, first.years, combine_teaching_groups(courses), first.page, None)

    def schedule(self, year: int | None = None, period: str | None = None) -> SchedulePage:
        """The period timetable. One request covers the whole week.

        ``period`` is the semester selector's value (read it from ``SchedulePage.periods``).
        """
        page = self._get(SCHEDULE)
        result = parse_schedule(page.html)
        if year is not None and year != result.year:
            if year not in result.years:
                raise ProtocolError(f"Academic year {year} is not available in Inbar")
            name = page.soup.select_one("select#cmbActiveYear")["name"]
            page = self._post(page, {name: str(year), "__EVENTTARGET": name, "__EVENTARGUMENT": ""})
            result = parse_schedule(page.html)
            if result.year != year:
                raise ProtocolError(f"Inbar did not select schedule year {year}")
        if period is not None and period != result.period:
            if period not in result.periods:
                raise ProtocolError("Semester is not available in Inbar")
            name = page.soup.select_one("select[id$=ddlPeriodTypeFilter2]")["name"]
            page = self._post(page, {name: period, "__EVENTTARGET": name, "__EVENTARGUMENT": ""})
            result = parse_schedule(page.html)
            if result.period != period:
                raise ProtocolError("Inbar did not select the schedule semester")
        return result

    # -- persistence ----------------------------------------------------------

    def export_session(self) -> list[dict[str, Any]]:
        return self.http.export_cookies()

    def restore_session(self, cookies: list[dict[str, Any]]) -> None:
        self.http.import_cookies(cookies)
