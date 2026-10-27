"""Education Portal (bagrut) and OpenU - synthetic payloads only."""

import json

import pytest

from conftest import Reply, body_of
from scholix._http import HttpSession
from scholix.eduportal import EducationPortalClient
from scholix.errors import AuthenticationError, SessionExpired
from scholix.openu import OpenUClient, course_grades, normalize_term, parse_course


def portal(mount):
    return EducationPortalClient("sid=abc", "111111", session=mount(HttpSession()))


def test_portal_sends_cookie_and_csrt_and_reads_user(fake, mount):
    fake.add("GET", "/api/public/getuserinfo", Reply({"TZehut": 123456789, "FirstName": "A"}))
    assert portal(mount).user_info()["FirstName"] == "A"
    request = fake.requests[0]
    assert request.headers["Cookie"] == "sid=abc" and "csrt=111111" in request.url


def test_portal_unauthenticated_and_html_login_page(fake, mount):
    fake.add("GET", "/api/public/getuserinfo", Reply({"TZehut": -1}))
    with pytest.raises(SessionExpired):
        portal(mount).user_info()
    fake.routes.clear()
    fake.add("GET", "/api/public/getuserinfo", Reply("<html>login</html>"))
    with pytest.raises(SessionExpired):
        portal(mount).user_info()


def test_portal_redirect_means_session_lapsed(fake, mount):
    fake.add("GET", "/api/public/getuserinfo", Reply("", status=302, headers={"Location": "https://apps2.education.gov.il/x"}))
    fake.add("GET", "/matriculation-exams/grades-page", Reply("", status=302))
    fake.add("GET", "/", Reply("no token here"))
    with pytest.raises(SessionExpired):
        portal(mount).user_info()


def test_portal_stale_csrt_is_refreshed_once(fake, mount):
    answers = iter([Reply("", status=404), Reply({"TZehut": 5})])
    fake.add("GET", "/api/public/getuserinfo", lambda r: next(answers))
    fake.add("GET", "/matriculation-exams/grades-page", Reply("<a href='x?csrt=222222'>"))
    client = portal(mount)
    assert client.user_info()["TZehut"] == 5
    assert client.csrt == "222222" and "csrt=222222" in fake.requests[-1].url


def test_bagrut_grades_drill_down_and_field_precedence(fake, mount):
    fake.add(
        "GET",
        "/api/bgr/fullgradespage",
        Reply(
            {
                "hasmachot": [{"CODE_MAARECHET": 1}, {"CODE_MAARECHET": 7}, {"CODE_MAARECHET": 9}, {"CODE_MAARECHET": 7}],
                "miktzoa": [{"SHEM_MIKTZOA": "summary"}],
            }
        ),
    )
    fake.add(
        "GET",
        "/api/bgr/getgrades/7-9",
        Reply(
            [
                {"SHEM_MIKTZOA": "Math", "SHEM_SHEELON": "035581", "TEUR_CODE_MOED": "Summer", "TEUR_SHANA": 2026, "TZIYUN_SOFI": 0, "TZIYUN_BCHINA": 88.0},
                {"SHEM_MIKTZOA": "Lit", "TZIYUN_SOFI": "93"},
            ]
        ),
    )
    math, lit = portal(mount).bagrut_grades()
    assert (math.subject, math.value, math.name) == ("Math", "88", "035581 · Summer 2026")
    assert (lit.value, lit.name) == ("93", "בגרות")


def test_bagrut_falls_back_to_summary_without_codes(fake, mount):
    fake.add(
        "GET",
        "/api/bgr/fullgradespage",
        Reply({"hasmachot": [{"CODE_MAARECHET": 1}], "miktzoa": [{"SHEM_MIKTZOA": "Bio", "TZIYUN_SOFI": "81"}]}),
    )
    (grade,) = portal(mount).bagrut_grades()
    assert (grade.subject, grade.value) == ("Bio", "81")


# -- OpenU --------------------------------------------------------------------

MMN = "ממ״ן"
COURSE = {
    "courseId": 20109,
    "courseName": "Calculus",
    "semester": "2026ב",
    "statusDesc": "בלימוד",
    "finalGrade": "null",
    "examGrade": "91",
    "assigmentScores": [
        {"assignmentNo": "11", "assignmentTypeDesc": MMN, "scoreValue": 100, "weight": "10", "isPublic": True},
        {"assignmentNo": "12", "assignmentTypeDesc": MMN, "scoreValue": 55, "isPublic": "false"},
        {"assignmentNo": "13", "assignmentTypeDesc": MMN, "scoreValue": 70, "isScorePublic": False, "submitDate": "2026-05-01"},
        {"assignmentNo": "14", "assignmentTypeDesc": "בחינה", "scoreValue": 91, "examDate": "2026-07-01"},
        {"assignmentNo": "11", "assignmentTypeDesc": MMN, "scoreValue": 99},
    ],
}


def test_term_normalization():
    assert normalize_term(" 2026ב ") == "2026b"
    assert normalize_term("2027C-x") == "2027c"


def test_course_parsing_hides_unpublished_scores():
    course = parse_course(COURSE)
    assert (course["id"], course["key"], course["semester"], course["year"]) == ("20109", "20109:2026b", "b", 2026)
    assert "final_grade" not in course  # the literal string "null" is not a grade
    numbers = [a["number"] for a in course["assignments"]]
    assert numbers == ["11", "13", "14", "11"]  # 12 is not public
    unpublished = next(a for a in course["assignments"] if a["number"] == "13")
    assert "grade" not in unpublished and unpublished["date"] == "2026-05-01"


def test_course_grades_dedupe_and_exam_not_repeated():
    course = parse_course(COURSE)
    names = [(g.name, g.value) for g in course_grades(course)]
    assert names == [(f"{MMN} 11", "100"), ("בחינה 14", "91")]  # duplicate #11 dropped, exam already present


def test_exam_grade_is_added_when_not_an_assignment():
    course = parse_course({**COURSE, "assigmentScores": [], "finalGrade": "95"})
    assert [(g.name, g.value) for g in course_grades(course)] == [("Final grade", "95"), ("Exam grade", "91")]


def test_course_without_name_or_id_is_skipped():
    assert parse_course({"courseId": 1}) is None
    assert parse_course({"courseName": "x"}) is None


def test_openu_login_and_courses(fake, mount):
    client = OpenUClient(mount(HttpSession()))
    fake.add("GET", "https://sheilta.apps.openu.ac.il/Main/Courses", Reply("<html>courses</html>"))
    fake.add("POST", "https://sso.apps.openu.ac.il/process", Reply("<html>ok</html>"))
    fake.add("GET", "/Main/api/homeApi/isAuthenticated", Reply("true"))
    client.login("user", "pw", "123456789")
    sso = body_of(fake.requests[1])
    assert sso == {
        "p_user": "user",
        "p_sisma": "pw",
        "p_mis_student": "123456789",
        "T_PLACE": "https://sheilta.apps.openu.ac.il/Main/Courses",
    }

    fake.add("GET", "/Main/api/coursesApi/get", Reply("﻿" + json.dumps([COURSE])))
    (course,) = client.courses()
    assert course["name"] == "Calculus"


def test_openu_login_failure_and_expiry(fake, mount):
    client = OpenUClient(mount(HttpSession()))
    fake.add("GET", "https://sheilta.apps.openu.ac.il/Main/Courses", Reply('<input name="p_user">'))
    fake.add("POST", "https://sso.apps.openu.ac.il/process", Reply("x"))
    fake.add("GET", "/Main/api/homeApi/isAuthenticated", Reply("false"))
    with pytest.raises(AuthenticationError):
        client.login("u", "p", "1")
    fake.routes.clear()
    fake.add("GET", "/Main/api/coursesApi/get", Reply("<html>sign in</html>"))
    fake.add("GET", "/Main/api/homeApi/isAuthenticated", Reply("false"))
    with pytest.raises(SessionExpired):
        client.courses()
