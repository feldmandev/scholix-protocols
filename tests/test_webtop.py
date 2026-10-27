import datetime as dt
import html

import pytest

from conftest import Reply, body_of
from scholix import moe
from scholix.errors import (
    AuthenticationError,
    CaptchaRequired,
    SessionExpired,
    VerificationRequired,
)
from scholix.webtop import (
    HOSTS,
    MOE_ENTRY,
    WebtopClient,
    current_semester,
    encrypt_login_field,
    study_year,
)

# Captured from the live website's encryptStringToServer on 2026-10-17.
AES_VECTORS = {
    "test-user0": "xIKv4WqO5o7fO/LgmjaVSw==",
    "0123456780": "c39fRupZjW7EDKUoH0L09g==",
    'abc"\\0': "uDppSUp599nPYfFXTJr6DA==",
}


@pytest.mark.parametrize("plain,expected", AES_VECTORS.items())
def test_login_field_encryption_matches_live_site(plain, expected):
    assert encrypt_login_field(plain) == expected


def test_study_year_and_semester():
    assert study_year(dt.date(2026, 10, 1)) == 2027
    assert study_year(dt.date(2027, 6, 1)) == 2027
    assert [current_semester(dt.date(2027, m, 1)) for m in (1, 2, 8, 9)] == ["a", "b", "b", "a"]


PROFILE = {
    "userId": "enc-user",
    "firstName": "Test",
    "lastName": "Student",
    "institutionCode": 123456,
    "classCode": 10,
    "classNumber": 2,
}


def signed_in(fake, mount):
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    fake.add("POST", "/server/api/user/LoginByUserNameAndPassword", Reply({"status": True, "data": PROFILE}))
    client.login_password("user", "pw")
    return client


def test_password_login_sends_encrypted_marker_and_exposes_profile(fake, mount):
    client = signed_in(fake, mount)
    sent = body_of(fake.requests[0])
    assert sent["UserName"] == "user" and sent["Password"] == "pw"
    assert sent["Data"] == encrypt_login_field("user0")
    assert fake.requests[0].headers["language"] == "he"
    assert (client.user_id, client.institution_code, client.class_code) == ("enc-user", 123456, 10)
    assert client.class_selector == "10|2" and client.name == "Test Student"


def test_password_login_rejections(fake, mount):
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    fake.add("POST", "/server/api/user/LoginByUserNameAndPassword", Reply({"status": False}))
    with pytest.raises(AuthenticationError):
        client.login_password("u", "p")
    fake.routes.clear()
    fake.add("POST", "/server/api/user/LoginByUserNameAndPassword", Reply({"status": True, "message": "get2FA", "data": {}}))
    with pytest.raises(VerificationRequired):
        client.login_password("u", "p")


def test_grades_use_encrypted_student_id_and_division(fake, mount):
    client = signed_in(fake, mount)
    fake.add(
        "POST",
        "/server/api/PupilCard/GetSettingsList",
        Reply(
            {
                "status": True,
                "data": {
                    "divisionsList": [
                        {"division_id": 11, "year_division": 1, "division_name": "מחצית א"},
                        {"division_id": 22, "year_division": 2, "division_name": "מחצית ב"},
                    ],
                    "children": [{"id": "enc-pupil"}],
                    "currentStudyYear": 2027,
                },
            }
        ),
    )
    fake.add(
        "POST",
        "/server/api/PupilCard/GetPupilGrades",
        Reply({"status": True, "data": [{"subject": "Math", "title": "Test 1", "grade": "95", "date": "2026-10-01"}]}),
    )
    (grade,) = client.grades(year=2027, semester="b")
    assert (grade.subject, grade.name, grade.value) == ("Math", "Test 1", "95")
    sent = body_of(fake.requests[-1])
    assert sent == {"studyYear": 2027, "moduleID": 6, "periodID": 22, "studentID": "enc-pupil"}


def test_expired_session_is_reported(fake, mount):
    client = signed_in(fake, mount)
    fake.add("POST", "/server/api/messageBox/GetMessagesInbox", Reply("", status=401))
    with pytest.raises(SessionExpired):
        client.inbox()


def test_timetable_normalizes_original_lessons(fake, mount):
    client = signed_in(fake, mount)
    fake.add(
        "POST",
        "/server/api/shotef/ShotefSchedualeData",
        Reply(
            {
                "data": [
                    {
                        "dayIndex": 2,
                        "hoursData": [
                            {
                                "hour": 3,
                                "hourName": "10:30 - 09:45",
                                "scheduale": [
                                    {
                                        "subject": "Bio ",
                                        "teacherPrivateName": "A",
                                        "teacherLastName": "B",
                                        "room": "null",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
    )
    (lesson,) = client.timetable()
    assert (lesson.day, lesson.period, lesson.subject, lesson.teacher) == (1, 3, "Bio", "A B")
    assert (lesson.start, lesson.end, lesson.room) == ("09:45", "10:30", "")
    assert body_of(fake.requests[-1]) == {"institutionCode": 123456, "selectedValue": "10|2", "typeView": 1}


# -- full MOE hand-off ------------------------------------------------------

SERVER_URL = "https://lgn.edu.gov.il/nidp/wsfed/ep?id=contract&sid=0&option=credential&sid=0"
ASSERTION = '<t:RequestSecurityTokenResponse xmlns:t="http://schemas.xmlsoap.org/ws/2005/02/trust">A&B</t:RequestSecurityTokenResponse>'


def credential_page(login_type="usernamePassword"):
    return (
        "<html><script>"
        f"var _serverUrl = '{SERVER_URL}'; var _loginType = '{login_type}'; var _target = 'target-1';"
        "</script></html>"
    )


def moe_routes(fake, *, ajax_result=None):
    fake.add(
        "GET",
        "https://www.webtop.co.il/applications/loginMOENew/default.aspx",
        Reply("", status=302, headers={"Location": "https://lgn.edu.gov.il/nidp/wsfed/ep?wa=wsignin1.0"}),
    )
    fake.add("GET", "https://lgn.edu.gov.il/nidp/wsfed/ep", Reply(credential_page()))
    results = iter(
        [
            {"isError": True, "errorCode": "INVALID_INPUT"},  # initiateLoginSequence on a fresh session
            ajax_result or {"isError": False, "isCaptchaNeeded": False, "isPasswordExpiring": False},
        ]
    )

    def credential(request):
        fields = body_of(request)
        if fields.get("target"):  # completion POST
            return Reply("<script>top.location.href='ep?sid=0'</script>")
        return Reply(next(results))

    fake.add("POST", "https://lgn.edu.gov.il/nidp/wsfed/ep", credential)
    escaped = html.escape(ASSERTION, quote=True)
    fake.add(
        "GET",
        "https://lgn.edu.gov.il/nidp/wsfed/ep?sid=0",
        Reply(
            "<html><body onload='document.forms[0].submit()'>"
            f"<form method='post' action='https://webtop.smartschool.co.il/account/saml'>"
            f"<input name='wa' value='wsignin1.0'><input name='wresult' value=\"{escaped}\"></form>"
            "<script>document.forms[0].submit();</script></body></html>"
        ),
    )
    fake.add(
        "POST",
        "https://webtop.smartschool.co.il/account/saml",
        Reply("", status=302, headers={"Location": "https://webtop.smartschool.co.il/account/loginMoe?key=ab+cd%2Bef"}),
    )
    fake.add("GET", "https://webtop.smartschool.co.il/account/loginMoe", Reply("<html>app</html>"))


def test_moe_login_full_handoff(fake, mount):
    moe_routes(fake)
    session = mount(moe.new_moe_session(HOSTS))
    client = WebtopClient(session)
    fake.add("POST", "/server/api/user/LoginMoe", Reply({"status": True, "data": PROFILE}))
    session.cookies.set("webToken", "t", domain=".smartschool.co.il", path="/")

    client.login_moe("moe-user", "moe-pass")

    posts = [body_of(r) for r in fake.requests if r.method == "POST" and "lgn.edu.gov.il" in r.url]
    assert posts[0]["initiateLoginSequence"] == "true"
    assert posts[1]["HIN_USERID"] == "moe-user" and posts[1]["Ecom_Password"] == "moe-pass"
    assert posts[1]["g-recaptcha-response"] == " "
    assert posts[2] == {"option": "credential", "target": "target-1"}

    saml = next(r for r in fake.requests if r.url.endswith("/account/saml"))
    assert body_of(saml)["wresult"] == ASSERTION  # entities decoded exactly once

    login = body_of(fake.requests[-1])
    assert login["key"] == "ab cd+ef"  # '+' -> space, '%2B' -> '+', like Angular's parser
    assert login["deviceDataJson"] == '{"isMobile": false, "isTablet": false, "isDesktop": true}'
    assert client.user_id == "enc-user"


def test_moe_captcha_is_never_bypassed(fake, mount):
    moe_routes(fake, ajax_result={"isError": False, "isCaptchaNeeded": True})
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    with pytest.raises(CaptchaRequired):
        client.login_moe("u", "p")


def test_moe_wrong_password(fake, mount):
    moe_routes(fake, ajax_result={"isError": True, "errorCode": "BAD"})
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    with pytest.raises(AuthenticationError, match="BAD"):
        client.login_moe("u", "p")


def test_moe_sms_account_is_reported_not_attempted(fake, mount):
    fake.add(
        "GET",
        "https://www.webtop.co.il/applications/loginMOENew/default.aspx",
        Reply(credential_page("usernameSMSOTP")),
    )
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    with pytest.raises(VerificationRequired):
        client.login_moe("u", "p")
    assert all(r.method == "GET" for r in fake.requests)  # no credentials were posted


def test_session_refuses_unexpected_hosts(fake, mount):
    from scholix.errors import ProtocolError

    fake.add(
        "GET",
        "https://www.webtop.co.il/applications/loginMOENew/default.aspx",
        Reply("", status=302, headers={"Location": "https://evil.example/steal"}),
    )
    fake.add("GET", "https://evil.example/steal", Reply("x"))
    client = WebtopClient(mount(moe.new_moe_session(HOSTS)))
    with pytest.raises(ProtocolError, match="Unexpected address"):
        client.login_moe("u", "p")
