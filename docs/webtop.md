# Webtop (Smart School)

Webtop is the parent/student portal of Smart School (`webtop.smartschool.co.il`), an Angular single-page app over a JSON API at `https://webtopserver.smartschool.co.il/server/api/`. Pupils sign in either with Webtop's own username/password or through the Ministry of Education ([moe-sso.md](moe-sso.md)).

**Evidence**

- MOE → Webtop sign-in, password branch: **Live**, 2026-09-08, two fresh HTTP sessions, one authorized account.
- Password-login field encryption: **Live**, vectors captured from the site's `encryptStringToServer` on 2026-10-17 (bundle `main.878d6d935b432b30.js`). They are the regression vectors in `tests/test_webtop.py`.
- Grades, discipline events, class timetable, pupil schedule, events board, inbox: **App** (`WebtopPlatform`, `WebtopMailbox`).
- Mailbox write operations: **App**, listed below but **not** in the Python client.

**Python:** `scholix.webtop.WebtopClient`.

## Session model

Authentication yields a cookie named `webToken` on `.smartschool.co.il`. Every API call is a `POST` with a JSON body and these headers: `language: he`, `rememberMe: 0`. There is no bearer token; the cookie is the session.

- **HTTP 401/403, or an empty body, means the session ended.** The Android app refreshes once and retries; the Python client raises `SessionExpired`.
- Responses are `{"status": true, "data": …}`. `status: false` is a failure; `data` can legitimately be `null`.
- Certificate validation works in Python: the 2026-09-08 runs used normal TLS verification. The Android app uses a permissive trust manager for these calls; the reason is not documented, and the Python client does not copy it.

## Sign-in

### Webtop password — `POST user/LoginByUserNameAndPassword`

```json
{ "Data": "<AES(UserName + \"0\")>", "UserName": "…", "Password": "…",
  "deviceDataJson": "{\"isMobile\":true,\"os\":\"Android\",\"browser\":\"Chrome\",\"cookies\":true}" }
```

`Data` is a field the website computes with `encryptStringToServer` and the server expects. It is **AES-256-CBC** over the *JSON-encoded* string `UserName + "0"` (so a user name containing `"` or `\` is escaped), PKCS#7-padded, Base64-encoded:

| | |
| --- | --- |
| Key (32 bytes, ASCII) | `01234567890000000150778345678901` |
| IV (16 bytes, ASCII) | `6543210987654321` |

The key and IV are constants in the public JavaScript bundle; they protect nothing, they just have to match. The site previously used PBKDF2 with a salt; that format is gone.

Regression vectors (**Live**, 2026-10-17):

| Input | Output |
| --- | --- |
| `test-user0` | `xIKv4WqO5o7fO/LgmjaVSw==` |
| `0123456780` | `c39fRupZjW7EDKUoH0L09g==` |
| `abc"\0` | `uDppSUp599nPYfFXTJr6DA==` |

Outcomes:

| Response | Meaning |
| --- | --- |
| `status: true`, `data` object | Success. |
| `status: false` | Rejected credentials. |
| `message: "get2FA"` | Webtop wants a second factor. Not automatable. |
| `data.mustChangePassword: true` | Change the password on the website. |

### Ministry of Education — `POST user/LoginMoe`

First run the MOE conversation ([moe-sso.md](moe-sso.md)) with entry URL `https://www.webtop.co.il/applications/loginMOENew/default.aspx`. It ends at `https://webtop.smartschool.co.il/account/loginMoe?key=<key>`. Then:

```json
{ "rememberMe": "false", "key": "<key>", "UniqueId": "<fresh uuid4>",
  "deviceDataJson": "{\"isMobile\":false,\"isTablet\":false,\"isDesktop\":true}" }
```

with headers `Origin: https://webtop.smartschool.co.il`, `Referer: https://webtop.smartschool.co.il/`, `language: he`, `rememberMe: 0`, `X-XSRF-TOKEN:` (empty).

Notes:

- `deviceDataJson` is a JSON **string**, not an object.
- `key` is parsed like Angular parses query strings: a literal `+` becomes a space; `%2B` stays `+`. `urllib.parse.parse_qs` does the same.
- `rememberMe` accepted both `""` and `"false"` in the live runs.
- A valid sign-in must set a non-empty `webToken` cookie. The client treats its absence as a failure.
- `POST dashboard/CheckToken` with `{}` is a cheap "is this cookie still good" probe (**Live**).

### The profile (`data` of the sign-in response)

| Field | Use |
| --- | --- |
| `userId` | **Encrypted** pupil id, accepted wherever an API wants `studentID`. Numeric in some responses; treat as a string. |
| `firstName`, `lastName` | Display name. |
| `institutionCode` | School code; goes into timetable and events requests. |
| `classCode`, `classNumber` | Grade and class. The timetable selector is the string `"<classCode>|<classNumber>"`. A parent account has neither. |
| `studentId` | National-ID-like student number (present for some accounts). |
| `userType`, `institutionName` | Informational. |

## Endpoints

All `POST` JSON, relative to `…/server/api/`.

### Pupil card — study year, semesters, encrypted id

`PupilCard/GetSettingsList` `{"id": <studyYear>}` → `data`:

- `divisionsList[]`: `division_id`, `year_division` (`1` = first semester, `2` = second), `division_name`. The IDs are year-specific; look them up, do not hard-code. (The Android app falls back to `938`/`939` when the lookup fails. Their provenance is unknown and they are probably specific to one year; the Python client raises instead of guessing.)
- `children[0].id`: the encrypted student id to use as `studentID`.
- `currentStudyYear`: the year the server resolved your request to.

**Study year** is the calendar year in which the school year ends, rolling over in **September** (October 2026 → `2027`).

| Endpoint | Body | `data` |
| --- | --- | --- |
| `PupilCard/GetPupilGrades` | `{"studyYear", "moduleID": 6, "periodID", "studentID"}` | Array of `{subject, title, grade, date}`. |
| `PupilCard/GetPupilDiciplineEvents` (sic) | `{"studentID", "moduleID": 4, "periodID", "studyYear"}` | `{diciplineEvents: [...]}` (some builds spell it `disciplineEvents`). Fields: `eventType`, `eventDate`, `subjectName`, `teacherName`, `isJustified`, `justifiedReason`, `autoJustifyInEvents`, `enableJustified`, `remark`. |
| `PupilCard/GetPupilScheduale` (sic) | `{"weekIndex": 0, "viewType": 0, "studyYear", "studentID", "classCode": <int>, "moduleID": 10}` | The pupil's own week, resolving parallel groups. Returned raw. |

`periodID` is the `division_id` of the chosen semester, or **`0` for the whole year**. A discipline event with `autoJustifyInEvents` is justified even if `isJustified` is false.

### Class timetable (Shotef)

`shotef/ShotefSchedualeData` `{"institutionCode", "selectedValue": "10|2", "typeView": 1}` → `data` is an array of days:

```json
{ "dayIndex": 2,
  "hoursData": [ {
      "hour": 3, "hourName": "09:45 - 10:30",
      "scheduale": [ { "subject": "…", "subjectLevel": "…", "teacherPrivateName": "…",
                       "teacherLastName": "…", "room": "…", "hour": 3 } ],
      "changes":   [ { "isAddition": false, "type": "…", "definition": "…", "privateName": "…", "lastName": "…", "room": "…" } ],
      "exams":     [ { "title": "…" } ] } ] }
```

- `scheduale` is Webtop's spelling; some responses say `schedule`. Accept both.
- `dayIndex` is `1` = Sunday. The Android app also accepts `day` or `dayId` in its place.
- The class timetable lists **every parallel group**; use `GetPupilScheduale` to find the pupil's own.
- `hourName` holds the clock times. The Android app takes the two `\d{1,2}:\d{2}` matches and sorts them, so the order in the string is evidently not reliable (likely right-to-left text).
- `room` can be the literal string `"null"`.
- `changes` describes substitutions, cancellations and added lessons; an added lesson has `isAddition` or the Hebrew `תוספת שיעור` in `type`/`definition`. The Python client's `timetable()` returns the **original** lessons only; read `changes` from `class_schedule()` if you need them. Applying changes correctly is subtle (an addition has no `scheduale` entry to attach to), and the Android app's logic for it is not reproduced.

`shotef/EventsTestsBoard` `{"institutionCode", "selectedDate": "<UTC instant>", "selectedView": 3, "loadFilters": true, "studyYear"}` → the Events & Tests board. `selectedDate` is local midnight as a UTC instant, e.g. `2026-10-26T22:00:00Z`. Returned raw.

### Messages (inbox)

| Endpoint | Body | `data` |
| --- | --- | --- |
| `messageBox/GetMessagesInbox` | `{"PageId": n, "LabelId": 0, "HasRead": null, "SearchQuery": ""}` | Array: `messageId`, `subject`, `student_F_name`, `student_L_name` (sender), `sendingDate`, `hasRead`, `filesWereAttached`. |
| `messageBox/GetMessagesInboxData` | `{"MessageId", "FilterId": 0, "IsInbox": true, "hasRead": null}` | `{messageData: {subject, privateName, lastName, sendingDate, messageContent (HTML), filesList[{fileName, fileUrl, fileSize:{size, sizeName}}]}}` |

`HasRead: null` is the only value observed; the meaning of `true`/`false` is unverified.

Attachment URLs must be fetched **with the session cookies**, over HTTPS, and only from `webtopserver.smartschool.co.il` / `webtop.smartschool.co.il`. The Android downloader refuses insecure links and insecure redirects.

The mailbox has more endpoints, all under `messageBox/` unless noted (**App** only; not in the Python client). Read: `initMessagesData`, `InitData` (recipient directory), `GetSignature`, `GetMessagesOutbox`, `GetMessagesDraft`, `GetMessagesDeleted`, `GetMessagesOutboxData`, `GetAllRecipients`, `GetMessageFolders`. Write: `MarkMessagesAsRead`, `DeleteMessages`, `MessageRecovery`, `MoveToFolder`, `InserEditFolder` (sic), `DeleteFolder`, plus multipart send/draft/upload calls and `Notification/…` (`GetNotificationsSettings`, `GetChildrenList`, `GetNotificationList`, `UpdateNotificationReadStatus`, `DeleteNotifications`). Use `WebtopClient.post(endpoint, payload)` to experiment, and open an issue with a **sanitized** capture if you want one promoted to a typed method.

## Python

```python
from scholix.webtop import WebtopClient

client = WebtopClient()
client.login_password("webtop-user", "password")      # Webtop's own account
# or, with Ministry credentials:
# client.login_moe("moe-user", "moe-password")        # may raise CaptchaRequired

for g in client.grades():                             # current year and semester
    print(g.subject, g.name, g.value)
for g in client.grades(year=2027, semester="ab"):     # whole year
    ...
for lesson in client.timetable():                     # original class timetable
    print(lesson.day, lesson.period, lesson.start, lesson.subject, lesson.teacher)

print(client.events_tests_board())                    # raw
print(client.inbox(page=1))                           # raw dicts
```

`client.http.export_cookies()` persists the session.

## Unknowns

- Whether MOE sign-in is stable for accounts that need CAPTCHA/OTP. Those branches raise; they were never bypassed.
- Server-side lifetime of a `webToken` and of the one-time `key`.
- Webtop parent accounts: the code paths exist (a parent profile has no class) but are only fixture-tested.
