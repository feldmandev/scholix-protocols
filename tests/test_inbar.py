"""Inbar parsing and flow, against synthetic Web Forms pages (no real student data)."""

import re

import pytest

from conftest import Reply, body_of
from scholix import inbar
from scholix._http import HttpSession
from scholix.errors import AuthenticationError, ProtocolError, RateLimited, SessionExpired

TARGET = "ctl00$ContentPlaceHolder1$gvGradesList"

LOGIN_PAGE = """<form id="form1" action="/Live/Login.aspx?ReturnUrl=%2fLive%2fmain.aspx">
<input type="hidden" name="__PageDataKey" value="login-state"><input type="hidden" name="__VIEWSTATE" value="">
<input type="hidden" name="__EVENTVALIDATION" value="login-validation">
<input name="edtUsername"><input name="edtMobile"><input type="submit" name="btnLogin" value="Enter">
<input type="submit" name="language" value="EN"></form>"""


def challenge(state="sms-state"):
    return f"""<form id="form1"
action="/Live/Authenticate.aspx?ReturnUrl=%2fLive%2fmain.aspx&amp;AuthLevel=SmsAndNormal">
<input type="hidden" name="__PageDataKey" value="{state}">
<input type="hidden" name="__EVENTVALIDATION" value="sms-validation">
<input name="edtCode"><input type="submit" name="btnVerify" value="Continue">
<input type="submit" name="btnSendSmsCode" value="Resend" disabled>
<input type="submit" name="btnLogout" value="Back"></form>"""


def grades_page(year=2026, rows=None, pager=None):
    body = rows if rows is not None else """
<tr><td>sample-01</td><td>Sample algebra</td><td>Lecturer</td><td>סמסטר קיץ</td><td>4</td><td>60</td>
<td><span id="ContentPlaceHolder1_gvGradesList_lblRowFinalGrade_0">73</span></td><td>01/09/2026</td><td></td>
<td><div style="display:none"><table><tr class="AssignmentHeader"><td></td><td>Weight</td><td>Grade</td></tr>
<tr class="AssignmentText"><td>Exam</td><td>85</td><td>70</td></tr>
<tr><td colspan="2">01/08/2026 (A)</td><td>70</td></tr>
<tr class="AssignmentText"><td>Homework</td><td>15</td><td>90</td></tr></table></div></td><td></td></tr>
<tr><td>sample-02</td><td>Pending course</td><td>Lecturer</td><td>סמסטר א'</td><td>2</td><td>60</td>
<td><span id="ContentPlaceHolder1_gvGradesList_lblRowFinalGrade_1"></span></td>
<td></td><td></td><td></td><td></td></tr>"""
    return f"""<form id="form1" action="/Live/StudentGradesList.aspx">
<input type="hidden" name="__PageDataKey" value="grades-state-{year}">
<input type="hidden" name="__EVENTVALIDATION" value="grades-validation">
<input type="hidden" name="__EVENTTARGET" value=""><input type="hidden" name="__EVENTARGUMENT" value="">
<select id="cmbActiveYear" name="ctl00$cmbActiveYear">
<option value="2026" {"selected" if year == 2026 else ""}>2026</option>
<option value="2027" {"selected" if year == 2027 else ""}>2027</option></select>
<table id="ContentPlaceHolder1_gvGradesList"><tbody><tr>
<th>קוד קבוצת קורס</th><th>שם</th><th>מרצה</th><th>תקופה</th><th>נ"ז</th><th>ציון עובר</th>
<th>ציון סופי</th><th>ת.עדכון</th><th>הערה</th><th>מטלות</th><th>בקשה לציון עובר</th></tr>
{body}{pager or ""}</tbody></table></form>"""


def pager(current):
    links = "".join(
        f"<td><span>{n}</span></td>" if n == current
        else f"<td><a href=\"javascript:__doPostBack('{TARGET}','Page${n}')\">{n}</a></td>"
        for n in (1, 2)
    )
    return f"<tr><td colspan='11'><table><tr>{links}</tr></table></td></tr>"


ROW_1 = grades_page().split("<tbody>")[1].split("</tbody>")[0].split("</tr>", 1)[1].split("<tr><td>sample-02")[0]


def paged(page):
    first = re.search(r"<tr><td>sample-01.*?</tr></table></div></td><td></td></tr>", grades_page(), re.S).group(0)
    second = re.search(r"<tr><td>sample-02.*?</tr>", grades_page(), re.S).group(0)
    return grades_page(rows=first if page == 1 else second, pager=pager(page))


def routes(fake, *pages_after_verify):
    fake.add("GET", "/Live/Login.aspx", Reply(LOGIN_PAGE))
    fake.add("POST", "/Live/Login.aspx", Reply(challenge()))
    fake.add("POST", "/Live/Authenticate.aspx", Reply("<html>Signed in</html>"))
    seq = iter(pages_after_verify)
    fake.add("GET", "/Live/StudentGradesList.aspx", lambda r: Reply(next(seq)))


def client(mount):
    return inbar.InbarClient(mount(HttpSession(allowed_hosts={inbar.HOST})))


def test_sms_sign_in_posts_only_the_form_state_and_the_chosen_button(fake, mount):
    routes(fake, grades_page())
    c = client(mount)
    c.request_sms("test-passport", "0501234567")
    page = c.verify_sms("12345")
    assert page.year == 2026

    login_post = body_of(fake.requests[1])
    assert login_post["edtUsername"] == "test-passport" and login_post["btnLogin"] == "Enter"
    assert "language" not in login_post  # other buttons are not 'successful' controls

    verify = fake.requests[2]
    # requests upper-cases percent-escapes (%2f -> %2F), which is equivalent under RFC 3986.
    assert verify.url.lower().endswith("/live/authenticate.aspx?returnurl=%2flive%2fmain.aspx&authlevel=smsandnormal")
    sent = body_of(verify)
    assert sent == {"__PageDataKey": "sms-state", "__EVENTVALIDATION": "sms-validation", "edtCode": "12345", "btnVerify": "Continue"}


def test_wrong_code_keeps_the_challenge_for_a_retry(fake, mount):
    fake.add("GET", "/Live/Login.aspx", Reply(LOGIN_PAGE))
    fake.add("POST", "/Live/Login.aspx", Reply(challenge()))
    answers = iter([Reply(challenge("retry-state")), Reply("<html>ok</html>")])
    fake.add("POST", "/Live/Authenticate.aspx", lambda r: next(answers))
    fake.add("GET", "/Live/StudentGradesList.aspx", Reply(grades_page()))
    c = client(mount)
    c.request_sms("x", "y")
    with pytest.raises(AuthenticationError):
        c.verify_sms("11111")
    c.verify_sms("22222")
    assert body_of(fake.requests[-2])["__PageDataKey"] == "retry-state"


def test_sms_restriction_alert_is_reported_and_stops(fake, mount):
    restricted = challenge() + "<script>function f(){ window.alert('לא ניתן לשלוח עוד הודעות/לאמת קוד בשלב זה , יש לנסות מאוחר יותר'); }</script>"
    fake.add("GET", "/Live/Login.aspx", Reply(LOGIN_PAGE))
    fake.add("POST", "/Live/Login.aspx", Reply(restricted))
    c = client(mount)
    with pytest.raises(RateLimited):
        c.request_sms("x", "y")
    with pytest.raises(AuthenticationError):  # no challenge was kept; nothing more is submitted
        c.verify_sms("12345")
    assert len(fake.requests) == 2


def test_resend_respects_cooldown(fake, mount):
    fake.add("GET", "/Live/Login.aspx", Reply(LOGIN_PAGE))
    fake.add("POST", "/Live/Login.aspx", Reply(challenge()))
    c = client(mount)
    c.request_sms("x", "y")
    with pytest.raises(RateLimited):
        c.resend_sms()


def test_expired_session_redirect_is_not_followed_into_login(fake, mount):
    fake.add("GET", "/Live/StudentGradesList.aspx", Reply("", status=302, headers={"Location": "/Live/Login.aspx?ReturnUrl=x"}))
    with pytest.raises(SessionExpired):
        client(mount).grades()
    assert [r.url.split("/Live/")[1] for r in fake.requests] == ["StudentGradesList.aspx"]


def test_redirect_off_portal_is_refused(fake, mount):
    fake.add("GET", "/Live/Login.aspx", Reply("", status=302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(ProtocolError):
        client(mount).request_sms("x", "y")


def test_grade_parsing_courses_assignments_and_final():
    page = inbar.parse_grades(grades_page())
    assert (page.year, page.years) == (2026, [2026, 2027])
    algebra, pending = page.courses
    assert (algebra.id, algebra.key, algebra.semester, algebra.credits) == ("sample-01", "2026:sample-01", "c", "4")
    assert [(g.name, g.value) for g in algebra.grades] == [("Exam", "70"), ("Homework", "90"), ("Final grade", "73")]
    assert algebra.grades[0].weight == 85 and algebra.grades[0].date == "01/08/2026"
    assert algebra.grades[0].raw["submissions"] == [{"type": "01/08/2026 (A)", "date": "01/08/2026 (A)", "grade": "70"}]
    assert pending.grades == [] and pending.semester == "a"  # blank final grade stays pending


@pytest.mark.parametrize("row", ["<tr><td colspan='11'>אין נתונים</td></tr>", "<tr><td colspan='11'>No records</td></tr>", "<tr><td colspan='11'>&nbsp;</td></tr>"])
def test_empty_year_keeps_year_choices(row):
    page = inbar.parse_grades(grades_page(2027, rows=row))
    assert page.year == 2027 and page.years == [2026, 2027] and page.courses == []


def test_reordered_and_missing_optional_columns_still_map_by_header():
    from bs4 import BeautifulSoup

    doc = BeautifulSoup(grades_page(), "html.parser")
    for row in doc.select("#ContentPlaceHolder1_gvGradesList > tbody > tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        cells[-1].extract()  # the optional pass-request column is not always displayed
        final = cells[6]  # ...and the final grade moves in front of the course code
        final.extract()
        cells[0].insert_before(final)
    course = inbar.parse_grades(str(doc)).courses[0]
    assert (course.id, course.name, course.teacher) == ("sample-01", "Sample algebra", "Lecturer")
    assert course.grades[-1].value == "73"


def test_unknown_or_truncated_rows_fail_instead_of_returning_wrong_data():
    with pytest.raises(inbar.InbarLayoutChanged):
        inbar.parse_grades(grades_page(2027, rows="<tr><td colspan='11'>Unexpected portal response</td></tr>"))
    with pytest.raises(inbar.InbarLayoutChanged):
        inbar.parse_grades(grades_page().replace("<th>שם</th>", "<th>Unknown</th>"))
    truncated = grades_page().replace("<td></td><td></td><td></td><td></td></tr>", "<td></td></tr>")
    with pytest.raises(inbar.InbarLayoutChanged) as error:
        inbar.parse_grades(truncated)
    details = error.value.details
    assert set(details) == {"year", "headers", "cells", "colspans"}  # counts and headers only, never cell text
    assert details["cells"] < len(details["headers"])


def test_login_page_means_expired_session():
    with pytest.raises(SessionExpired):
        inbar.parse_grades("<html><form>login</form></html>")


def test_pagination_collects_every_page_with_fresh_postback_state(fake, mount):
    seq = iter([paged(1), paged(2)])
    fake.add("GET", "/Live/StudentGradesList.aspx", lambda r: Reply(next(seq)))
    fake.add("POST", "/Live/StudentGradesList.aspx", lambda r: Reply(paged(2)))
    result = client(mount).grades()
    assert [c.id for c in result.courses] == ["sample-01", "sample-02"]
    post = body_of(fake.requests[1])
    assert post["__EVENTTARGET"] == TARGET and post["__EVENTARGUMENT"] == "Page$2"
    assert post["__PageDataKey"] == "grades-state-2026"


def test_repeated_pager_page_fails_without_partial_results(fake, mount):
    fake.add("GET", "/Live/StudentGradesList.aspx", Reply(paged(1)))
    fake.add("POST", "/Live/StudentGradesList.aspx", Reply(paged(1)))
    with pytest.raises(ProtocolError):
        client(mount).grades()


def test_year_switch_posts_the_selector_without_a_second_get(fake, mount):
    answers = iter([grades_page(2026)])
    fake.add("GET", "/Live/StudentGradesList.aspx", lambda r: Reply(next(answers)))
    fake.add("POST", "/Live/StudentGradesList.aspx", Reply(grades_page(2027, rows="<tr><td colspan='11'>אין נתונים</td></tr>")))
    result = client(mount).grades(2027)
    post = body_of(fake.requests[-1])
    assert post["ctl00$cmbActiveYear"] == "2027" and post["__EVENTTARGET"] == "ctl00$cmbActiveYear"
    assert result.year == 2027 and result.courses == []


def test_unavailable_year_is_refused(fake, mount):
    fake.add("GET", "/Live/StudentGradesList.aspx", Reply(grades_page(2026)))
    with pytest.raises(ProtocolError):
        client(mount).grades(1999)
    assert len(fake.requests) == 1


def test_teaching_groups_are_combined_across_pages(fake, mount):
    second = paged(2).replace("Pending course", "Sample algebra").replace("סמסטר א'", "סמסטר קיץ")
    seq = iter([paged(1)])
    fake.add("GET", "/Live/StudentGradesList.aspx", lambda r: Reply(next(seq)))
    fake.add("POST", "/Live/StudentGradesList.aspx", Reply(second))
    (course,) = client(mount).grades().courses
    assert course.id == "sample-01" and course.related_groups[0]["id"] == "sample-02"


# -- schedule ---------------------------------------------------------------------


def schedule_row(day="יום א'", time="09:00 - 11:00", code="sample-01"):
    return f"""<tr><td>{day}</td><td>{time}</td><td>Sample course</td><td>{code}</td><td>Semester A</td>
<td>2</td><td>3</td><td>2</td><td>Lecturer</td><td>Building 1 / Room 2</td><td>Projector</td>
<td></td><td>Lecture</td></tr>"""


def schedule_page(year=2027, period="1", rows=""):
    empty = "<tr><td>אין נתונים</td>" + "<td></td>" * 12 + "</tr>"
    return f"""<form id="form1" action="/Live/StudentPeriodSchedule.aspx">
<input type="hidden" name="__PageDataKey" value="s-{period}"><input type="hidden" name="__EVENTTARGET" value="">
<select id="cmbActiveYear" name="ctl00$cmbActiveYear">
<option value="2027" {"selected" if year == 2027 else ""}>2027</option><option value="2026" {"selected" if year == 2026 else ""}>2026</option></select>
<select id="tbMain_ctl03_ddlPeriodTypeFilter2" name="ctl00$tbMain$ctl03$ddlPeriodTypeFilter2">
<option value="1" {"selected" if period == "1" else ""}>Semester A</option><option value="2" {"selected" if period == "2" else ""}>Semester B</option>
<option value="3" {"selected" if period == "3" else ""}>Summer</option><option value="5" {"selected" if period == "5" else ""}>All semesters</option></select>
<table id="ContentPlaceHolder1_PeriodScheduleA_gvPeriodSchedule"><tr>
<th>יום</th><th>שעה</th><th>שם קבוצת קורס</th><th>קוד קבוצת קורס</th><th>תקופה</th><th>שעות</th>
<th>נ"ז</th><th>ש"ש</th><th>מרצה</th><th>חדר</th><th>עזרים</th><th>סילבוס</th><th>סוג מפגש</th></tr>
{rows or empty}</table></form>"""


def test_empty_schedule_still_reports_filters():
    page = inbar.parse_schedule(schedule_page())
    assert (page.year, page.years, page.period, list(page.periods)) == (2027, [2027, 2026], "1", ["1", "2", "3", "5"])
    assert page.lessons == []


def test_schedule_rows_become_lessons():
    (lesson,) = inbar.parse_schedule(schedule_page(rows=schedule_row())).lessons
    assert (lesson.day, lesson.start, lesson.end) == (0, "09:00", "11:00")
    assert (lesson.subject, lesson.teacher, lesson.room) == ("Sample course", "Lecturer", "Building 1 / Room 2")
    assert lesson.raw["course_code"] == "sample-01" and lesson.raw["credits"] == "3" and lesson.raw["meeting_type"] == "Lecture"


def test_schedule_weekdays_reversed_times_and_concurrent_courses():
    rows = (
        schedule_row("יום ג׳", "11:00–09:00", "sample-02")
        + schedule_row("ראשון", "9:00 - 11:00", "sample-01")
        + schedule_row("ראשון", "9:00 - 10:00", "sample-03")
        + schedule_row("שבת", "12:00 - 14:00", "sample-04")
    )
    lessons = inbar.parse_schedule(schedule_page(rows=rows)).lessons
    assert [l.day for l in lessons] == [0, 0, 2, 6]
    assert [(l.start, l.end) for l in lessons][2] == ("09:00", "11:00")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.replace("<th>חדר</th>", "<th>Room</th>"),
        lambda h: h.replace("09:00 - 11:00", "no time"),
        lambda h: h.replace("יום א'", "מחר"),
        lambda h: h.replace("09:00 - 11:00", "25:00 - 26:00"),
    ],
)
def test_schedule_layout_changes_are_errors_not_empty_schedules(mutate):
    with pytest.raises(inbar.InbarLayoutChanged):
        inbar.parse_schedule(mutate(schedule_page(rows=schedule_row())))


def test_schedule_filters_post_latest_form_state(fake, mount):
    seq = iter([schedule_page(2027, "1")])
    fake.add("GET", "/Live/StudentPeriodSchedule.aspx", lambda r: Reply(next(seq)))
    answers = iter([schedule_page(2026, "1"), schedule_page(2026, "3", rows=schedule_row())])
    fake.add("POST", "/Live/StudentPeriodSchedule.aspx", lambda r: Reply(next(answers)))
    result = client(mount).schedule(year=2026, period="3")
    year_post, period_post = body_of(fake.requests[1]), body_of(fake.requests[2])
    assert year_post["ctl00$cmbActiveYear"] == "2026"
    assert period_post["ctl00$tbMain$ctl03$ddlPeriodTypeFilter2"] == "3" and period_post["__PageDataKey"] == "s-1"
    assert (result.year, result.period, len(result.lessons)) == (2026, "3", 1)
