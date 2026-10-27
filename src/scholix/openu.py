"""Open University of Israel "Sheilta" course and grade API. See ``docs/openu.md``.

Example::

    from scholix.openu import OpenUClient

    client = OpenUClient()
    client.login("username", "password", "student-id")
    for course in client.courses():
        print(course["name"], course["term"], course.get("final_grade"))
    for grade in client.grades(client.courses()[0]):
        print(grade.name, grade.value)
"""

from __future__ import annotations

import re
from typing import Any

from ._http import HttpSession
from .errors import AuthenticationError, ProtocolError, SessionExpired
from .models import Grade

COURSES_PAGE = "https://sheilta.apps.openu.ac.il/Main/Courses"
COURSES_API = "https://sheilta.apps.openu.ac.il/Main/api/coursesApi/get"
AUTH_API = "https://sheilta.apps.openu.ac.il/Main/api/homeApi/isAuthenticated"
SSO_PROCESS = "https://sso.apps.openu.ac.il/process"


def _first_text(source: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = source.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "null":
            return text
    return None


def _explicitly_false(source: dict[str, Any], name: str) -> bool:
    if name not in source or source[name] is None:
        return False
    value = source[name]
    return value is False or str(value).lower() == "false" or str(value) == "0"


def normalize_term(raw: str) -> str:
    """``"2026ג"`` -> ``"2026c"``: Hebrew semester letters to ``a``/``b``/``c``, keep only digits and letters a-c."""
    mapped = raw.strip().lower().replace("א", "a").replace("ב", "b").replace("ג", "c")
    return "".join(ch for ch in mapped if ch.isdigit() or ch in "abc")


def parse_course(source: dict[str, Any]) -> dict[str, Any] | None:
    course_id = _first_text(source, "courseId", "id")
    name = _first_text(source, "courseName", "name")
    if not course_id or not name:
        return None
    raw_term = _first_text(source, "semester") or ""
    term = normalize_term(raw_term)
    course: dict[str, Any] = {
        "id": course_id,
        "key": f"{course_id}:{term}",
        "name": name,
        "raw_term": raw_term,
        "term": term,
        "semester": next((c for c in reversed(term) if c in "abc"), ""),
        "year": int(term[:4]) if term[:4].isdigit() else None,
        "assignments": _assignments(source),
    }
    for key, names in (
        ("status", ("statusDesc", "statusCodeDisp")),
        ("semester_start", ("semesterStartDate",)),
        ("final_grade", ("finalGrade",)),
        ("exam_grade", ("examGrade",)),
        ("points", ("points",)),
    ):
        value = _first_text(source, *names)
        if value:
            course[key] = value
    return course


def _assignments(course: dict[str, Any]) -> list[dict[str, Any]]:
    # The API spells it "assigment" (sic); some responses use the "...Model" name.
    source = next((course[k] for k in ("assigmentScores", "assigmentScoreModel") if isinstance(course.get(k), list)), [])
    result = []
    for item in source:
        if not isinstance(item, dict) or _explicitly_false(item, "isPublic"):
            continue
        row: dict[str, Any] = {}
        fields = {
            "number": ("assignmentNo",),
            "type": ("assignmentTypeDesc", "assignmentType", "typeDesc", "type"),
            "gradedAt": ("scoreDate", "scoreUpdateDate", "scorePublishedDate", "scorePublicationDate", "gradeDate", "gradeUpdateDate", "updatedAt"),
            "date": ("registrationDate", "submitDate", "submissionDate", "examinationDate", "examDate", "assignmentDate"),
            "weight": ("weight", "assignmentWeight"),
            "status": ("statusDesc", "status"),
            "statusCode": ("statusCode",),
            "assignmentSemester": ("assignmentSemester",),
        }
        for key, names in fields.items():
            value = _first_text(item, *names)
            if value:
                row[key] = value
        if not _explicitly_false(item, "isScorePublic"):  # never expose an unpublished score
            grade = _first_text(item, "scoreValue", "grade", "score")
            if grade:
                row["grade"] = grade
        result.append(row)
    return result


def _is_exam(kind: str) -> bool:
    return any(word in kind.lower() for word in ("exam", "מבחן", "בחינה", "בחינת"))


def course_grades(course: dict[str, Any]) -> list[Grade]:
    """Published assignment scores, the exam grade (when not already an assignment) and the final grade."""
    grades: list[Grade] = []
    seen: set[str] = set()
    exam_grade = (course.get("exam_grade") or "").strip()
    exam_included = False
    for index, row in enumerate(course.get("assignments", [])):
        grade = (row.get("grade") or "").strip()
        if not grade:
            continue
        number, kind = row.get("number", "").strip(), row.get("type", "").strip()
        key = number or f"{kind}|{grade}|{row.get('date', '')}"
        if key in seen:
            continue
        seen.add(key)
        if _is_exam(kind) and grade == exam_grade:
            exam_included = True
        order = re.search(r"\d+", number)
        grades.append(
            Grade(
                subject=course["name"],
                name=" ".join(p for p in (kind, number) if p) or "Assignment",
                value=grade,
                date=row.get("date", ""),
                weight=float(row["weight"]) if re.fullmatch(r"\d+(\.\d+)?", row.get("weight", "")) else None,
                type=row.get("status", ""),
                raw={"id": f"{course['key']}:assignment:{number or index}", "gradedAt": row.get("gradedAt", ""), "order": int(order.group(0)) if order else None},
            )
        )
    final = (course.get("final_grade") or "").strip()
    if final:
        grades.append(Grade(subject=course["name"], name="Final grade", value=final, type="final", raw={"id": f"{course['key']}:final"}))
    if exam_grade and not exam_included:
        grades.append(Grade(subject=course["name"], name="Exam grade", value=exam_grade, raw={"id": f"{course['key']}:exam"}))
    return grades


def looks_like_login(url: str, body: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() == "sso.apps.openu.ac.il"
        or "/login" in parts.path.lower()
        or 'name="p_user"' in body.lower()
        or 'name="p_sisma"' in body.lower()
    )


class OpenUClient:
    def __init__(self, session: HttpSession | None = None) -> None:
        self.http = session or HttpSession(allowed_hosts={"sheilta.apps.openu.ac.il", "sso.apps.openu.ac.il"})

    def is_authenticated(self) -> bool | None:
        """The portal's own session check: ``True``/``False``, or ``None`` when it cannot be read."""
        response = self.http.get(AUTH_API, headers={"Accept": "application/json"})
        if not response.ok:
            return None
        return {"true": True, "false": False}.get(response.text.strip().lower())

    def login(self, username: str, password: str, student_id: str) -> None:
        """Sign in through the university SSO form (``p_user``, ``p_sisma``, ``p_mis_student``)."""
        if not (username and password and student_id):
            raise AuthenticationError("Enter the username, password and student id")
        self.http.get(COURSES_PAGE)
        self.http.post(
            SSO_PROCESS,
            data={"p_user": username, "p_sisma": password, "p_mis_student": student_id, "T_PLACE": COURSES_PAGE},
        )
        page = self.http.get(COURSES_PAGE)
        if not page.ok or looks_like_login(page.url, page.text) or self.is_authenticated() is not True:
            raise AuthenticationError("Open University sign-in did not establish a session")

    def courses(self) -> list[dict[str, Any]]:
        """Every course (current and past) with assignment scores. Raises :class:`SessionExpired` if signed out."""
        response = self.http.get(COURSES_API, headers={"Accept": "application/json"})
        body = response.text.strip().lstrip("﻿")
        if not response.ok:
            if self.is_authenticated() is False:
                raise SessionExpired("Open University session expired")
            raise ProtocolError(f"Open University course API returned {response.status_code}")
        if looks_like_login(response.url, body):
            raise SessionExpired("Open University session expired")
        if not body.startswith("["):
            if self.is_authenticated() is False:
                raise SessionExpired("Open University session expired")
            raise ProtocolError("Open University course API returned a non-array response")
        import json

        return [c for c in (parse_course(item) for item in json.loads(body) if isinstance(item, dict)) if c]

    def grades(self, course: dict[str, Any]) -> list[Grade]:
        return course_grades(course)
