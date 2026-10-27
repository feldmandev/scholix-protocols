"""Ministry of Education Students & Graduates Portal - matriculation (bagrut) grades.

See ``docs/education-portal.md``. **This client cannot sign in.** The identity
provider sits behind F5 bot defense for this portal, so the session must come
from a real browser sign-in. Pass in the resulting ``Cookie`` header and the
``csrt`` token (both visible in the browser's network tab)::

    from scholix.eduportal import EducationPortalClient

    portal = EducationPortalClient(cookie_header="a=b; c=d", csrt="123456789")
    print(portal.user_info())
    for grade in portal.bagrut_grades():
        print(grade.subject, grade.name, grade.value)
"""

from __future__ import annotations

import json
import re
from typing import Any

from ._http import HttpSession
from .errors import ProtocolError, SessionExpired
from .models import Grade

BASE = "https://students.education.gov.il"
API = f"{BASE}/api/"
GRADES_PAGE = f"{BASE}/matriculation-exams/grades-page"

#: Where a person starts a browser sign-in; ``ReturnUrl`` brings the browser back to the portal.
LOGIN_URL = f"https://apps2.education.gov.il/EduLogin/login.aspx?ReturnUrl={GRADES_PAGE}"

_GRADE_FIELDS = ("TZIYUN_SOFI", "TZIYUN_BCHINA", "TZIYUN_T", "TZIYUN_M", "TZIYUN_BEIT_SEFER")


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _grade_value(row: dict[str, Any]) -> str:
    """First meaningful value among the several ``TZIYUN_*`` fields (0 and blank are skipped)."""
    for key in _GRADE_FIELDS:
        text = _text(row, key)
        if text and text != "0":
            return text
    return ""


def _to_grade(row: dict[str, Any]) -> Grade:
    moed = _text(row, "TEUR_CODE_MOED") or _text(row, "TEUR_MOED")
    year = _text(row, "TEUR_SHANA") or _text(row, "SHANA")
    when = " ".join(p for p in (moed, year) if p)
    form = _text(row, "SHEM_SHEELON")
    return Grade(
        subject=_text(row, "SHEM_MIKTZOA") or _text(row, "SHEM_MIKZOA") or "בגרות",
        name=" · ".join(p for p in (form, when) if p) or "בגרות",
        value=_grade_value(row) or None,
        date=when,
        raw=row,
    )


class EducationPortalClient:
    def __init__(self, cookie_header: str, csrt: str | None = None, session: HttpSession | None = None) -> None:
        self.http = session or HttpSession(allowed_hosts={"students.education.gov.il"})
        self.cookie_header = cookie_header
        self.csrt = csrt

    def _fetch(self, url: str) -> str | None:
        response = self.http.get(
            url,
            headers={
                "Cookie": self.cookie_header,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*",
                "Referer": GRADES_PAGE,
            },
            allow_redirects=False,
        )
        # A redirect means the session lapsed and the portal is sending us to its sign-in page.
        return response.text if response.status_code == 200 else None

    def refresh_csrt(self) -> str | None:
        """Re-scrape ``csrt`` from a portal page; the API answers 404 without a current one."""
        for url in (GRADES_PAGE, BASE + "/"):
            body = self._fetch(url)
            match = re.search(r"csrt=([0-9]{6,})", body or "")
            if match:
                self.csrt = match.group(1)
                return self.csrt
        return self.csrt

    def _api(self, endpoint: str) -> str:
        sep = "&" if "?" in endpoint else "?"
        token = self.csrt or self.refresh_csrt()
        if not token:
            raise SessionExpired("No csrt token; sign in with a browser and capture it")
        body = self._fetch(f"{API}{endpoint}{sep}csrt={token}")
        if body is None:
            # A stale csrt makes the API 404; retry once with a freshly scraped one.
            fresh = self.refresh_csrt()
            body = self._fetch(f"{API}{endpoint}{sep}csrt={fresh}") if fresh and fresh != token else None
        if body is None:
            raise SessionExpired("The portal rejected the session. Sign in with a browser again.")
        return body

    def user_info(self) -> dict[str, Any]:
        """``public/getuserinfo``. ``TZehut`` is the national ID; ``-1`` or less means not signed in."""
        try:
            info = json.loads(self._api("public/getuserinfo"))
        except ValueError:
            raise SessionExpired("The portal returned a sign-in page instead of JSON") from None
        tzehut = info.get("TZehut", -1) if isinstance(info, dict) else -1
        if not isinstance(tzehut, (int, float)) or tzehut <= -1:
            raise SessionExpired("The portal session is not authenticated")
        return info

    def bagrut_grades(self) -> list[Grade]:
        """Matriculation grades.

        Reads the exam-system codes from ``bgr/fullgradespage`` (``hasmachot[].CODE_MAARECHET``,
        excluding ``1``) and drills into ``bgr/getgrades/<codes>``; with no such codes it falls
        back to the page's own per-subject summary (``miktzoa``).
        """
        try:
            page = json.loads(self._api("bgr/fullgradespage"))
        except ValueError:
            raise SessionExpired("The portal session expired (HTML returned instead of JSON)") from None
        if not isinstance(page, dict):
            raise ProtocolError("Unexpected grades page shape")
        codes: list[str] = []
        for entry in page.get("hasmachot") or []:
            code = _text(entry, "CODE_MAARECHET")
            if code and code != "1" and code not in codes:
                codes.append(code)
        rows: list[dict[str, Any]] = []
        if codes:
            try:
                drilled = json.loads(self._api("bgr/getgrades/" + "-".join(codes)))
                rows = [r for r in drilled if isinstance(r, dict)] if isinstance(drilled, list) else []
            except (ValueError, SessionExpired):
                rows = []  # fall back to the summary rather than failing the whole request
        if not rows:
            rows = [r for r in page.get("miktzoa") or [] if isinstance(r, dict)]
        return [_to_grade(r) for r in rows]
