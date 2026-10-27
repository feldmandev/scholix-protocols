"""OpenU: Sheilta + Opal parsers and flows on synthetic pages (no real data)."""

import datetime as dt
import json

import pytest

from conftest import Reply, body_of
from scholix import openu
from scholix._http import HttpSession
from scholix.errors import ProtocolError, SessionExpired


def client(mount):
    return openu.OpenUClient(mount(HttpSession(allowed_hosts=openu.HOSTS)))


# -- semesters, status, urls --------------------------------------------------


def test_semester_normalization_is_strict_and_roundtrips():
    assert openu.normalize_semester(" 2026ב ") == "2026b"
    assert openu.normalize_semester("2027C-x") == "2027c"
    assert openu.sheilta_semester("2026b") == "2026ב"
    for bad in ("", "26b", "2026d", "abc"):
        with pytest.raises(ValueError):
            openu.normalize_semester(bad)
    assert openu.guess_semester(dt.date(2026, 3, 1)) == "2026a"
    assert openu.guess_semester(dt.date(2026, 7, 1)) == "2026b"
    assert openu.guess_semester(dt.date(2026, 11, 1)) == "2026c"


def test_status_classification_order():
    today = dt.date.today()
    past, future = (today - dt.timedelta(days=5)).isoformat(), (today + dt.timedelta(days=60)).isoformat()
    assert openu.classify_status("מבוטל") == "canceled"
    assert openu.classify_status("עבר בהצלחה") == "did"
    assert openu.classify_status("בלימוד") == "this_semester"
    assert openu.classify_status("נרשם", past) == "this_semester"
    assert openu.classify_status("נרשם", future) == "upcoming"
    assert openu.classify_status("???") == "did"


def test_url_allow_lists():
    assert openu.assert_allowed_url("https://opal.openu.ac.il/x")
    with pytest.raises(ProtocolError):
        openu.assert_allowed_url("http://opal.openu.ac.il/x")
    with pytest.raises(ProtocolError):
        openu.assert_allowed_url("https://user:pw@opal.openu.ac.il/x")
    with pytest.raises(ProtocolError):
        openu.assert_allowed_url("https://evil.example/x")
    assert openu.assert_allowed_url("https://x.bynetcdn.com/a.m3u8", hls=True)
    with pytest.raises(ProtocolError):
        openu.assert_allowed_url("https://bynetcdn.com.evil.example/a", hls=True)


# -- assessment artifacts and merge -----------------------------------------


ROW = {
    "courseId": "20109",
    "courseName": "Calc",
    "semester": "2026ב",
    "studyCenter": "01",
    "studyGroup": "12",
    "activityCode": "02",
    "assigmentScores": [
        {"assignmentNo": "11", "assignmentTypeDesc": "ממ״ן", "scoreValue": 90, "weight": "10"},
        {
            "assignmentNo": "50",
            "assignmentTypeDesc": "בחינת גמר",
            "scoreValue": 80,
            "examinationDate": "2026-07-01",
            "questionScores": [{"questionNo": 1, "score": 40}],
            "hasExamFileId": False,
            "hasQuestionnaireFileId": True,
        },
    ],
}


def test_exam_artifacts_use_explicit_flags_over_inference():
    course = openu.parse_course(ROW)
    assert (course["study_center"], course["study_group"], course["activity_code"]) == ("01", "12", "02")
    exam = next(a for a in course["assignments"] if a["number"] == "50")
    assert exam["isExam"] and exam["artifacts"]["notebook"] is False  # hasExamFileId=False wins
    assert exam["artifacts"]["questionnaire"] == "personal"
    assert exam["artifacts"]["appeal"] is True  # an exam with a grade
    assert exam["questionScores"] == [{"number": "1", "score": "40"}]
    plain = next(a for a in course["assignments"] if a["number"] == "11")
    assert plain["artifacts"] == {"questionnaire": None, "notebook": False, "appeal": False}


def test_merge_prefers_sheilta_grade_and_opal_status_and_sorts_numerically():
    opal = [
        {"number": "7", "name": "ממ״ן 07", "due_date": "1/1", "grade": "", "status": "הוגש", "status_code": "S"},
        {"number": "10", "name": "ממ״ן 10", "grade": "", "status": ""},
    ]
    sheilta = [
        {"number": "07", "type": "ממ״ן", "grade": "95", "weight": "5", "date": "2026-02-01", "status": "x", "isExam": False, "artifacts": {}},
        {"number": "2", "type": "ממ״ן", "grade": "70", "isExam": False},
    ]
    merged = openu.merge_assessments(opal, sheilta)
    assert [m["number"] for m in merged] == ["2", "7", "10"]
    seven = merged[1]
    assert (seven["grade"], seven["status"], seven["weight"], seven["due_date"]) == ("95", "הוגש", "5", "1/1")


# -- sheilta flows -------------------------------------------------------------


def test_course_list_retries_500_once_after_refreshing_page_session(fake, mount):
    answers = iter([Reply("", status=500), Reply(json.dumps([ROW]))])
    fake.add("GET", "/Main/api/coursesApi/get", lambda r: next(answers))
    fake.add("GET", "/Main/Courses", Reply("<html>courses</html>"))
    (course,) = client(mount).courses()
    assert course["name"] == "Calc"
    assert [r.url.split("/Main/")[1] for r in fake.requests] == ["api/coursesApi/get", "Courses", "api/coursesApi/get"]


def test_meetings_drop_cancelled_and_bad_rows(fake, mount):
    good = {"meetingDate": "2026-11-02T00:00:00", "fromHour": "9:00", "toHour": "10:30:00", "typeDescription": "הרצאה", "position": "Zoom", "onlineMeeting": True}
    fake.add("GET", "/Main/api/coursesApi/getStudyGroupMeetings", Reply([good, {**good, "isCanceled": True}, {"meetingDate": "bad", "fromHour": "9:00", "toHour": "10:00"}]))
    fake.add("GET", "/Main/api/coursesApi/getStudyGroupLabMeetings", Reply("", status=500))
    course = openu.parse_course(ROW)
    (meeting,) = client(mount).meetings(course)
    assert (meeting["date"], meeting["from_hour"], meeting["to_hour"], meeting["online"]) == ("2026-11-02", "09:00", "10:30", True)
    url = fake.requests[0].url
    assert "semester=2026%D7%91" in url and "studyCenter=01" in url and "studyGroup=12" in url  # raw semester, verbatim


EXAMS_HTML = """<button class="accordion">קורס: 20109 - חדווא | סמסטר לימוד: 2026ב</button>
<div class="panel"><p class="covid_p">תאור מתכונת הבחינה: בחינה מקוונת</p><p class="covid_p">סמסטר בחינה: 2026ב</p>
<table class="content_tbl covid_table"><tr><th>תאריך בחינה</th><th>שעת התחלה</th><th>שעת סיום</th><th>מקום בחינה</th></tr>
<tr><td>01/07/2026</td><td>09:00</td><td>12:00</td><td>מרכז</td></tr><tr><td>נרשמת למועד</td></tr></table></div>"""

DETAIL_HTML = """<html><body>זימון למועד 01/07/2026 שעת התחלת הבחינה 9:00 מרכז הבחינה רעננה תחבורה ציבורית: קו 5
חומר עזר המותר בשימוש: מחשבון בחר מועד בחינה <input name="p_moed_act" value="2"></body></html>"""


def test_exam_summary_and_confirmed_detail(fake, mount):
    summary = openu.parse_exams(EXAMS_HTML)[0]
    assert (summary["course_id"], summary["semester"], summary["format"], summary["date"]) == ("20109", "2026b", "בחינה מקוונת", "01/07/2026")
    assert summary["registration"] == "נרשמת למועד"

    fake.add("GET", "/pls/dmyopt2/course_info.Home_Test", Reply(EXAMS_HTML))
    fake.add("GET", "/pls/dmyopt2/bchinot.bchinot_screen", Reply(DETAIL_HTML))
    (exam,) = client(mount).exams([openu.parse_course(ROW)])
    assert (exam["date"], exam["start_time"], exam["location"], exam["transportation"]) == ("01/07/2026", "9:00", "רעננה", "קו 5")
    assert exam["allowed_materials"] == "מחשבון" and "מועד: 2" in exam["exam_semester"]
    query = fake.requests[-1].url
    assert "P_SUG_CHALUFA=26" in query and "p_KOD_PEILUT_KURS=02" in query and "p_sug_rashuma=GMAR" in query


def test_unconfirmed_exam_yields_nothing():
    assert openu.parse_exam_details({"semester": "2026b", "location": ""}, "<html>אין מועד</html>", "u") is None


def test_assessment_document_flags(fake, mount):
    fake.add("GET", "/Main/api/coursesApi/downloadScanned", Reply("%PDF"))
    c = client(mount)
    c.assessment_document("20109", "2026b", "50", "notebook")
    c.assessment_document("20109", "2026b", "50", "questionnaire", "personal")
    first, second = fake.requests[0].url, fake.requests[1].url
    assert "examScanned=true" in first and "questionnaire=false" in first and "semester=2026%D7%91" in first
    assert "questionnaireScanned=true" in second and "examScanned=false" in second


def test_appeal_url_must_be_on_an_openu_host(fake, mount):
    row = {**ROW, "assigmentScores": [{"assignmentNo": "50", "assignmentTypeDesc": "בחינה", "objectionLink": "https://www.openu.ac.il/appeal?p_token=abc"}]}
    fake.add("GET", "/Main/api/coursesApi/get", Reply(json.dumps([row])))
    assert client(mount).appeal_url("20109", "2026b", "50").startswith("https://www.openu.ac.il/appeal")
    row["assigmentScores"][0]["objectionLink"] = "https://evil.example/appeal"
    fake.routes.clear()
    fake.add("GET", "/Main/api/coursesApi/get", Reply(json.dumps([row])))
    with pytest.raises(ProtocolError):
        client(mount).appeal_url("20109", "2026b", "50")


# -- opal ------------------------------------------------------------------------


def test_resolve_course_site_from_redirect_or_body_class(fake, mount):
    fake.add("GET", "/ouil/course.php", Reply("", status=302, headers={"Location": "https://opal.openu.ac.il/course/view.php?id=4321"}))
    fake.add("GET", "/course/view.php", Reply("<html></html>"))
    assert client(mount).resolve_course_site("20109", "2026ב") == "4321"
    assert "course=c20109" in fake.requests[0].url and "semester=2026b" in fake.requests[0].url

    fake.routes.clear()
    fake.add("GET", "/ouil/course.php", Reply("<body class='x course-777 y'>"))
    assert client(mount).resolve_course_site("20109", "2026b") == "777"
    fake.routes.clear()
    fake.add("GET", "/ouil/course.php", Reply("<body>no site</body>"))
    assert client(mount).resolve_course_site("20109", "2026b") is None


def test_login_page_means_expired(fake, mount):
    fake.add("GET", "/ouil/course.php", Reply("", status=302, headers={"Location": "https://sso.apps.openu.ac.il/login?x=1"}))
    fake.add("GET", "/login", Reply("<form></form>"))
    with pytest.raises(SessionExpired):
        client(mount).resolve_course_site("20109", "2026b")


ASSIGN_LIST = """<ul id="ouilassign_index_list_container"><li data-assigh-type="file">
<div index-item="name"><a href="/mod/ouilassign/view.php?id=9"><span>11</span> ממ"ן 11</a></div>
<div index-item="duedate"><span class="ouilassign_index_list_title">מועד</span> 01/12/2026</div>
<div index-item="status"><span ouilassign-status="3">נבדק</span></div>
<div index-item="grade">92</div></li></ul>"""


def test_assignment_list_and_merge(fake, mount):
    (row,) = openu.parse_assignment_list(ASSIGN_LIST)
    assert (row["number"], row["name"], row["due_date"], row["status_code"], row["grade"]) == ("11", 'ממ"ן 11', "01/12/2026", "3", "92")
    assert row["url"] == "https://opal.openu.ac.il/mod/ouilassign/view.php?id=9"

    fake.add("GET", "/mod/ouilassign/index.php", Reply(ASSIGN_LIST))
    merged = client(mount).assignments(openu.parse_course(ROW), moodle_id="4321")
    assert [m["number"] for m in merged] == ["11", "50"]
    assert merged[0]["grade"] == "90" and merged[0]["due_date"] == "01/12/2026"  # sheilta grade wins, opal date kept
    assert "id=4321" in fake.requests[0].url and "quicklinks=1" in fake.requests[0].url


def test_no_course_site_still_returns_sheilta_assessments(mount, fake):
    merged = client(mount).assignments(openu.parse_course(ROW), moodle_id=None)
    assert [m["number"] for m in merged] == ["11", "50"] and not fake.requests


COURSE_HTML = """<html><body>
<div class="sections-nav-list"><div class="dropdown-menu">
<a class="dropdown-item" href="/course/view.php?id=4321&sectionid=100">יחידה 1</a>
<a class="dropdown-item" href="/course/view.php?id=4321&sectionid=101">יחידה 2</a></div></div>
<div data-sectionid="100" class="current"></div>
<div class="block_ouil_navigation"><ul><li class="type_structure current_parent"><p><a href="/course/view.php?id=4321&sectionid=100">יחידה 1</a></p><ul>
<li class="type_activity mod_completed"><a href="/mod/quiz/view.php?id=55"><span class="item-content-wrap">בוחן</span></a></li>
<li class="type_activity"><a href="/mod/ouilforum/view.php?id=56"><span class="item-content-wrap">פורום הקורס</span></a></li>
<li class="type_activity"><a href="/mod/url/view.php?id=57"><span class="item-content-wrap">קישור</span></a></li>
</ul></li></ul></div>
<a href="/pls/myopr/booklet.viewbook?p_kurs=1">חוברת הקורס</a></body></html>"""


def test_sections_and_activities(fake, mount):
    fake.add("GET", "/course/view.php", Reply(COURSE_HTML))
    result = client(mount).sections("4321")
    assert [s["id"] for s in result["sections"]] == ["100", "101"]
    assert result["initial_section_id"] == "100" and result["has_booklet"] is True
    acts = result["sections"][0]["activities"]
    assert [(a["cmid"], a["module"], a["type"], a["completed"]) for a in acts] == [
        ("55", "quiz", "quiz", True),
        ("56", "ouilforum", "forum", False),
        ("57", "url", "link", False),
    ]


def test_lazy_section_falls_back_to_navbranch(fake, mount):
    page = '<html><script>M.cfg = {"sesskey":"KEY123"};</script><a class="dropdown-item" href="/course/view.php?id=1&sectionid=200">U</a></html>'
    fake.add("GET", "/course/view.php", Reply(page))
    fake.add(
        "POST",
        "/lib/ajax/getnavbranch.php",
        Reply({"children": [{"name": "קובץ", "link": "https://opal.openu.ac.il/mod/resource/view.php?id=9&amp;x=1", "class": "mod_completed"}]}),
    )
    section = client(mount).section("1", "200")
    assert section["title"] == "U" and section["activities"][0]["type"] == "content" and section["activities"][0]["completed"]
    sent = body_of(fake.requests[-1])
    assert sent == {"elementid": "expandable_branch_30_200", "id": "200", "type": "30", "sesskey": "KEY123"}
    assert fake.requests[-1].headers["X-Requested-With"] == "XMLHttpRequest"


def test_forums_books_and_booklet_parsers():
    forums = openu.parse_forums(
        '<li class="forumslist" data-forumid="5"><div class="forum_list_title"><a>הודעות</a></div>'
        '<div class="forum_body_content">תיאור</div><div class="forum_footer">דיונים: 12</div>'
        '<div class="forum_unread"><span class="forumlist_number">3</span></div><span class="forum_clean_date">א</span></li>'
    )
    assert forums == [{"id": "5", "title": "הודעות", "description": "תיאור", "discussions": 12, "unread": 3, "last_update": "א"}]

    signed = "https://opal.openu.ac.il/local/ouil_ebook/signedpdf.php?id=1&sig=x"
    books = openu.parse_books(
        f'<div class="ebook_cell" id="ebook7"><span class="ebook_name">ספר</span><a class="ebook_digital_link" href-content="{signed}"></a></div>'
        '<div class="ebook_cell" id="ebook8"><span class="ebook_name">ספר ב</span><a class="ebook_digital_link" href="#"></a></div>'
        '<div class="ebook_cell" id="ebookX"></div>'
    )
    assert [(b["id"], b["requires_consent"]) for b in books] == [("7", False), ("8", True)]
    with pytest.raises(ProtocolError):  # right path, wrong host: never fetch it
        openu.parse_books('<div class="ebook_cell" id="ebook9"><a class="ebook_digital_link" href="https://evil.example/local/ouil_ebook/signedpdf.php"></a></div>')

    items = openu.parse_booklet(
        '<frame src="booklet.download?in_file_name=a1.pdf&p_kurs=1"><a href="booklet.download?in_file_name=..%2Fx.pdf">bad</a>'
        '<a href="booklet.download?in_file_name=a1.pdf">dup</a><a href="booklet.download?in_file_name=b2.PDF">פרק 2</a>'
    )
    assert items == [{"title": "חוברת הקורס", "file": "a1.pdf"}, {"title": "פרק 2", "file": "b2.PDF"}]


def test_booklet_performs_the_sso_hop_once(fake, mount):
    bounce = "<script>top.location.href='https://sso.apps.openu.ac.il/login?T_PLACE=x'</script>".encode("windows-1255")
    page = '<frame src="booklet.download?in_file_name=a1.pdf">'.encode("windows-1255")
    answers = iter([Reply(bounce, headers={"content-type": "text/html"}), Reply(page, headers={"content-type": "text/html"})])
    fake.add("GET", "/pls/myopr/booklet.viewbook", lambda r: next(answers))
    fake.add("GET", "/login", Reply("<html></html>"))
    result = client(mount).booklet("020109", "2026ב")
    assert result == {"course_id": "20109", "semester": "2026b", "items": [{"title": "חוברת הקורס", "file": "a1.pdf"}]}
    hop = [r.url for r in fake.requests if "sso.apps" in r.url][0]
    assert "T_PLACE=https%3A%2F%2Fsheilta.apps.openu.ac.il%2Fpls%2Fmyopr%2Fbooklet.viewbook" in hop


def test_profile_parser():
    html = (
        "<html><head><title>פרופיל: דנה לוי : פרופיל ציבורי</title></head><body>"
        '<a href="/user/edit.php?id=88">edit</a><a href="mailto:d@example.com">d@example.com</a>'
        '<div class="profile_tree"><section class="node_category"><h3>פרטים</h3><dl><dt>עיר</dt><dd>חיפה</dd></dl>'
        '<ul><li><span><a href="/blog">בלוג</a></span></li></ul></section></div></body></html>'
    )
    profile = openu.parse_profile(html)
    assert (profile["full_name"], profile["user_id"], profile["email"]) == ("דנה לוי", "88", "d@example.com")
    assert profile["sections"][0]["entries"][0] == {"label": "עיר", "value": "חיפה", "links": []}
    assert profile["sections"][0]["entries"][1]["value"] == "בלוג"


# -- recordings ------------------------------------------------------------------

COLLECTION_HTML = """<html><script>var x = {"courseid":"9001"};</script>
<ul id="ovc_collections_list"><li class="active"><a class="collection_link" c-value="1" cid="55" mid="MID" cmid="300">הרצאות</a></li>
<li><a class="collection_link" c-value="2" cid="55" mid="MID" cmid="301">תרגולים</a></li></ul>
<div class="ovc_playlist_border" id="playlist12"><div class="ovc_thumb"><img src="/t.png?p_token=zzz"></div>
<span class="ovc_playlist_title">שיעור 1</span><span class="pl_instructor">ד"ר א</span><span class="pl_recorddate">1/1</span></div></html>"""


def test_recording_discovery_and_hints(fake, mount):
    fake.add("GET", "/course/view.php", Reply('<a href="/mod/ouilvideocollection/view.php?id=300">הקלטות</a>'))
    fake.add("GET", "/mod/ouilvideocollection/view.php", Reply(COLLECTION_HTML))
    collections = client(mount).recording_collections("4321")
    assert [c["cmid"] for c in collections] == ["300", "301"] and "quicklinks=1" in fake.requests[-1].url
    (rec,) = collections[0]["recordings"]
    assert (rec.title, rec.playlist_id, rec.context_id, rec.mid, rec.course_db_id) == ("שיעור 1", "12", "55", "MID", "9001")
    assert rec.thumbnail == ""  # token-bearing thumbnail dropped


def test_stream_resolution_follows_cdn_redirects_with_allow_list(fake, mount, monkeypatch):
    fake.add("POST", "/mod/ouilvideocollection/actions.php", Reply({"media": {"cipher": "CIPHER"}}))
    fake.add("GET", "/local/ouil_video/player.php", Reply('{"file":"https:\\/\\/cdn.bynetcdn.com\\/r?x=1"}'))
    hops = iter(
        [
            Reply("", status=302, headers={"Location": "https://edge.bynetcdn.com/rss"}),
            Reply('<rss><source file="https://edge.bynetcdn.com/v/playlist.m3u8?a=1&amp;b=2"/></rss>'),
        ]
    )
    seen = []

    class FakeCdn:
        headers = {}

        def get(self, url, **kwargs):
            seen.append((url, kwargs.get("headers", {}).get("Referer")))
            reply = next(hops)
            import requests

            r = requests.Response()
            r.status_code, r.url = reply.status, url
            r._content = reply.body.encode()
            r.headers.update(reply.headers)
            return r

    monkeypatch.setattr(openu.requests, "Session", lambda: FakeCdn())
    rec = openu.Recording(title="t", playlist_id="12", context_id="55", mid="MID", course_db_id="9001")
    url = client(mount).resolve_stream(rec)
    assert url == "https://edge.bynetcdn.com/v/playlist.m3u8?a=1&b=2"
    assert body_of(fake.requests[0]) == {"action": "getplaylist", "context": "55", "playlistid": "12", "course": "9001", "mid": "MID"}
    assert seen[0] == ("https://cdn.bynetcdn.com/r?x=1", "https://opal.openu.ac.il/")


def test_stream_redirect_off_cdn_is_refused(fake, mount, monkeypatch):
    fake.add("POST", "/mod/ouilvideocollection/actions.php", Reply({"media": {"cipher": "C"}}))
    fake.add("GET", "/local/ouil_video/player.php", Reply('{"file":"https://cdn.bynetcdn.com/r"}'))

    class Evil:
        headers = {}

        def get(self, url, **kwargs):
            import requests

            r = requests.Response()
            r.status_code, r.url = 302, url
            r.headers["Location"] = "https://evil.example/x"
            r._content = b""
            return r

    monkeypatch.setattr(openu.requests, "Session", lambda: Evil())
    with pytest.raises(ProtocolError):
        client(mount).resolve_stream(openu.Recording(title="t", playlist_id="1", context_id="2", mid="m", course_db_id="3"))


def test_stream_needs_hints(mount):
    with pytest.raises(ProtocolError):
        client(mount).resolve_stream(openu.Recording(title="t"))


# -- portal JSON -------------------------------------------------------------------


def test_portal_reads_and_exam_system_casing(fake, mount):
    j = {"content-type": "application/json"}
    fake.add("GET", "/Main/ServiceRequests/GetApplicationRequestsByUser", Reply([{"StatusGroupId": "1"}, {"StatusGroupId": "2"}], headers=j))
    fake.add("GET", "/main/api/assignmentsApi/exams", Reply([], headers=j))
    c = client(mount)
    assert c.service_requests("2") == [{"StatusGroupId": "2"}]
    assert c.exam_portal("exams") == []
    assert fake.requests[-1].url.endswith("/main/api/assignmentsApi/exams")  # lower-case 'main'
    with pytest.raises(ValueError):
        c.exam_portal("../secrets")


def test_portal_html_instead_of_json_means_expired(fake, mount):
    fake.add("GET", "/Main/StudyPrograms/GetSearchItems", Reply("<html>login</html>", headers={"content-type": "text/html"}))
    with pytest.raises(ProtocolError):
        client(mount).study_program_search()


def test_request_form_uses_codeToCreate_and_catalog_sends_csrf(fake, mount):
    j = {"content-type": "application/json"}
    tree = [{"code": "1", "description": "root", "subTypes": [{"code": "2", "codeToCreate": "22", "idToCreate": 5, "description": "leaf"}]}]
    fake.add("GET", "/Main/api/generalApi/getServiceRequestTypes/44", Reply(tree, headers=j))
    for path in ("getGlobalParams", "studentCoursesSpecial"):
        fake.add("GET", f"/Main/api/generalApi/{path}", Reply([], headers=j))
    fake.add("GET", "/Main/api/serviceRequestsApi/permissions/22", Reply({}, headers=j))
    fake.add("GET", "/Main/api/serviceRequestsFilesApi/types/22", Reply([], headers=j))
    fake.add("GET", "/Main/api/serviceRequestsFilesApi/constraints", Reply({}, headers=j))
    form = client(mount).service_request_form("2")
    assert form["type"]["code"] == "2"
    assert any("requestCode=22" in r.url for r in fake.requests)
    with pytest.raises(ValueError):
        client(mount).service_request_form("nope")

    fake.add("GET", "/Main/GlobalRequest", Reply('<input name="__RequestVerificationToken" value="TOK">'))
    fake.add("GET", "/Main/api/serviceRequestsApi/searchRequests", Reply([], headers=j))
    client(mount).service_request_catalog(query="x")
    assert fake.requests[-1].headers["X-RequestVerificationToken"] == "TOK"


def test_registration_search_url_rules():
    url = openu.registration_search_url("2027a", query="חדווא", course_id="20109", subject="5", area="99", credits="all", days="101010")
    from urllib.parse import parse_qs, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["Semester"] == "2027a" and q["CourseIDs"] == ",20109," and q["Subject"] == ",5,"
    assert q["GeographicalArea"] == ",01,02,03,04,05,06,07,09," and "Credits" not in q
    assert q["Degree"] == "0" and q["Days"] == "101010" and q["Page"] == "1" and q["InstructionHours"] == "00:01"
    with pytest.raises(TypeError):
        openu.registration_search_url("2027a", bogus="1")
