"""Open University of Israel (Sheilta + Opal). See ``docs/openu/README.md``.

Example::

    from scholix.openu import OpenUClient

    client = OpenUClient()
    client.login("username", "password", "student-id")
    for course in client.courses():
        print(course["key"], course["name"], course["status_kind"])
        for grade in client.grades(course):
            print("   ", grade.name, grade.value)

Reads only. Quizzes, forum posts, book-terms consent, request creation and
uploads are documented but intentionally not implemented.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag

from ._http import HttpSession
from .errors import (
    AuthenticationError,
    NetworkError,
    ProtocolError,
    SessionExpired,
)
from .models import Grade

SHEILTA = "https://sheilta.apps.openu.ac.il"
OPAL = "https://opal.openu.ac.il"
COURSES_PAGE = f"{SHEILTA}/Main/Courses"
COURSES_API = f"{SHEILTA}/Main/api/coursesApi/get"
AUTH_API = f"{SHEILTA}/Main/api/homeApi/isAuthenticated"
MEETINGS_API = f"{SHEILTA}/Main/api/coursesApi"
SSO_PROCESS = "https://sso.apps.openu.ac.il/process"
LEGACY_HOME = f"{SHEILTA}/pls/dmyopt2/sheilta.main"
EXAMS_PAGE = f"{SHEILTA}/pls/dmyopt2/course_info.Home_Test"
EXAM_DETAILS_PAGE = f"{SHEILTA}/pls/dmyopt2/bchinot.bchinot_screen"

HOSTS = {
    "opal.openu.ac.il",
    "opsrv.apps.openu.ac.il",
    "sheilta.apps.openu.ac.il",
    "sso.apps.openu.ac.il",
    "www.openu.ac.il",
    "www-e.openu.ac.il",
}
HLS_SUFFIXES = ("bynetcdn.com", "openu.ac.il")


# -- helpers ---------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_semester(value: str) -> str:
    """``"2026ב"`` -> ``"2026b"``. Raises ``ValueError`` when no ``20YY`` + a/b/c prefix is found.

    (The author's TypeScript client silently substitutes a guess from today's date here;
    that hides bad input, so the guess is a separate helper: :func:`guess_semester`.)
    """
    lowered = (value or "").strip().lower()
    for hebrew, latin in (("א", "a"), ("ב", "b"), ("ג", "c")):
        lowered = lowered.replace(hebrew, latin, 1)  # first occurrence only, like the portal client
    match = re.match(r"^20\d{2}[abc]", lowered)
    if not match:
        raise ValueError(f"Not an OpenU semester: {value!r}")
    return match.group(0)


def guess_semester(today: _dt.date | None = None) -> str:
    today = today or _dt.date.today()
    return f"{today.year}{'a' if today.month < 5 else 'b' if today.month < 10 else 'c'}"


def sheilta_semester(value: str) -> str:
    """Latin to the Hebrew letter the document API wants: ``2026b`` -> ``2026ב``."""
    semester = normalize_semester(value)
    return semester[:-1] + {"a": "א", "b": "ב", "c": "ג"}[semester[-1]]


def normalize_term(raw: str) -> str:
    """Lenient term key used for course ids: lower-case, Hebrew letters mapped, only digits and ``a``-``c``."""
    mapped = raw.strip().lower().replace("א", "a").replace("ב", "b").replace("ג", "c")
    return "".join(ch for ch in mapped if ch.isdigit() or ch in "abc")


def assert_allowed_url(url: str, *, hls: bool = False) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    ok = (
        any(host == s or host.endswith("." + s) for s in HLS_SUFFIXES)
        if hls
        else host in HOSTS
    )
    if parts.scheme != "https" or parts.username or parts.password or not ok:
        raise ProtocolError("Upstream URL is not allowed")
    return url


def _first_text(source: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = source.get(name)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text and text.lower() != "null":
            return text
    return None


def _explicitly_false(source: dict[str, Any], name: str) -> bool:
    if source.get(name) is None:
        return False
    value = source[name]
    return value is False or str(value).lower() == "false" or str(value) == "0"


def _truthy_key(value: Any, pattern: str) -> bool:
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if re.search(pattern, key, re.I) and (
            item is True or item == 1 or str(item).lower() in ("true", "yes", "1", "כ")
        ):
            return True
        if isinstance(item, dict) and _truthy_key(item, pattern):
            return True
    return False


def _academic_url(value: Any, pattern: str, key: str = "") -> str | None:
    if isinstance(value, str):
        if (re.search(pattern, key, re.I) or re.search(pattern, value, re.I)) and re.match(r"https://", value, re.I):
            return value
        return None
    if isinstance(value, list):
        return next((u for u in (_academic_url(i, pattern, key) for i in value) if u), None)
    if isinstance(value, dict):
        for child_key, child in value.items():
            found = _academic_url(child, pattern, child_key)
            if found:
                return found
    return None


_APPEAL = r"objection|appeal|irur"


def classify_status(text: str, start_date: str = "") -> str:
    """``canceled`` / ``did`` / ``this_semester`` / ``upcoming`` from the Hebrew status text."""
    if re.search(r"בוטל|מבוטל", text):
        return "canceled"
    if re.search(r"הצלחה|סיים|עבר", text):
        return "did"
    if "בלימוד" in text:
        return "this_semester"
    if "נרשם" in text:
        try:
            started = _dt.date.fromisoformat(start_date[:10]) <= _dt.date.today()
        except ValueError:
            started = False
        return "this_semester" if started else "upcoming"
    return "did"


# -- course rows -----------------------------------------------------------------


def parse_course(source: dict[str, Any]) -> dict[str, Any] | None:
    course_id = _first_text(source, "courseId", "id")
    name = _first_text(source, "courseName", "name")
    if not course_id or not name:
        return None
    raw_term = _first_text(source, "semester") or ""
    term = normalize_term(raw_term)
    status_text = _first_text(source, "statusDesc", "statusCodeDisp") or ""
    start = _first_text(source, "semesterStartDate") or ""
    course: dict[str, Any] = {
        "id": course_id,
        "key": f"{course_id}:{term}",
        "name": name,
        "raw_term": raw_term,
        "term": term,
        "semester": next((c for c in reversed(term) if c in "abc"), ""),
        "year": int(term[:4]) if term[:4].isdigit() else None,
        "status_kind": classify_status(status_text, start),
        "study_center": _first_text(source, "studyCenter") or "",
        "study_group": _first_text(source, "studyGroup") or "",
        "activity_code": _first_text(source, "activityCode") or "",
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


def _raw_assessments(source: dict[str, Any]) -> list[dict[str, Any]]:
    # The API spells it "assigment" (sic); some responses use the "...Model" name.
    found = next((source[k] for k in ("assigmentScores", "assigmentScoreModel") if isinstance(source.get(k), list)), [])
    return [i for i in found if isinstance(i, dict)]


def _assignments(course: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in _raw_assessments(course):
        if _explicitly_false(item, "isPublic"):
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
        questions = [
            {
                "number": _first_text(q, "questionNo", "questionNumber", "number") or "",
                "score": _first_text(q, "score", "scoreValue", "grade") or "",
            }
            for q in item.get("questionScores", [])
            if isinstance(q, dict)
        ]
        questions = [q for q in questions if q["number"] or q["score"]]
        if questions and not _explicitly_false(item, "isScorePublic"):
            row["questionScores"] = questions
        is_exam = bool(re.search(r"בחינ|מבחנ", row.get("type", "")))
        has_grade = "grade" in row
        row["isExam"] = is_exam
        row["artifacts"] = {
            "questionnaire": ("personal" if _truthy_key(item, "questionnaireScanned") else "exam") if is_exam else None,
            "notebook": is_exam and (_truthy_key(item, r"examScanned|notebook") or bool(questions) or has_grade),
            "appeal": is_exam and (bool(_academic_url(item, _APPEAL)) or _truthy_key(item, _APPEAL) or has_grade),
        }
        if isinstance(item.get("hasExamFileId"), bool):  # explicit flags beat the inferred ones
            row["artifacts"]["notebook"] = item["hasExamFileId"]
        if isinstance(item.get("hasQuestionnaireFileId"), bool):
            row["artifacts"]["questionnaire"] = (
                "personal" if item["hasQuestionnaireFileId"] else ("exam" if item.get("hasQuestionnaire") else None)
            )
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


def looks_like_login(url: str, body: str = "") -> bool:
    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() == "sso.apps.openu.ac.il"
        or "/login" in parts.path.lower()
        or 'name="p_user"' in body.lower()
        or 'name="p_sisma"' in body.lower()
    )


def _assessment_key(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return str(int(text)) if text.isdigit() else text


def merge_assessments(opal: list[dict[str, str]], sheilta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Opal's list with a course's Sheilta rows (see ``docs/openu/sheilta-courses.md``)."""
    merged: dict[str, dict[str, Any]] = {}
    for row in opal:
        number = row.get("number", "")
        merged[_assessment_key(number)] = {
            "number": number,
            "type": row.get("name") or row.get("type") or "מטלה",
            "name": row.get("name") or "מטלה",
            "start_date": row.get("start_date", ""),
            "due_date": row.get("due_date", ""),
            "registration_date": "",
            "exam_date": "",
            "weight": "",
            "grade": row.get("grade", ""),
            "status": row.get("status", ""),
            "status_code": row.get("status_code", ""),
            "url": row.get("url", ""),
            "is_exam": bool(re.search(r"בחינ|מבחנ", row.get("name") or row.get("type") or "")),
            "question_scores": [],
            "artifacts": {"questionnaire": None, "notebook": False, "appeal": False},
        }
    for row in sheilta:
        key = _assessment_key(row.get("number", ""))
        entry = {
            "number": row.get("number", ""),
            "type": row.get("type", ""),
            "name": row.get("type", ""),
            "start_date": "",
            "due_date": "",
            "registration_date": row.get("date", "") if not row.get("isExam") else "",
            "exam_date": row.get("date", "") if row.get("isExam") else "",
            "weight": row.get("weight", ""),
            "grade": row.get("grade", ""),
            "status": row.get("status", ""),
            "status_code": row.get("statusCode", ""),
            "url": "",
            "is_exam": row.get("isExam", False),
            "question_scores": row.get("questionScores", []),
            "artifacts": row.get("artifacts", {"questionnaire": None, "notebook": False, "appeal": False}),
        }
        existing = merged.get(key)
        if existing:
            existing.update(
                type=entry["type"] or existing["type"],
                registration_date=entry["registration_date"],
                exam_date=entry["exam_date"],
                weight=entry["weight"],
                grade=entry["grade"] or existing["grade"],
                status=existing["status"] or entry["status"],
                status_code=existing["status_code"] or entry["status_code"],
                is_exam=entry["is_exam"],
                question_scores=entry["question_scores"],
                artifacts=entry["artifacts"],
            )
        else:
            merged[key] = entry

    def order(item: dict[str, Any]) -> tuple[int, float, str]:
        number = item["number"]
        return (0, float(number), "") if re.fullmatch(r"\d+(\.\d+)?", number) else (1, 0.0, number)

    return sorted(merged.values(), key=order)


# -- HTML parsers ----------------------------------------------------------------


def parse_exams(html: str) -> list[dict[str, str]]:
    """The alternative-assessments accordion: one dict per published sitting."""
    doc = BeautifulSoup(html, "html.parser")
    exams = []
    for button in doc.select("button.accordion"):
        heading = re.search(r"קורס:\s*(\d+)\s*-\s*(.*?)\s*\|\s*סמסטר לימוד:\s*(\S+)", _norm(button.get_text()))
        panel = button.find_next_sibling("div", class_="panel")
        if not heading or panel is None:
            continue
        course_id, course_name, semester = heading.groups()
        paragraphs: list[str] = []
        for node in panel.select("p.covid_p, table.content_tbl.covid_table"):
            if node.name == "p":
                paragraphs = [*paragraphs, _norm(node.get_text())][-2:]
                continue
            headers = [_norm(th.get_text()) for th in node.find_all("th")]
            row_texts = [_norm(tr.get_text()) for tr in node.find_all("tr")]
            cells = next(([td for td in tr.find_all("td")] for tr in node.find_all("tr") if tr.find("td")), None)
            paragraphs_used, paragraphs = paragraphs, []
            if not cells:
                continue
            details = {h: _norm(cells[i].get_text()) for i, h in enumerate(headers) if i < len(cells)}
            label = "תאור מתכונת הבחינה:"
            try:
                semester_key = normalize_semester(semester)
            except ValueError:
                semester_key = semester
            exams.append(
                {
                    "course_id": course_id,
                    "course_name": course_name,
                    "semester": semester_key,
                    "format": next((p.replace(label, "").strip() for p in paragraphs_used if label in p), ""),
                    "exam_semester": next((p for p in paragraphs_used if "סמסטר בחינה:" in p), ""),
                    "date": details.get("תאריך בחינה", ""),
                    "start_time": details.get("שעת התחלה", ""),
                    "end_time": details.get("שעת סיום", ""),
                    "location": details.get("מקום בחינה", ""),
                    "transportation": "",
                    "center": details.get("פרטי מרכז הוראה", ""),
                    "registration": next((t for t in row_texts if "נרשמת" in t), ""),
                    "note": next((t for t in row_texts if t.startswith("הערה:")), ""),
                    "allowed_materials": "",
                    "url": EXAMS_PAGE,
                }
            )
    return exams


def parse_exam_details(exam: dict[str, str], html: str, page_url: str) -> dict[str, str] | None:
    """The per-course exam screen; ``None`` until a sitting is confirmed."""
    doc = BeautifulSoup(html, "html.parser")
    text = _norm(doc.get_text(" "))
    date = re.search(r"זימון למועד\s+(\d{1,2}/\d{1,2}/20\d{2})", text)
    start = re.search(r"שעת התחלת הבחינה\s+([0-2]?\d:[0-5]\d)", text)
    if not date or not start:
        return None
    moed = doc.select_one('input[name="p_moed_act"]')
    sitting = _norm(moed.get("value", "")) if moed else ""
    location = re.search(r"מרכז הבחינה\s+(.*?)\s+חומר עזר המותר בשימוש:", text)
    materials = re.search(r"חומר עזר המותר בשימוש:\s*(.*?)\s+בחר מועד בחינה", text)
    where, _, transport = _norm(location.group(1) if location else exam["location"]).partition("תחבורה ציבורית:")
    shown = sheilta_semester(exam["semester"]) if re.match(r"^20\d{2}[abc]$", exam["semester"]) else exam["semester"]
    return {
        **exam,
        "exam_semester": f"סמסטר בחינה: {shown}" + (f", מועד: {sitting}" if sitting else ""),
        "date": date.group(1),
        "start_time": start.group(1),
        "end_time": "",
        "location": where.strip(),
        "transportation": transport.strip(),
        "registration": "נרשמת למועד הבחינה",
        "allowed_materials": _norm(materials.group(1) if materials else ""),
        "url": page_url,
    }


def _field(item: Tag, name: str) -> str:
    element = item.select_one(f'[index-item="{name}"]')
    if element is None:
        return ""
    for title in element.select(".ouilassign_index_list_title"):
        title.extract()
    return _norm(element.get_text())


def parse_assignment_list(html: str) -> list[dict[str, str]]:
    doc = BeautifulSoup(html, "html.parser")
    rows = []
    for item in doc.select("#ouilassign_index_list_container > li, #ouilassign_index_list_container > span > li"):
        link = item.select_one('[index-item="name"] a')
        if link is None:
            continue
        span = link.find("span")
        number = _norm(span.get_text()) if span else ""
        if span:
            span.extract()
        href = link.get("href", "")
        status = item.select_one('[index-item="status"] [ouilassign-status]')
        rows.append(
            {
                "type": item.get("data-assigh-type") or ("online" if "/mod/quiz/" in href else "file"),
                "number": number,
                "name": _norm(link.get_text()),
                "due_date": _field(item, "duedate"),
                "start_date": _field(item, "timeopen"),
                "status": _field(item, "status"),
                "status_code": status.get("ouilassign-status", "") if status else "",
                "grade": _field(item, "grade"),
                "url": urljoin(OPAL + "/", href) if href.startswith(("/", "https://")) else "",
            }
        )
    return rows


def parse_sections(html: str) -> list[dict[str, str]]:
    doc = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, str]] = {}

    def add(href: str | None, title: str) -> None:
        if not href or not title:
            return
        ids = parse_qs(urlsplit(urljoin(OPAL, href)).query).get("sectionid")
        if ids and ids[0] not in found:
            found[ids[0]] = {"id": ids[0], "title": title}

    for a in doc.select(".sections-nav-list .dropdown-menu a[href*='sectionid='], a.dropdown-item[href*='sectionid=']"):
        add(a.get("href"), _norm(a.get_text()))
    if not found:
        for a in doc.select("[role='main'] a.section[href*='sectionid=']"):
            name = a.select_one(".sectionname:not(.hidden)")
            add(a.get("href"), _norm(name.get_text()) if name else _norm(a.get_text()))
    if not found:
        for li in doc.select("li.section[data-sectionid], [data-sectionid].section"):
            name = li.select_one(".sectionname, .section-title, h3")
            title, sid = _norm(name.get_text()) if name else "", li.get("data-sectionid", "")
            if sid and title and sid not in found:
                found[sid] = {"id": sid, "title": title}
    return list(found.values())


def parse_current_section_id(html: str) -> str | None:
    doc = BeautifulSoup(html, "html.parser")
    current = doc.select_one("[data-sectionid].current, [data-sectionid].active")
    if current:
        return current.get("data-sectionid")
    link = doc.select_one("a.nav-current-section[href*='sectionid='], li.type_structure.current > a[href*='sectionid=']")
    if link:
        ids = parse_qs(urlsplit(urljoin(OPAL, link.get("href", ""))).query).get("sectionid")
        return ids[0] if ids else None
    return None


def activity_type(module: str, title: str = "") -> str:
    if module in ("combopage", "page", "resource"):
        return "content"
    if module == "quiz":
        return "quiz"
    if module in ("ouilforum", "forum") or "פורום" in title:
        return "forum"
    if "video" in module:
        return "video"
    if module == "folder":
        return "file"
    if module in ("assign", "ouilassign"):
        return "assignment"
    return "link"


def parse_activities(html: str, section_id: str | None = None) -> list[dict[str, Any]]:
    doc = BeautifulSoup(html, "html.parser")
    candidates: list[Tag] = list(
        doc.select(
            ".block_ouil_navigation li.type_structure.current_parent > ul > li.type_activity, "
            ".block_ouil_navigation li.type_structure.contains_current > ul > li.type_activity, "
            ".block_ouil_navigation li.type_structure.contains_direct_current > ul > li.type_activity, "
            ".course-content li.activity"
        )
    )
    if section_id:
        title = next((s["title"] for s in parse_sections(html) if s["id"] == section_id), None)
        for item in doc.select(".block_ouil_navigation li.type_structure"):
            first_p = item.find("p", recursive=False)
            link = first_p.select_one("a[href*='sectionid=']") if first_p else None
            by_href = bool(link) and parse_qs(urlsplit(urljoin(OPAL, link.get("href", ""))).query).get("sectionid", [None])[0] == section_id
            by_title = bool(title and first_p and _norm(first_p.get_text()) == title)
            if by_href or by_title:
                ul = item.find("ul", recursive=False)
                if ul:
                    candidates += ul.find_all("li", class_="type_activity", recursive=False)
    candidates += doc.select("#region-main [role='main'] a[href*='/mod/'][href*='id=']")
    found: dict[str, dict[str, Any]] = {}
    for element in candidates:
        anchor = element if element.name == "a" else element.select_one("a[href*='/mod/']")
        if anchor is None or not anchor.get("href"):
            continue
        url = urlsplit(urljoin(OPAL, anchor["href"]))
        cmid = parse_qs(url.query).get("id", [""])[0]
        match = re.match(r"^/mod/([^/]+)/", url.path)
        module = match.group(1) if match else ""
        label = anchor.select_one(".item-content-wrap, .instancename")
        title = _norm(label.get_text()) if label else _norm(anchor.get_text())
        if not cmid or not module or not title or cmid in found:
            continue
        classes = element.get("class") or []
        found[cmid] = {
            "cmid": cmid,
            "title": title,
            "module": module,
            "type": activity_type(module, title),
            "url": url.geturl(),
            "completed": "mod_completed" in classes or bool(element.select(".mod_completed, .completion-btn-success")),
        }
    return list(found.values())


def parse_nav_branch(value: Any) -> list[dict[str, Any]]:
    children = value.get("children") if isinstance(value, dict) else None
    result = []
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict) or not isinstance(child.get("name"), str) or not isinstance(child.get("link"), str):
            continue
        url = urlsplit(urljoin(OPAL, child["link"].replace("&amp;", "&")))
        cmid = parse_qs(url.query).get("id", [""])[0]
        match = re.match(r"^/mod/([^/]+)/", url.path)
        module, title = (match.group(1) if match else ""), _norm(child["name"])
        if cmid and module and title:
            result.append(
                {
                    "cmid": cmid,
                    "title": title,
                    "module": module,
                    "type": activity_type(module, title),
                    "url": url.geturl(),
                    "completed": "mod_completed" in str(child.get("class", "")),
                }
            )
    return result


def has_booklet(html: str) -> bool:
    return bool(re.search(r"booklet\.(viewbook|tafrit|download)|pls/myopr/booklet|חוברת\s+ה?קורס", html, re.I))


def parse_forums(html: str) -> list[dict[str, Any]]:
    doc = BeautifulSoup(html, "html.parser")
    forums = []
    for item in doc.select("li.forumslist[data-forumid]"):
        title = item.select_one(".forum_list_title a")
        footer = _norm((item.select_one(".forum_footer") or item).get_text())
        unread = item.select_one(".forum_unread .forumlist_number")
        body = item.select_one(".forum_body_content")
        date = item.select_one(".forum_clean_date")
        count = re.search(r"דיונים:\s*(\d+)", footer)
        entry = {
            "id": item.get("data-forumid", ""),
            "title": _norm(title.get_text()) if title else "",
            "description": _norm(body.get_text()) if body else "",
            "discussions": int(count.group(1)) if count else 0,
            "unread": int(_norm(unread.get_text())) if unread and _norm(unread.get_text()).isdigit() else 0,
            "last_update": _norm(date.get_text()) if date else "",
        }
        if entry["id"] and entry["title"]:
            forums.append(entry)
    return forums


def _valid_signed_book_url(value: str) -> str:
    assert_allowed_url(value)
    parts = urlsplit(value)
    if parts.hostname != "opal.openu.ac.il" or parts.path != "/local/ouil_ebook/signedpdf.php":
        raise ProtocolError("OpenU book URL is not allowed")
    return value


def parse_books(html: str, page_url: str = OPAL + "/") -> list[dict[str, Any]]:
    doc = BeautifulSoup(html, "html.parser")
    books = []
    for cell in doc.select(".ebook_cell[id^='ebook']"):
        book_id = cell.get("id", "").removeprefix("ebook")
        if not book_id.isdigit():
            continue

        def text(selector: str) -> str:
            node = cell.select_one(selector)
            return _norm(node.get_text()) if node else ""

        link = cell.select_one("a.ebook_digital_link")
        href = (link.get("href-content") or link.get("href") or "") if link else ""
        file_url = None
        if href and href != "#" and re.search(r"/local/ouil_ebook/signedpdf\.php", href, re.I):
            file_url = _valid_signed_book_url(urljoin(page_url, href))
        book = {
            "id": book_id,
            "title": text(".ebook_name"),
            "part": text(".ebook_part"),
            "subtitle": text(".ebook_subtitle"),
            "requires_consent": file_url is None,
        }
        if file_url:
            book["file_url"] = file_url
        books.append(book)
    return books


def parse_booklet(html: str) -> list[dict[str, str]]:
    doc = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for node in doc.select('a[href*="booklet.download"], frame[src*="booklet.download"], iframe[src*="booklet.download"]'):
        url = node.get("href") or node.get("src") or ""
        match = re.search(r"[?&]in_file_name=([^&]+)", url)
        if not match:
            continue
        from urllib.parse import unquote

        name = unquote(match.group(1))
        if not re.fullmatch(r"[\w.-]+\.pdf", name, re.I) or name in seen:
            continue
        seen.add(name)
        items.append({"title": _norm(node.get_text()) or "חוברת הקורס", "file": name})
    return items


def parse_profile(html: str, profile_url: str = OPAL + "/user/profile.php") -> dict[str, Any]:
    doc = BeautifulSoup(html, "html.parser")

    def name_of(value: str) -> str:
        value = _norm(value)
        value = re.sub(r"^\s*פרופיל\s*:\s*", "", value)
        value = re.sub(r"\s*:\s*פרופיל ציבורי.*$", "", value)
        return re.sub(r"\s*שימו לב!.*$", "", value).strip()

    title = doc.find("title")
    heading = doc.select_one(".page-header-headings h1")
    full_name = (name_of(title.get_text()) if title else "") or (name_of(heading.get_text()) if heading else "")
    id_link = doc.select_one('a[href*="user/edit.php"], a[href*="preferences.php?userid="]')
    id_match = re.search(r"[?&](?:id|userid)=(\d+)", id_link.get("href", "")) if id_link else None
    mail = doc.select_one('a[href^="mailto:"]')
    sections = []
    for card in doc.select(".profile_tree section.node_category"):
        entries = []
        for dt in card.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            entries.append(
                {
                    "label": _norm(dt.get_text()),
                    "value": _norm(dd.get_text()) if dd else "",
                    "links": [{"label": _norm(a.get_text()), "url": a.get("href", "")} for a in (dd.select("a[href]") if dd else [])],
                }
            )
        for a in card.select("li:not(.contentnode) > span > a[href]"):
            label = _norm(a.get_text())
            if label:
                entries.append({"label": "", "value": label, "links": [{"label": label, "url": a.get("href", "")}]})
        h3 = card.find("h3")
        section_title = _norm(h3.get_text()) if h3 else ""
        if section_title or entries:
            sections.append({"title": section_title, "entries": entries})
    return {
        "full_name": full_name,
        "user_id": id_match.group(1) if id_match else "",
        "email": _norm(mail.get_text()) or None if mail else None,
        "profile_url": profile_url,
        "sections": sections,
    }


@dataclass
class Recording:
    title: str
    instructor: str = ""
    date: str = ""
    description: str = ""
    thumbnail: str = ""
    collection: str = ""
    cmid: str | None = None
    playlist_id: str | None = None
    context_id: str | None = None
    mid: str | None = None
    course_db_id: str | None = None


def _safe_thumbnail(value: str) -> str:
    if not value:
        return ""
    url = urljoin(OPAL, value)
    return "" if re.search(r"(?:^|[?&])(?:p_token|token)=", urlsplit(url).query, re.I) else url


def parse_recordings(html: str, cmid: str | None, collection: str) -> list[Recording]:
    doc = BeautifulSoup(html, "html.parser")
    # Read the attributes from the element. A regex such as `cid=...[^>]*mid=` is greedy and
    # matches inside `cmid=`, returning the wrong value.
    link = doc.select_one("#ovc_collections_list li.active .collection_link, li.active > .collection_link") or doc.select_one(".collection_link[cid]")
    context = link.get("cid") if link else None
    mid = link.get("mid") if link else None
    course_db = re.search(r'["\']courseid["\']\s*:\s*["\'](\d+)', html)
    playlist_ids = re.findall(r'id=["\']playlist(\d+)["\']', html)
    result = []
    for index, item in enumerate(doc.select(".ovc_playlist_border")):

        def text(selector: str) -> str:
            node = item.select_one(selector)
            return _norm(node.get_text()) if node else ""

        title = text(".ovc_playlist_title")
        if not title:
            continue
        element_id = item.get("id") or ""
        if not element_id:
            inner = item.select_one('[id^="playlist"]')
            element_id = inner.get("id", "") if inner else ""
        playlist = re.fullmatch(r"playlist(\d+)", element_id or "")
        thumb = item.select_one(".ovc_thumb img")
        result.append(
            Recording(
                title=title,
                instructor=text(".pl_instructor"),
                date=text(".pl_recorddate"),
                description=text(".ovc_descr_short"),
                thumbnail=_safe_thumbnail(thumb.get("src", "") if thumb else ""),
                collection=collection,
                cmid=cmid,
                playlist_id=playlist.group(1) if playlist else (playlist_ids[index] if index < len(playlist_ids) else None),
                context_id=context or None,
                mid=mid or None,
                course_db_id=course_db.group(1) if course_db else None,
            )
        )
    return result


def _clean_name(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value)


# -- client ----------------------------------------------------------------------


class OpenUClient:
    def __init__(self, session: HttpSession | None = None) -> None:
        self.http = session or HttpSession(allowed_hosts=HOSTS)

    # transport -----------------------------------------------------------------

    def _fetch(self, url: str, **kwargs: Any) -> requests.Response:
        response = self.http.get(assert_allowed_url(url), **kwargs)
        if looks_like_login(response.url) or "/login/index.php" in response.url:
            raise SessionExpired("OpenU session expired; sign in again")
        return response

    def _html(self, url: str, *, legacy: bool = False, **kwargs: Any) -> str:
        response = self._fetch(url, **kwargs)
        if not response.ok:
            raise NetworkError(f"OpenU returned HTTP {response.status_code}")
        charset_hint = "windows-1255" in response.headers.get("content-type", "").lower()
        if legacy or charset_hint:
            return response.content.decode("windows-1255", errors="replace")
        return response.text

    # authentication ------------------------------------------------------------

    def is_authenticated(self) -> bool | None:
        """The portal's own session check: ``True``/``False``, or ``None`` when it cannot be read."""
        response = self.http.get(AUTH_API, headers={"Accept": "application/json"})
        if not response.ok:
            return None
        return {"true": True, "false": False}.get(response.text.strip().lower())

    def login(self, username: str, password: str, student_id: str, *, target: str = COURSES_PAGE) -> None:
        """Sign in through the university SSO form. ``target`` is ``T_PLACE`` (see ``docs/openu/authentication.md``)."""
        if not (username and password and student_id):
            raise AuthenticationError("Enter the username, password and student id")
        assert_allowed_url(target)
        self.http.get(COURSES_PAGE)
        self.http.post(SSO_PROCESS, data={"p_user": username, "p_sisma": password, "p_mis_student": student_id, "T_PLACE": target})
        page = self.http.get(target)
        if not page.ok or looks_like_login(page.url, page.text) or self.is_authenticated() is not True:
            doc = BeautifulSoup(page.text, "html.parser")
            reason = doc.select_one("#messageBox, .error, .error-message")
            raise AuthenticationError(_norm(reason.get_text()) if reason else "Open University sign-in did not establish a session")

    # sheilta -------------------------------------------------------------------

    def course_rows(self) -> list[dict[str, Any]]:
        """The raw JSON rows of ``coursesApi/get`` (may contain one-time tokens; do not log)."""
        response = self.http.get(COURSES_API, headers={"Accept": "application/json"})
        if response.status_code == 500:
            # The API sometimes needs its page session refreshed once.
            page = self.http.get(COURSES_PAGE)
            if looks_like_login(page.url):
                raise SessionExpired("OpenU session expired; sign in again")
            response = self.http.get(COURSES_API, headers={"Accept": "application/json"})
        if looks_like_login(response.url):
            raise SessionExpired("OpenU session expired; sign in again")
        body = response.text.strip().lstrip("﻿")
        if not response.ok:
            if self.is_authenticated() is False:
                raise SessionExpired("OpenU session expired; sign in again")
            raise ProtocolError(f"Open University course API returned {response.status_code}")
        if not body.startswith("["):
            if self.is_authenticated() is False:
                raise SessionExpired("OpenU session expired; sign in again")
            raise ProtocolError("Open University course API returned a non-array response")
        return [r for r in json.loads(body) if isinstance(r, dict)]

    def courses(self) -> list[dict[str, Any]]:
        """Every course (current and past) with assignment scores."""
        return [c for c in (parse_course(r) for r in self.course_rows()) if c]

    def grades(self, course: dict[str, Any]) -> list[Grade]:
        return course_grades(course)

    def meetings(self, course: dict[str, Any]) -> list[dict[str, Any]]:
        """Lectures and labs for a course from :meth:`courses` (cancelled meetings are dropped)."""
        params = {
            "course": course["id"],
            "semester": course["raw_term"],
            "studyCenter": course.get("study_center", ""),
            "studyGroup": course.get("study_group", ""),
        }
        meetings = []
        for endpoint in ("getStudyGroupMeetings", "getStudyGroupLabMeetings"):
            response = self.http.get(f"{MEETINGS_API}/{endpoint}", params=params, headers={"Accept": "application/json"})
            if looks_like_login(response.url):
                raise SessionExpired("OpenU session expired; sign in again")
            if not response.ok:
                continue
            try:
                rows = response.json()
            except ValueError:
                continue
            for row in rows if isinstance(rows, list) else []:
                meeting = _map_meeting(course, row)
                if meeting:
                    meetings.append(meeting)
        return meetings

    def exams(self, courses: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        """Confirmed alternative-assessment sittings (one request per course that lists one)."""
        by_key = {(c["id"].lstrip("0"), c["term"]): c for c in (courses if courses is not None else self.courses())}
        summaries = parse_exams(self._html(EXAMS_PAGE))
        exams, seen = [], set()
        for summary in summaries:
            if summary["course_id"] in seen:
                continue
            seen.add(summary["course_id"])
            course = by_key.get((summary["course_id"].lstrip("0"), summary["semester"]))
            params = {
                "p_kurs": summary["course_id"],
                "p_semester": summary["semester"],
                "p_semester_bchina": summary["semester"],
                "P_SUG_CHALUFA": "26",
                "p_KOD_PEILUT_KURS": (course or {}).get("activity_code") or "01",
                "p_sug_rashuma": "GMAR",
            }
            response = self._fetch(f"{EXAM_DETAILS_PAGE}?{urlencode(params)}")
            if not response.ok:
                continue
            html = response.content.decode("windows-1255", errors="replace") if "windows-1255" in response.headers.get("content-type", "").lower() else response.text
            detailed = parse_exam_details(summary, html, response.url)
            if detailed:
                exams.append(detailed)
        return exams

    def assessment_document(self, course_id: str, semester: str, assignment_no: str, artifact: str = "questionnaire", kind: str = "exam") -> requests.Response:
        """Download a scanned exam document. ``artifact``: ``questionnaire`` or ``notebook``; ``kind``: ``exam`` or ``personal``."""
        if artifact not in ("questionnaire", "notebook"):
            raise ValueError("artifact must be 'questionnaire' or 'notebook'")
        hebrew = sheilta_semester(semester)
        params = {
            "course": course_id,
            "semester": hebrew,
            "assignmentNo": assignment_no,
            "assignmentSemester": hebrew,
            "questionnaire": str(artifact == "questionnaire" and kind == "exam").lower(),
            "questionnaireScanned": str(artifact == "questionnaire" and kind == "personal").lower(),
            "examScanned": str(artifact == "notebook").lower(),
        }
        return self._fetch(f"{SHEILTA}/Main/api/coursesApi/downloadScanned", params=params)

    def appeal_url(self, course_id: str, semester: str, assignment_no: str) -> str:
        """The appeal page link of an exam (opening it in a browser submits nothing)."""
        wanted = course_id.lstrip("0")
        rows = [r for r in self.course_rows() if str(r.get("courseId", "")).lstrip("0") == wanted]
        target = normalize_semester(semester)
        row = next((r for r in rows if normalize_term(str(r.get("semester", ""))) == target), rows[0] if rows else None)
        for item in _raw_assessments(row or {}):
            if _assessment_key(item.get("assignmentNo")) == _assessment_key(assignment_no):
                url = _academic_url(item, _APPEAL)
                if url:
                    return assert_allowed_url(url)
        raise ProtocolError("No appeal is available for this exam")

    # opal ----------------------------------------------------------------------

    def resolve_course_site(self, course_id: str, semester: str) -> str | None:
        """Moodle course id, or ``None`` when the course has no Opal site."""
        url = f"{OPAL}/ouil/course.php?course=c{quote(course_id)}&semester={normalize_semester(semester)}"
        response = self._fetch(url)
        found = parse_qs(urlsplit(response.url).query).get("id")
        if found:
            return found[0]
        match = re.search(r"class=[\"'][^\"']*course-(\d+)", response.text)
        return match.group(1) if match else None

    def assignments(self, course: dict[str, Any], moodle_id: str | None | bool = False) -> list[dict[str, Any]]:
        """Merged assignment list. Pass ``moodle_id`` to skip site discovery (``None`` = no site)."""
        site = self.resolve_course_site(course["id"], course["raw_term"] or course["term"]) if moodle_id is False else moodle_id
        opal: list[dict[str, str]] = []
        if site:
            opal = parse_assignment_list(self._html(f"{OPAL}/mod/ouilassign/index.php?id={quote(str(site))}&quicklinks=1"))
        return merge_assessments(opal, course.get("assignments", []))

    def sections(self, moodle_id: str, section_id: str | None = None) -> dict[str, Any]:
        suffix = f"&sectionid={quote(section_id)}&from=nav" if section_id else ""
        html = self._html(f"{OPAL}/course/view.php?id={quote(moodle_id)}{suffix}")
        sections = parse_sections(html)
        current = parse_current_section_id(html)
        activities = parse_activities(html)
        shown = next((s for s in sections if s["id"] == current), None)
        if shown and activities:
            shown["activities"] = activities
        return {"sections": sections, "initial_section_id": current or (sections[0]["id"] if sections else None), "has_booklet": has_booklet(html)}

    def section(self, moodle_id: str, section_id: str) -> dict[str, Any]:
        html = self._html(f"{OPAL}/course/view.php?id={quote(moodle_id)}&sectionid={quote(section_id)}&from=courseHome")
        activities = parse_activities(html, section_id)
        sesskey = re.search(r"[\"']sesskey[\"']\s*:\s*[\"']([^\"']+)", html)
        if not activities and sesskey:
            response = self.http.post(
                f"{OPAL}/lib/ajax/getnavbranch.php",
                data={"elementid": f"expandable_branch_30_{section_id}", "id": section_id, "type": "30", "sesskey": sesskey.group(1)},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
            try:
                activities = parse_nav_branch(response.json())
            except ValueError:
                activities = []
        title = next((s["title"] for s in parse_sections(html) if s["id"] == section_id), "")
        return {"id": section_id, "title": title, "activities": activities}

    def forums(self, moodle_id: str) -> list[dict[str, Any]]:
        return parse_forums(self._html(f"{OPAL}/mod/ouilforum/index.php?id={quote(moodle_id)}&quicklinks=1"))

    def books(self, moodle_id: str) -> list[dict[str, Any]]:
        url = f"{OPAL}/local/ouil_ebook/index.php?id={quote(moodle_id)}&type=digital&quicklinks=1"
        return parse_books(self._html(url), url)

    def book_file(self, moodle_id: str, book_id: str) -> requests.Response:
        """The book PDF. Raises if the student has not accepted the book's terms on the website."""
        book = next((b for b in self.books(moodle_id) if b["id"] == book_id), None)
        if book is None:
            raise ProtocolError("Book was not found in this course")
        if not book.get("file_url"):
            raise ProtocolError("Book terms must be accepted on the website first")
        return self._fetch(_valid_signed_book_url(book["file_url"]))

    def _legacy_page(self, url: str, referer: str | None = None) -> str:
        def get() -> str:
            return self._html(url, legacy=True, headers={"Referer": referer} if referer else None)

        def bounces(html: str) -> bool:
            return "top.location.href" in html and "sso.apps.openu.ac.il/login" in html

        html = get()
        if bounces(html):
            # The hop itself lives on the SSO host; with a live SSO cookie it redirects straight back.
            self.http.get(f"https://sso.apps.openu.ac.il/login?T_PLACE={quote(url, safe='')}")
            html = get()
            if bounces(html):
                raise SessionExpired("OpenU session expired; sign in again")
        return html

    def booklet(self, course_id: str, semester: str) -> dict[str, Any]:
        kurs, term = course_id.lstrip("0"), normalize_semester(semester)
        query = f"p_semester={quote(term)}&p_kurs={quote(kurs)}"
        base = f"{SHEILTA}/pls/myopr"
        view = f"{base}/booklet.viewbook?{query}"
        items = parse_booklet(self._legacy_page(view))
        if not items:
            items = parse_booklet(self._legacy_page(f"{base}/booklet.tafrit?{query}", referer=view))
        return {"course_id": kurs, "semester": term, "items": items}

    def booklet_file(self, course_id: str, semester: str, file: str) -> requests.Response:
        if not re.fullmatch(r"[\w.-]+\.pdf", file, re.I):
            raise ValueError("Booklet file names are plain PDF ids")
        url = (
            f"{SHEILTA}/pls/myopr/booklet.download?in_file_name={quote(file)}"
            f"&p_kurs={quote(course_id.lstrip('0'))}&p_semester={quote(normalize_semester(semester))}"
        )
        return self._fetch(url)

    def profile(self) -> dict[str, Any]:
        url = f"{OPAL}/user/profile.php"
        return parse_profile(self._html(url), url)

    # recordings ----------------------------------------------------------------

    def recording_collections(self, moodle_id: str) -> list[dict[str, Any]]:
        course = BeautifulSoup(self._html(f"{OPAL}/course/view.php?id={quote(moodle_id)}"), "html.parser")
        href = next(
            (
                a.get("href")
                for a in course.find_all("a")
                if "ouilvideocollection" in (a.get("href") or "") or re.search(r"הקלטות|שיעורים מוקלטים", _norm(a.get_text()))
            ),
            None,
        )
        if not href:
            return []
        url = urljoin(OPAL, href)
        url += ("&" if "?" in url else "?") + "quicklinks=1"
        response = self._fetch(url)
        html = response.text
        doc = BeautifulSoup(html, "html.parser")
        collections = [
            {"cmid": el.get("cmid", ""), "title": _norm(el.get_text())}
            for el in doc.select(".collection_link")
            if el.get("cmid") and _norm(el.get_text())
        ]
        if collections:
            active = doc.select_one("#ovc_collections_list li.active .collection_link, li.active > .collection_link")
            cmid = (active.get("cmid") if active else None) or parse_qs(urlsplit(response.url).query).get("id", [None])[0] or collections[0]["cmid"]
            for collection in collections:
                if collection["cmid"] == cmid:
                    recordings = parse_recordings(html, cmid, collection["title"])
                    if recordings:
                        collection["recordings"] = recordings
            return collections
        recordings = parse_recordings(html, None, "הקלטות")
        return [{"cmid": parse_qs(urlsplit(url).query).get("id", ["default"])[0], "title": "הקלטות", "recordings": recordings}] if recordings else []

    def recordings(self, moodle_id: str) -> list[Recording]:
        """All recordings of a course's site (every collection)."""
        result: list[Recording] = []
        for collection in self.recording_collections(moodle_id):
            if "recordings" in collection:
                result += collection["recordings"]
            else:
                html = self._html(f"{OPAL}/mod/ouilvideocollection/view.php?id={quote(collection['cmid'])}")
                result += parse_recordings(html, collection["cmid"], collection["title"])
        return result

    def resolve_stream(self, recording: Recording) -> str:
        """The HLS ``.m3u8`` URL for a recording (needs its context/mid/playlist/course ids)."""
        if not (recording.playlist_id and recording.context_id and recording.mid and recording.course_db_id):
            raise ProtocolError("Recording has no stream hints; rediscover it with recordings()")
        response = self.http.post(
            f"{OPAL}/mod/ouilvideocollection/actions.php",
            data={
                "action": "getplaylist",
                "context": recording.context_id,
                "playlistid": recording.playlist_id,
                "course": recording.course_db_id,
                "mid": recording.mid,
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        if looks_like_login(response.url):
            raise SessionExpired("OpenU session expired; sign in again")
        try:
            cipher = (response.json().get("media") or {}).get("cipher")
        except (ValueError, AttributeError):
            cipher = None
        if not cipher:
            raise ProtocolError("Recording is not playable")
        player = self._html(f"{OPAL}/local/ouil_video/player.php?mediaid={quote(str(cipher))}")
        redirector = re.search(r"[\"']file[\"']\s*:\s*[\"'](https:[^\"']+)", player)
        if not redirector:
            raise ProtocolError("Recording player has no stream source")
        return self._redirector_to_m3u8(redirector.group(1).replace("\\/", "/"))

    def _redirector_to_m3u8(self, url: str) -> str:
        cdn = requests.Session()  # CDN hosts are outside the portal allow-list; checked per hop below
        cdn.headers["User-Agent"] = self.http.headers.get("User-Agent", "scholix")
        target = assert_allowed_url(url, hls=True)
        response = None
        for hop in range(6):
            response = cdn.get(target, allow_redirects=False, headers={"Referer": OPAL + "/"}, timeout=20)
            location = response.headers.get("Location")
            if not location or response.status_code not in (301, 302, 303, 307, 308):
                break
            if hop == 5:
                raise ProtocolError("Too many recording redirects")
            target = assert_allowed_url(urljoin(target, location), hls=True)
        assert response is not None
        body = response.text
        source = re.search(r"source\s+file=[\"']([^\"']+)", body)
        if source:
            return assert_allowed_url(source.group(1).replace("&amp;", "&"), hls=True)
        if body.lstrip().startswith("#EXTM3U") or ".m3u8" in response.url:
            return response.url
        raise ProtocolError("Could not find the recording stream")

    # portal JSON APIs (read-only) ------------------------------------------------

    def _json(self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        response = self.http.get(f"{SHEILTA}{path}", params=params, headers={"Accept": "application/json", **(headers or {})})
        if looks_like_login(response.url):
            raise SessionExpired("OpenU session expired; sign in again")
        if not response.ok:
            raise ProtocolError(f"OpenU portal returned HTTP {response.status_code}; reconnect if the session expired")
        if "json" not in response.headers.get("content-type", "").lower():
            raise ProtocolError("OpenU did not return portal data; the session may have expired")
        return response.json()

    def _csrf(self) -> dict[str, str]:
        response = self.http.get(f"{SHEILTA}/Main/GlobalRequest")
        field = BeautifulSoup(response.text, "html.parser").find("input", {"name": "__RequestVerificationToken"})
        if not response.ok or field is None:
            raise SessionExpired("OpenU session expired; sign in again")
        return {"X-RequestVerificationToken": field["value"]}

    def service_requests(self, status_group: str | None = None) -> list[dict[str, Any]]:
        """All service requests (פניות), history included."""
        rows = self._json("/Main/ServiceRequests/GetApplicationRequestsByUser")
        return [r for r in rows if status_group is None or r.get("StatusGroupId") == status_group]

    def service_request_link(self, application_code: str, application_number: str) -> str:
        """Page URL of one existing request (open it with a browser; it may embed a one-time token)."""
        info = self._json(
            "/Main/ServiceRequests/GetApplicationRequestMoreInfo",
            {"applicationCode": application_code, "applicationNumber": application_number},
        )
        return assert_allowed_url(urljoin(SHEILTA, info["Url"]))

    def service_request_catalog(self, category: str | None = None, query: str | None = None) -> Any:
        if query:
            path, params = "searchRequests", {"freeText": query}
        elif category:
            path, params = "getCategoryData", {"category": category, "hotRequest": ""}
        else:
            path, params = "getUserData", None
        return self._json(f"/Main/api/serviceRequestsApi/{path}", params, self._csrf())

    def service_request_types(self, group: str = "44") -> list[dict[str, Any]]:
        return self._json(f"/Main/api/generalApi/getServiceRequestTypes/{quote(group, safe='')}")

    def service_request_form(self, request_type: str, group: str = "44") -> dict[str, Any]:
        """Dynamic parameters, eligible courses, permissions and attachment rules. Creates nothing."""

        def walk(items: list[dict[str, Any]]):
            for item in items:
                yield item
                yield from walk(item.get("subTypes") or [])

        selected = next((r for r in walk(self.service_request_types(group)) if r.get("code") == request_type), None)
        if selected is None:
            raise ValueError("Unknown request type in this group; use service_request_types()")
        code = str(selected.get("codeToCreate") or selected["code"])
        return {
            "type": selected,
            "params": self._json("/Main/api/generalApi/getGlobalParams", {"requestCode": code}),
            "courses": self._json("/Main/api/generalApi/studentCoursesSpecial", {"requestTypeCode": code}),
            "permissions": self._json(f"/Main/api/serviceRequestsApi/permissions/{quote(code, safe='')}"),
            "file_types": self._json(f"/Main/api/serviceRequestsFilesApi/types/{quote(code, safe='')}"),
            "file_constraints": self._json("/Main/api/serviceRequestsFilesApi/constraints", {"requestType": code}),
        }

    def service_request_files(self, request_id: str) -> Any:
        return self._json(f"/Main/api/serviceRequestsFilesApi/files/{quote(request_id, safe='')}")

    def exam_portal(self, section: str = "exams") -> Any:
        """The new exam/task system. ``section``: permissions, exams, tasks, exams/submissions, tasks/submissions, messages, files/constraints."""
        allowed = {"permissions", "exams", "tasks", "exams/submissions", "tasks/submissions", "messages", "files/constraints"}
        if section not in allowed:
            raise ValueError(f"section must be one of {sorted(allowed)}")
        return self._json(f"/main/api/assignmentsApi/{section}")  # note: lower-case 'main'

    def exam_portal_details(self, semester: str, course_id: str, assignment_id: str, section: str = "details") -> Any:
        if section not in ("details", "state", "questionnaires/metadata"):
            raise ValueError("section must be details, state or questionnaires/metadata")
        path = "/main/api/assignmentsApi/exams/" + "/".join(quote(p, safe="") for p in (semester, course_id, assignment_id))
        return self._json(path + ("" if section == "details" else f"/{section}"))

    def study_programs(self) -> dict[str, Any]:
        data = self._json("/Main/StudyPrograms/GetIndexData")
        return {k: v for k, v in data.items() if k not in ("Menu", "CurrentUser", "ActorUser")}

    def study_program_search(self, subject_codes: str | None = None) -> Any:
        if subject_codes:
            return self._json("/Main/StudyPrograms/SearchProgramsBySubSubjectID", {"subSubjectId": subject_codes})
        return self._json("/Main/StudyPrograms/GetSearchItems")

    def study_program_xml(self, program_code: str, program_id: str = "", parent_code: str = "") -> Any:
        return self._json(
            "/Main/StudyPrograms/GetProgramXML",
            {"programCode": program_code, "programId": program_id, "parentCode": parent_code, "status": "3" if program_id else "1"},
        )

    def study_program_courses(self, resource: str, **args: str) -> Any:
        routes = {
            "courses": ("GetCourses", {"subjectIds": args.get("subject_ids", ""), "levelIds": args.get("level_ids", ""), "freeText": args.get("query", ""), "mindExpanding": args.get("mind_expanding", "false")}),
            "departments": ("GetAllDepartments", {}),
            "branches": ("GetKnowledgeBranchs", {}),
            "subjects": ("GetSubjects", {"knowledgeBranch": args.get("knowledge_branch", "")}),
            "levels": ("GetLevels", {"subjectId": args.get("subject_id", "")}),
            "course": ("GetCourseById", {"courseId": args.get("course_id", ""), "isAdditional": "true"}),
        }
        if resource not in routes:
            raise ValueError(f"resource must be one of {sorted(routes)}")
        name, params = routes[resource]
        return self._json(f"/Main/StudyPrograms/{name}", params)


def registration_search_url(semester: str, **filters: str) -> str:
    """URL of the registration search results page (an HTML page; fetch and parse it, or open it in a browser).

    Keyword filters: ``query``, ``course_id``, ``degree``, ``subject``, ``language``, ``level``, ``credits``,
    ``department``, ``area``, ``center``, ``hours``, ``days``, ``instruction_type``, ``page``.
    List-type filters are comma-wrapped (``,5,``); the value ``all`` removes the filter.
    """
    names = {
        "query": "CourseName", "degree": "Degree", "subject": "Subject", "language": "LearningLanguage",
        "level": "Openinglevel", "credits": "Credits", "department": "AD", "area": "GeographicalArea",
        "center": "LearningCenter", "hours": "InstructionHours", "days": "Days",
        "instruction_type": "InstructionType", "page": "Page",
    }
    params = {"Degree": "0", "GeographicalArea": ",01,02,03,04,05,06,07,09,", "InstructionHours": "00:01", "Days": "111111", "Page": "1", "Semester": semester}
    for key, value in filters.items():
        if key == "course_id":
            params["CourseIDs"] = f",{value},"
        elif key in names:
            params[names[key]] = str(value)
        else:
            raise TypeError(f"Unknown filter: {key}")
    for key in ("subject", "language", "level", "credits", "department", "area", "center"):
        if key in filters:
            value = str(filters[key]).strip(",")
            if key == "area" and value == "99":
                params["GeographicalArea"] = ",01,02,03,04,05,06,07,09,"
            elif value == "all":
                params.pop(names[key], None)
            else:
                params[names[key]] = f",{value},"
    return f"{SHEILTA}/CoursesRegistration/Pages/GroupSearchResults.aspx?{urlencode(params)}"


def _normalize_hour(value: Any) -> str:
    match = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)", str(value or "").strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def _map_meeting(course: dict[str, Any], value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("isCanceled") is True:
        return None
    date = str(value.get("meetingDate") or "")[:10]
    start, end = _normalize_hour(value.get("fromHour")), _normalize_hour(value.get("toHour"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not start or not end:
        return None
    return {
        "course_id": course["id"],
        "course_name": course["name"],
        "semester": course["term"],
        "study_group": _norm(str(value.get("studyGroup") or course.get("study_group") or "")),
        "type": _norm(str(value.get("typeDescription") or "")),
        "date": date,
        "from_hour": start,
        "to_hour": end,
        "location": _norm(str(value.get("position") or "")),
        "online": bool(value.get("onlineMeeting")),
    }
