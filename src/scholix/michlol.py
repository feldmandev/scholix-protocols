"""Michlol (מכלול) student portal API, as deployed for the Bar-Ilan mathematics department.

See ``docs/michlol.md``. Michlol is a commercial product that other institutions
run under their own host; only ``biumath.michlol4.co.il`` has been exercised.

Example::

    from scholix.michlol import MichlolClient

    client = MichlolClient()
    client.login("student-id", "password")
    for course in client.courses():
        print(course["name"], course["year"])
    for grade in client.grades():
        print(grade.subject, grade.name, grade.value)
"""

from __future__ import annotations

from typing import Any

from ._http import HttpSession, json_body
from .errors import AuthenticationError, ProtocolError, SessionExpired
from .models import Grade

DEFAULT_BASE = "https://biumath.michlol4.co.il"
_DEVICE = '{"isMobile":true,"os":"Android","browser":"Chrome","cookies":true}'


class MichlolClient:
    def __init__(self, base_url: str = DEFAULT_BASE, session: HttpSession | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = session or HttpSession()
        self.token = ""
        self.display_name = ""
        #: Raw ``pagedata`` response header of the last grades request (needed to download scanned exams).
        self.page_data = ""
        self._last: Any = None

    def _post(self, path: str, body: dict[str, Any] | list[Any] | None, *, auth: bool = True) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        response = self.http.post(f"{self.base_url}/api/{path}", json={} if body is None else body, headers=headers)
        if response.status_code in (401, 403):
            raise SessionExpired("Michlol session expired")
        if not response.ok:
            raise ProtocolError(f"Michlol {path} failed (HTTP {response.status_code})")
        self._last = response
        return json_body(response, f"Michlol {path}")

    def login(self, student_id: str, password: str) -> None:
        body = self._post(
            "Login/Login",
            {
                "captchaToken": None,
                "loginType": "student",
                "password": password,
                "zht": student_id,
                "deviceDataJson": _DEVICE,
            },
            auth=False,
        )
        if not isinstance(body, dict) or not body.get("success") or not body.get("token"):
            raise AuthenticationError(str(body.get("errorDescription") or "Michlol rejected the sign-in") if isinstance(body, dict) else "Michlol rejected the sign-in")
        self.token = body["token"]
        info = self._post("Account/UserInfo", {}).get("userInfo") or {}
        self.display_name = f"{info.get('smp', '')} {info.get('smm', '')}".strip()

    def courses(self) -> list[dict[str, Any]]:
        """Enrolled courses: ``name``, ``year``, ``semester`` and the lecturers' names."""
        body = self._post("StudentCourses/Data", {"urlParameters": {}})
        rows = ((body.get("courses") or {}).get("clientData")) or []
        courses = []
        for row in rows:
            # ``all_pms_shm`` lists the lecturers with the word order reversed (RTL storage).
            lecturers = " ".join(reversed(str(row.get("all_pms_shm", "")).split()))
            courses.append(
                {
                    "name": row.get("krs_shm", ""),
                    "year": row.get("krs_snl", ""),
                    "semester": row.get("krs_sms_select", ""),
                    "teacher": lecturers,
                    "raw": row,
                }
            )
        return courses

    def grades(self) -> list[Grade]:
        """All grades, flattened. Each ``Grade.raw['submissions']`` lists the exam sittings (*moedim*)."""
        body = self._post("Grades/Data", {"urlParameters": []})
        self.page_data = self._last.headers.get("pagedata", "")
        result = []
        for course in (body.get("collapsedCourses") or {}).get("clientData") or []:
            for item in course.get("__body") or []:
                sittings = []
                for moed in item.get("__body") or []:
                    scan = next(
                        (b.get("routeData") for b in moed.get("__buttons") or [] if b.get("description") == "בחינה סרוקה"),
                        None,
                    )
                    sittings.append(
                        {
                            "type": moed.get("zin_sug"),
                            "grade": moed.get("moed_1_zin"),
                            "date": moed.get("krs_snl"),
                            "scan": scan,
                        }
                    )
                result.append(
                    Grade(
                        subject=course.get("krs_shm", ""),
                        name=item.get("krs_shm", ""),
                        value=item.get("bhnzin"),
                        date=item.get("krs_snl", ""),
                        raw={**item, "submissions": sittings},
                    )
                )
        return result
