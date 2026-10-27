"""Shahaf, Schooly, Lockerim and Michlol - offline, synthetic data only."""

import base64
import datetime as dt
import json

import pytest

from conftest import Reply, body_of
from scholix import lockerim, shahaf
from scholix._http import HttpSession
from scholix.errors import (
    AuthenticationError,
    ProtocolError,
    SessionExpired,
)
from scholix.michlol import MichlolClient
from scholix.schooly import SchoolyClient, pupil_card_grades, weekly_timetable


# -- Shahaf -------------------------------------------------------------------


def encode_payload(obj):
    raw = base64.b64encode(json.dumps(obj).encode()).decode()
    return raw.replace("=", ".").replace("/", "_").replace("+", "-")


def test_shahaf_payload_roundtrip_including_padding_alphabet():
    obj = {"Name": "ש", "Token": "tok??>>", "Student": {"Semel": "1", "ClassMid": "7"}}
    encoded = encode_payload(obj)
    assert "." in encoded or "=" not in encoded  # '=' is never emitted
    assert shahaf.decode_sign_in_payload(encoded) == obj


def test_shahaf_payload_rejects_garbage():
    with pytest.raises(ProtocolError):
        shahaf.decode_sign_in_payload("!!!")
    with pytest.raises(ProtocolError):
        shahaf.decode_sign_in_payload(encode_payload([1, 2]))


def test_shahaf_requests_carry_token_and_map_errors(fake, mount):
    client = shahaf.ShahafClient(token="T", session=mount(HttpSession()))
    fake.add("GET", "/api/school/student/", Reply([]))  # a school that does not use Shahaf
    assert client.schools() == []
    assert "token=T" in fake.requests[-1].url

    fake.add("GET", "/api/student/5/9/classes/", Reply({"Classes": [{"Id": 1, "Name": "A"}]}))
    assert client.classes(5, 9)[0]["Id"] == 1

    fake.add("GET", "/api/view/5/9/appdata/", Reply({"version": 1, "views": []}))
    client.app_data(5, 9, 1, {"week": "3"})
    url = fake.requests[-1].url
    assert "clsid=1" in url and "week=3" in url and "token=T" in url

    fake.routes.clear()
    fake.add("GET", "/api/school/student/", Reply({"Status": "failed", "Error": "INVALID_TOKEN"}))
    with pytest.raises(SessionExpired):
        client.schools()
    fake.routes.clear()
    fake.add("GET", "/api/user/authstudent/", Reply({"Status": "failed", "Error": "BAD_CREDENTIALS"}))
    with pytest.raises(AuthenticationError):
        client.login_password("u", "p")


# -- Schooly ------------------------------------------------------------------


def test_schooly_sign_in_with_school_choice(fake, mount):
    client = SchoolyClient(mount(HttpSession()))
    fake.add("POST", "/api/auth/identify", Reply({"success": True, "contactOptions": [{"type": "phone", "masked": "05*****12"}, {"type": "fax"}]}))
    (contact,) = client.identify("012345678")
    fake.add("POST", "/api/auth/send-otp", Reply({"success": True}))
    client.send_code(contact)
    assert body_of(fake.requests[-1]) == {"userId": "012345678", "contactType": "phone", "contactValue": "05*****12"}

    fake.add(
        "POST",
        "/api/auth/verify-otp",
        Reply({"success": True, "token": "jwt", "schoolEntities": [{"_id": "s1"}, {"_id": "s2"}]}),
    )
    schools = client.verify_code("123456")
    assert [s["_id"] for s in schools] == ["s1", "s2"] and client.token == "jwt"

    fake.add("POST", "/api/schools/switch-school", Reply({"success": True}))
    fake.add("GET", "/api/users/me", Reply({"data": {"student_login_id": "stu-1", "student_f_name": "A"}}))
    assert client.verify_code("123456", school_id="s2") == []
    assert body_of(fake.requests[-3])["school_id"] == "s2"
    assert client.student_id == "stu-1"
    assert fake.requests[-1].headers["Authorization"] == "Bearer jwt"
    assert fake.requests[-1].headers["Origin"] == "https://app.schooly.co.il"


def test_schooly_input_validation_and_expiry(fake, mount):
    client = SchoolyClient(mount(HttpSession()))
    with pytest.raises(AuthenticationError):
        client.identify("abc")
    with pytest.raises(AuthenticationError):
        client.verify_code("12345")
    fake.add("GET", "/v1/schools/periods", Reply({}, status=401))
    with pytest.raises(SessionExpired):
        client.periods()


def test_schooly_grades_filtering():
    card = {
        "assessmentDetails": [
            {"_id": "a", "value": 0, "event": {"name": "Zero counts", "subjectName": "Math", "dt": "20261015", "published": True}},
            {"_id": "b", "text": "Excellent", "event": {"name": "Oral", "subjectName": "Lit", "dt": "2026-10-16T00:00"}},
            {"_id": "c", "value": 90, "event": {"name": "Draft", "draft": True}},
            {"_id": "d", "value": 90, "event": {"name": "Deleted", "_deleted": True}},
            {"_id": "e", "value": 90, "event": {"name": "Unpublished", "published": False}},
            {"_id": "f", "value": 90, "studentId": "someone-else", "event": {"name": "Other kid"}},
            {"_id": "g", "event": {"name": "Empty"}},
        ]
    }
    grades = pupil_card_grades(card, "stu-1")
    assert [(g.name, g.value, g.date) for g in grades] == [("Zero counts", 0, "15/10/2026"), ("Oral", "Excellent", "2026-10-16")]


def test_schooly_weekly_timetable_overlay():
    response = {
        "data": [
            {
                "firstHour": 1,
                "subjects": [{"_id": "sub1", "name": "Physics"}],
                "groups": [{"_id": "g1", "teachers": ["t1"]}],
                "teachers": [{"_id": "t1", "name": "Dr. T"}],
                "ttGroups": [
                    {"day": 0, "slot": 0, "group_id": "g1", "subject": "sub1", "effectiveRoom_name": "R1"},
                    {"day": 0, "slot": 1, "group_id": "g1", "subject": "sub1"},
                ],
                "ttSubjects": [
                    {"day": 0, "slot": 0, "group_id": "g1", "subject": "sub1", "isVoided": True, "note": "sick"},
                    {"day": 0, "slot": 1, "group_id": "g1", "subject": "sub1", "movedTo": "x"},
                    {"day": 1, "slot": 2, "group_id": "g1", "subject": "sub1"},
                ],
            }
        ]
    }
    hours = [{"class_ids": [], "periods": [{"start": "08:00", "end": "08:45"}, {"start": "08:50", "end": "09:35"}, {"start": "09:50", "end": "10:35"}]}]
    updated = weekly_timetable(response, hours)
    assert [(l.day, l.period, l.cancelled, l.note) for l in updated] == [(0, 1, True, "sick"), (1, 3, False, "")]
    assert updated[0].teacher == "Dr. T" and updated[0].start == "08:00"

    original = weekly_timetable(response, hours, original=True)
    assert [(l.day, l.period, l.cancelled) for l in original] == [(0, 1, False), (0, 2, False)]
    assert original[0].room == "R1"


# -- Lockerim -----------------------------------------------------------------


def test_pem_command_matches_independent_reference_vector():
    # Synthetic ids. Calculated independently with Node's crypto (AES-128-ECB).
    frame = lockerim.command("123456", 0x01020304, 1, unlock=True)
    assert frame.hex() == "5b6e5a30a9b5833168bddbead8f63d184d5d"
    assert lockerim.decrypt_frame(frame, "123456") == "00010102030401123456000000000000"
    assert lockerim.command("123456", 0x01020304, 1, unlock=False) != frame
    assert lockerim.command("123456", 0x01020304, 2, unlock=True) != frame
    assert lockerim.enrollment_frame("0123abcd").hex() == "3c0123abcd3e"


def test_pem_key_overlay_and_validation():
    key = lockerim._lock_key("0a0b0c")  # last byte 0x0c -> 12 % 12 = 0
    assert key[:3] == bytes.fromhex("0a0b0c") and key[3:] == lockerim._BASE_KEY[3:]
    key = lockerim._lock_key("0a0b0b")  # 0x0b = 11 -> overlay at 11..13, truncated to the 16-byte key
    assert key[11:14] == bytes.fromhex("0a0b0b")
    with pytest.raises(ValueError):
        lockerim._lock_key("12345")
    with pytest.raises(ValueError):
        lockerim.command("123456", 1, 0, True)


def test_pem_reply_parsing():
    assert lockerim.succeeded(b"<PUS>", unlock=True) and not lockerim.succeeded(b"<PUS>", unlock=False)
    assert lockerim.next_counter(b"<PIC\x01\x02>") == 0x0102 + 1
    assert lockerim.next_counter(b"<PUS>") is None
    assert "battery" in lockerim.pem_error(b"<PCB>")
    assert lockerim.pem_error(b"<PUS>") is None
    assert lockerim.advertised_name("aa:bb:cc:dd:ee:ff") == "PEMAABBCCDDEEFF"


def test_lockerim_sms_flow_and_invalid_challenges(fake, mount):
    client = lockerim.LockerimClient(mount(HttpSession()))
    fake.add("POST", "/Account/Login", Reply({"isSucceeded": True, "content": {"otpToken": "c1"}}))
    assert client.send_code("0500000000", "cap-1") == "c1"
    assert body_of(fake.requests[-1]) == {"phoneNumber": "0500000000", "RecaptchaKey": "cap-1"}
    for bad in (None, "", "  ", "null", "NULL", 12):
        fake.routes.clear()
        fake.add("POST", "/Account/ResendLoginOtp", Reply({"isSucceeded": True, "content": {"otpToken": bad}}))
        with pytest.raises(ProtocolError):
            client.resend_code("c1", "cap")
    fake.routes.clear()
    fake.add("POST", "/Account/ValidateLoginOtp", Reply({"isSucceeded": True, "content": {"token": "a", "refreshToken": "r"}}))
    client.verify_code("c1", "0012", "cap-2")
    assert (client.token, client.refresh_token) == ("a", "r")


def test_lockerim_refreshes_once_on_401(fake, mount):
    client = lockerim.LockerimClient(mount(HttpSession()))
    client.token, client.refresh_token = "old", "old-refresh"
    answers = iter([Reply({}, status=401), Reply({"isSucceeded": True, "content": {"subscriptions": [{"cellNumber": 12}]}})])
    fake.add("GET", "/Subscription/GetMySubscriptions", lambda r: next(answers))
    fake.add("POST", "/Account/RefreshToken", Reply({"isSucceeded": True, "content": {"token": "new", "refreshToken": "new-refresh"}}))
    assert client.subscriptions()[0]["cellNumber"] == 12
    assert body_of(fake.requests[1]) == {"refreshToken": "old-refresh"}
    assert fake.requests[-1].headers["Authorization"] == "Bearer new"
    assert client.refresh_token == "new-refresh"


def test_lockerim_error_envelope_and_user_id(fake, mount):
    client = lockerim.LockerimClient(mount(HttpSession()))
    fake.add("POST", "/Account/Login", Reply({"isSucceeded": False, "errors": [{"message": "Validation failed"}], "content": None}))
    with pytest.raises(lockerim.LockerimError, match="Validation failed"):
        client.send_code("", "")
    claims = base64.urlsafe_b64encode(json.dumps({"UserECId": "16909060"}).encode()).decode().rstrip("=")
    client.token = f"h.{claims}.s"
    assert client.user_ec_id() == 0x01020304


# -- Michlol ------------------------------------------------------------------


def test_michlol_login_courses_and_grades(fake, mount):
    client = MichlolClient(session=mount(HttpSession()))
    fake.add("POST", "/api/Login/Login", Reply({"success": True, "token": "bearer-1"}))
    fake.add("POST", "/api/Account/UserInfo", Reply({"userInfo": {"smp": "First", "smm": "Last"}}))
    client.login("123456789", "pw")
    sent = body_of(fake.requests[0])
    assert sent["zht"] == "123456789" and sent["loginType"] == "student" and sent["captchaToken"] is None
    assert client.display_name == "First Last"
    assert fake.requests[1].headers["Authorization"] == "Bearer bearer-1"

    fake.add(
        "POST",
        "/api/StudentCourses/Data",
        Reply({"courses": {"clientData": [{"krs_shm": "Algebra", "krs_snl": "2026", "krs_sms_select": "A", "all_pms_shm": "Cohen Dana"}]}}),
    )
    (course,) = client.courses()
    assert course["teacher"] == "Dana Cohen"  # RTL word order restored

    fake.add(
        "POST",
        "/api/Grades/Data",
        Reply(
            {
                "collapsedCourses": {
                    "clientData": [
                        {
                            "krs_shm": "Algebra",
                            "__body": [
                                {
                                    "krs_shm": "Final",
                                    "krs_snl": "2026",
                                    "bhnzin": "88",
                                    "__body": [
                                        {
                                            "zin_sug": "Exam",
                                            "moed_1_zin": "88",
                                            "krs_snl": "2026",
                                            "__buttons": [{"description": "בחינה סרוקה", "routeData": {"scan_location": "x", "__hash": "h"}}],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            },
            headers={"pagedata": "PD"},
        ),
    )
    (grade,) = client.grades()
    assert (grade.subject, grade.name, grade.value) == ("Algebra", "Final", "88")
    assert grade.raw["submissions"][0]["scan"]["__hash"] == "h"
    assert client.page_data == "PD"


def test_michlol_bad_login(fake, mount):
    client = MichlolClient(session=mount(HttpSession()))
    fake.add("POST", "/api/Login/Login", Reply({"success": False, "errorDescription": "Bad credentials"}))
    with pytest.raises(AuthenticationError, match="Bad credentials"):
        client.login("1", "2")
