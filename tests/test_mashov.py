import datetime as dt

import pytest

from conftest import Reply, body_of
from scholix._http import HttpSession
from scholix.errors import AuthenticationError, CaptchaRequired, RateLimited, SessionExpired
from scholix.mashov import MashovClient, current_school_year, list_schools, normalize_grade_code

LOGIN = {
    "accessToken": {
        "displayName": "Parent Name",
        "children": [
            {"childGuid": "guid-1", "privateName": "Kid One", "classCode": "יא", "classNum": 3},
            {"childGuid": "guid-2", "privateName": "Kid Two", "classCode": "ח", "classNum": 1},
        ],
    }
}


def make(fake, mount):
    client = MashovClient("123456", "someone", 2027, session=mount(HttpSession()))
    fake.add(
        "POST",
        "/api/login",
        Reply(LOGIN, headers={"X-Csrf-Token": "csrf-1", "devicePass": "device-1"}),
    )
    return client


def test_login_body_and_state(fake, mount):
    client = make(fake, mount)
    client.login("secret")
    sent = body_of(fake.requests[0])
    assert sent["semel"] == "123456" and sent["year"] == "2027"
    assert sent["username"] == "someone" and sent["password"] == "secret"
    assert sent["isPrivateDevice"] is True
    assert sent["appName"] == "info.mashov.students"
    assert client.csrf_token == "csrf-1" and client.device_pass == "device-1"
    assert [s.grade for s in client.students] == ["11", "8"]
    assert client.student.guid == "guid-1"


def test_failed_login_raises(fake, mount):
    client = MashovClient("1", "u", 2027, session=mount(HttpSession()))
    fake.add("POST", "/api/login", Reply({}, status=401))
    with pytest.raises(AuthenticationError):
        client.login("bad")


def test_device_login_sends_header_and_no_password(fake, mount):
    client = make(fake, mount)
    fake.add("POST", "/api/loginDevice", Reply(LOGIN, headers={"devicePass": "device-2"}))
    client.login_with_device_pass("device-1")
    request = fake.requests[0]
    assert request.headers["devicePass"] == "device-1"
    assert "password" not in body_of(request)
    assert client.device_pass == "device-2"


def test_otp_request_validation_and_errors(fake, mount):
    client = make(fake, mount)
    with pytest.raises(AuthenticationError):
        client.request_otp("12345")
    fake.add("POST", "/api/user/otp/request", Reply("", status=429))
    with pytest.raises(RateLimited):
        client.request_otp("0501234567")
    fake.routes.clear()
    fake.add("POST", "/api/user/otp/request", Reply("captcha required", status=403))
    with pytest.raises(CaptchaRequired):
        client.request_otp("050-123-4567")
    sent = body_of(fake.requests[-1])
    assert sent == {"semel": 123456, "username": "someone", "cellphone": "0501234567"}


def test_grades_prefer_numeric_then_text(fake, mount):
    client = make(fake, mount)
    client.login("x")
    fake.add(
        "GET",
        "/api/students/guid-1/grades",
        Reply(
            [
                {"subjectName": "Math", "gradingEvent": "Quiz", "grade": 0, "eventDate": "2026-10-01"},
                {"groupName": "Lit", "gradeType": "Oral", "grade": None, "textualGrade": "Excellent"},
            ]
        ),
    )
    grades = client.grades()
    assert grades[0].value == 0 and grades[0].subject == "Math"  # zero is a real grade
    assert grades[1].value == "Excellent" and grades[1].subject == "Lit"
    assert fake.requests[-1].headers["X-Csrf-Token"] == "csrf-1"


def test_timetable_uses_bells_and_zero_based_days(fake, mount):
    client = make(fake, mount)
    client.login("x")
    fake.add(
        "GET",
        "/api/students/guid-1/timetable",
        Reply(
            [
                {
                    "timeTable": {"day": 1, "lesson": 2, "roomNum": "12"},
                    "groupDetails": {
                        "subjectName": "Physics",
                        "groupTeachers": [{"teacherName": "A"}, {"teacherName": "A"}, {"teacherName": "B"}],
                    },
                },
                {"timeTable": {"day": 2, "lesson": 0}, "groupDetails": {}},  # period 0 is skipped
            ]
        ),
    )
    fake.add("GET", "/api/bells", Reply([{"lessonNumber": 2, "startTime": "1970-01-01T08:55:00", "endTime": "09:40:00"}]))
    (lesson,) = client.timetable()
    assert (lesson.day, lesson.period, lesson.start, lesson.end) == (0, 2, "08:55", "09:40")
    assert lesson.teacher == "A, B" and lesson.room == "12"
    timetable_request = next(r for r in fake.requests if "/timetable" in r.url)
    assert timetable_request.url.endswith("/timetable?year=2027")


def test_expired_session(fake, mount):
    client = make(fake, mount)
    client.login("x")
    fake.add("GET", "/api/students/guid-1/grades", Reply("", status=401))
    with pytest.raises(SessionExpired):
        client.grades()


def test_helpers():
    assert current_school_year(dt.date(2026, 10, 27)) == 2027
    assert current_school_year(dt.date(2027, 3, 1)) == 2027
    assert normalize_grade_code("י״ב") == "12"
    assert normalize_grade_code("7") == "7"


def test_list_schools(fake, mount):
    fake.add("GET", "/api/schools", Reply([{"semel": 11, "name": "A"}, {"semel": None, "name": "B"}]))
    assert list_schools(mount(HttpSession())) == [{"semel": "11", "name": "A"}]
