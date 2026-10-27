# Inbar — Bar-Ilan University

Inbar (`https://inbar.biu.ac.il/Live/`) is Bar-Ilan's student portal. It is a classic **ASP.NET Web Forms** application: server-rendered pages, `__doPostBack` navigation, and no JSON API. Sign-in is **SMS only** (an ID/passport number plus the mobile number registered with the university; no password).

Not to be confused with [Michlol](michlol.md), which Bar-Ilan's mathematics department runs separately.

**Evidence:** **Live** and **App**. Scholix Android's SMS sign-in, grade reading, year switching, pagination and period schedule were run on a device by the author in October 2026: one successful attempt measured 0.658 s for the SMS request, a code arriving 4.446 s later, and 1.134 s for verification plus grades (about 5.6 s total; one attempt, not a guarantee). A live schedule read returned eight lessons for academic year 2026, Summer. Real pages and grades are never stored in this repository; the tests use synthetic HTML. The Python port has not been run live.

**Python:** `scholix.inbar.InbarClient`.

## Why this needs care

- **The portal can refuse to send or verify SMS codes.** It reports this in a JavaScript `alert` — *"לא ניתן לשלוח עוד הודעות/לאמת קוד בשלב זה"* — while still rendering the code box. There is no stated expiry. The client detects the alert, raises `RateLimited`, and stops. Do not retry in a loop.
- **A POST must carry the form's full state.** Web Forms posts `__PageDataKey`, `__EVENTVALIDATION` and the other hidden fields back; stale state is rejected. Every request in this client re-reads the latest page and submits that page's *successful controls* (below).
- **Session expiry looks like a redirect to the login page.** A data read must never follow it, because visiting the login/authenticate page can start a new SMS challenge.

## Hosts and pages

| Page | URL |
| --- | --- |
| Login | `/Live/Login.aspx?ReturnUrl=%2fLive%2fStudentGradesList.aspx` |
| SMS verification | `/Live/Authenticate.aspx?ReturnUrl=…&AuthLevel=SmsAndNormal` |
| Grades | `/Live/StudentGradesList.aspx` |
| Period schedule | `/Live/StudentPeriodSchedule.aspx` |

All on `https://inbar.biu.ac.il` (port 443, normal TLS validation). Anything redirecting elsewhere is refused before credentials are sent. Cookies are the session; redirects are followed manually (up to 10).

## Posting a Web Forms page

Take `form#form1`. Submit:

1. every **successful control** of the form — `input`/`select`/`textarea` with a `name`, not `disabled`; skipping `submit`, `button`, `image`, `reset`, `file`; radios and checkboxes only if `checked`; a `select` contributes its `selected` options, or its first option if none is selected and it is not `multiple`; a control with no `value` attribute contributes `""` (or `on` for a checkbox/radio);
2. then the **overrides** for this step (replacing any control of the same name);
3. to the form's `action`, resolved against the page URL, with `Referer: <page URL>` and `Origin: https://inbar.biu.ac.il`.

**Only the chosen button** is submitted: pressing "Verify" must not also send `btnLogout`. Redirect handling: `303`, or `301/302` after a POST, becomes a GET.

## Sign-in

| Step | Request | Detail |
| --- | --- | --- |
| 1 | `GET Login.aspx` | Requires inputs `edtUsername`, `edtMobile` and the button `btnLogin`. |
| 2 | POST overrides `edtUsername=<id/passport>`, `edtMobile=<mobile>`, `btnLogin=<its value>` | The portal texts a code and returns the verification page (input `edtCode`). A page without `edtCode` means the details were not accepted. Check for the restriction alert here. |
| 3 | POST overrides `edtCode=<code>`, `btnVerify=<its value>` | Success lands on the grades page (reuse that response). If `edtCode` is **still** present the code was wrong; keep that new form state for a corrected code. Check for the restriction alert again. |
| – | Resend: POST override `btnSendSmsCode=<its value>` | The button is `disabled` until the portal's cooldown ends; the client waits 45 s. |

Codes are four to ten digits (five in practice). Never persist one.

Do not submit more codes against a refused challenge. The Android app additionally limits itself to one SMS request per 90 s across restarts; a library user should impose a similar limit.

## Grades — `/Live/StudentGradesList.aspx`

Scraped from a GridView: `table#ContentPlaceHolder1_gvGradesList`, plus the year picker `select#cmbActiveYear`. If the table is absent the session is gone.

**Columns are matched by header text, never by position.** Optional columns can disappear and others can move. Headers are Hebrew with English equivalents:

| Hebrew | English | Required | Field |
| --- | --- | --- | --- |
| קוד קבוצת קורס | Code | yes | course-group code |
| שם | Name | yes | course name |
| מרצה | Lecturer | | |
| תקופה | Period | yes | e.g. `סמסטר קיץ`, `סמסטר א'` |
| נ"ז | Credits | | |
| ציון עובר | Passing grade | | |
| ציון סופי | Final grade | yes | text of `span[id*=lblRowFinalGrade]`; blank = still pending |
| ת.עדכון | Updated | | |
| הערה | Remark | | |
| מטלות | Assignments | | nested table, below |
| בקשה לציון עובר | Pass request | | |

A data row must have exactly as many cells as the header row, all with `colspan=1`; anything else raises `InbarLayoutChanged` rather than risking a grade attached to the wrong course. Recognized empty states (a single cell reading `אין נתונים`, `No data` or `No records`, or all cells blank) mean "no courses this year" and keep the year choices, so a student can still go on to older years.

**Assignments** (inside the "Assignments" cell, a nested table):
- `tr.AssignmentText` with three cells → `name`, `weight`, `grade`. A blank grade means ungraded.
- A following 2-cell row that is not `tr.AssignmentHeader` is a **submission** attempt: `[date and moed label, grade]`. It also sets the assignment's date to the first token of the label (e.g. `01/08/2026 (A)` → `01/08/2026`).

**Semester** from `Period`: contains `קיץ` → summer (`c`); the first letter after `סמסטר` being `א` → `a`; otherwise containing `סמסטר` and `ב` → `b`.

**Course keys** include the year (`<year>:<code>`), because the same code recurs across years.

### Teaching groups

The portal lists a lecture and its tutorial as separate rows sharing a course-code prefix. Rows are grouped on (year, name, period, code with its last `-suffix` removed). Within a group, graded rows are kept (several independently graded groups stay separate); if none is graded, the row with the most credits is kept. The others are recorded in `related_groups`, not as empty courses.

### Years

`cmbActiveYear` lists the available years, with the current one `selected`. Switching year is a POST of **the latest grades form** with the selector's name set to the year and `__EVENTTARGET=<selector name>`, `__EVENTARGUMENT=` (empty). The client verifies the portal actually selected that year. Refreshing the same year is a fresh GET; switching years reuses the held page to avoid a GET before every older-year request.

### Pagination

If more courses exist than fit, the last row is a **pager**: one cell with `colspan > 1`, a `span` with the current page number, and links `javascript:__doPostBack('<target>','Page$N')` (or `Page$Next`, `Previous`, `First`, `Last`). Follow forward by POSTing the latest form with `__EVENTTARGET=<target>` and `__EVENTARGUMENT=Page$N`. The client requires each next page to be exactly `current + 1`, to be new, and to stay in the same year; at most 100 pages. Anything else raises and returns **no** partial list — a failed later page must not replace known-good data with half of it.

## Period schedule — `/Live/StudentPeriodSchedule.aspx`

One request covers the whole week. Grids: `table[id$=_gvPeriodSchedule]` (more than one is possible when several semesters are selected). Filters: the year picker `select#cmbActiveYear`, and the semester picker `select[id$=ddlPeriodTypeFilter2]`, whose values are `1` (semester A), `2` (B), `3` (summer), `5` (all semesters). Both change via the same postback technique as grades (`__EVENTTARGET` = the select's `name`).

Required headers: `יום` (day), `שעה` (time), `שם קבוצת קורס`, `קוד קבוצת קורס`, `תקופה`, `מרצה`, `חדר` (room). Optional: `שעות`, `נ"ז`, `ש"ש`, `עזרים`, `סוג מפגש`.

- Day: strip `יום`, spaces and quotes; accept the Hebrew letter (`א`…`ש`), full name, English name or 1–7. Sunday is index 0. An unrecognized day raises.
- Time: one or two `H:MM` values; hour 0–23, minute 0–59. **Sort them** — right-to-left text can reverse the printed order. One value is a start with no end.
- Inbar publishes real clock times. There are no numbered periods and no invented free periods.
- An empty year shows a single `אין נתונים` row: an empty timetable that still carries the year and semester choices.

## Unknowns

How long the SMS restriction lasts; whether other tenants use the same layout; attendance and messages are not implemented.

## Python

```python
from scholix.inbar import InbarClient
from scholix.errors import RateLimited

client = InbarClient()
client.request_sms("012345678", "0501234567")      # one SMS; don't call in a loop
try:
    first = client.verify_sms(input("code: "))      # lands on the grades page
except RateLimited:
    raise SystemExit("Inbar is refusing SMS right now; try later")

for year in first.years:
    for course in client.grades(year).courses:
        print(year, course.name, [(g.name, g.value) for g in course.grades])

page = client.schedule(year=2026, period="3")       # period values are in page.periods
for lesson in page.lessons:
    print(lesson.day, lesson.start, lesson.end, lesson.subject, lesson.room)

cookies = client.export_session()                   # keep as a secret; no SMS needed while valid
```
