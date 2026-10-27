# Mashov (משוב)

Mashov is the school information system used by most Israeli schools. Students and parents use the web client at `https://web.mashov.info/students/`, which talks to a JSON API under `https://web.mashov.info/api`. Teacher and admin functions are out of scope.

**Evidence:** **App** (`MashovPlatform` in Scholix Android, used with real accounts). The constants in the login body are those of the official web client. SMS sign-in and device-token restore are **App**. No dated live run of the Python port is recorded.

**Python:** `scholix.mashov.MashovClient`.

## Identity: school, year, user

Mashov addresses an account by three values, all required to sign in:

| Field | Meaning |
| --- | --- |
| `semel` | The Ministry's school code. `GET /api/schools` is public and returns `[{"semel", "name", …}]`. |
| `year` | The calendar year in which the school year **ends** (school year 2026/27 → `2027`). Mashov rolls it over in July. |
| `username` | The student's or parent's Mashov user name. |

## Authentication

All three routes end the same way: the response sets session cookies and returns the account tree, and two **headers** that the client must keep.

| Header | Use |
| --- | --- |
| `X-Csrf-Token` (response) | Send it back as `X-Csrf-Token` on every later request. Without it data calls fail. |
| `devicePass` (response) | A long-lived "private device" token. Store it to sign in later with no password and no SMS. Mashov **rotates** it on every login; always keep the newest. |

### Password — `POST /api/login`

```json
{
  "semel": "123456", "year": "2027", "username": "…", "password": "…",
  "isPrivateDevice": true, "IsBiometric": false,
  "appName": "info.mashov.students",
  "apiVersion": "3.20210425", "appVersion": "3.20210425", "appBuild": "3.20210425",
  "deviceUuid": "<stable per install>", "devicePlatform": "android",
  "deviceManufacturer": "…", "deviceModel": "…", "deviceVersion": "…"
}
```

Send `Accept: application/json, text/plain, */*` and `Referer: https://web.mashov.info/students/`. A non-2xx response means the sign-in failed; the body is not needed.

`isPrivateDevice: true` is what makes Mashov issue a `devicePass`.

### SMS one-time code — **App**

1. `POST /api/user/otp/request` `{"semel": <int>, "username": "…", "cellphone": "05XXXXXXXX", ["captcha": "…"]}`. `cellphone` must be the ten-digit number on the account.
   - HTTP **429** → rate-limited. Stop.
   - HTTP **403** with a body mentioning `captcha` → a CAPTCHA token is required.
2. Verify by calling **`POST /api/login` again with the code in `password`**. There is no separate verify endpoint.

Codes are 4–8 digits. Never store one; after a successful SMS sign-in, keep only the `devicePass`.

### Device token — `POST /api/loginDevice`

Body: the device fields only (`appName`, `apiVersion`, `appVersion`, `appBuild`, `deviceUuid`, `devicePlatform`, `deviceManufacturer`, `deviceModel`, `deviceVersion`). Header: `devicePass: <stored token>`. Response is the same account tree plus a fresh `devicePass`. The Android app sends the same `deviceUuid` it signed in with; whether Mashov requires that is untested. HTTP failure means the token was revoked or expired; fall back to password or SMS.

### The account tree

```json
{ "accessToken": {
    "displayName": "…",
    "children": [ { "childGuid": "…", "privateName": "…", "classCode": "יא", "classNum": 3 } ]
} }
```

`children` is a list: a student account has one entry, a **parent** account has one per child. `childGuid` goes into every data URL. `classCode` is a Hebrew grade letter (`א`…`יב`; the client strips `׳ ״ " '` and maps it to `1`…`12`).

## Data endpoints

All are `GET`, relative to `/api`, with the CSRF header. `{guid}` is a `childGuid`. HTTP 401/403 means the session ended.

| Path | Response |
| --- | --- |
| `students/{guid}/grades` | Array of grades (below). |
| `students/{guid}/timetable?year=YYYY` | Array of weekly slots (below). |
| `bells` | `[{"lessonNumber", "startTime", "endTime"}]` — the school's period times. May be empty. |
| `students/{guid}/behave?start=YYYY-MM-DD&end=YYYY-MM-DD&year=YYYY` | Attendance/behavior events during lessons. |
| `students/{guid}/outBehave?start=…&end=…&year=…` | Events outside lessons. |
| `schools` | Public school list (no auth). |

### Grade

Fields used: `subjectName` (fall back to `groupName`), `gradingEvent` (fall back to `gradeType`), `grade` (number or `null`), `textualGrade`, `rangeGrade`, `eventDate`, `gradeType`, `teacherName`.

Value rule: use `grade` when it is not `null` — **`0` is a real grade**. Otherwise use `textualGrade`, then `rangeGrade`.

### Timetable slot

```json
{ "timeTable": { "day": 1, "lesson": 2, "roomNum": "12" },
  "groupDetails": { "subjectName": "…", "groupName": "…",
                    "groupTeachers": [ { "teacherName": "…" } ] } }
```

- `day` is `1` = Sunday. `lesson` is the period number; entries with `lesson ≤ 0` are not lessons.
- **Slots carry no clock times.** Join `lesson` to `bells[].lessonNumber`. A few deployments inline times on the slot (`startTime`, `lessonStart`, `fromHour`, …); the Android app probes those spellings first. The Python client uses `bells` only.
- Bell times look like `08:15:00` or `1970-01-01T08:15:00`; extract `\d{1,2}:\d{2}`.
- `groupTeachers` can repeat a name; de-duplicate.
- Mashov publishes no separate "original" and "updated" timetable; there is one.

### Behavior events

Raw event dictionaries; field names vary by event type, so the Android app probes several (`achvaName`/`eventName`/`eventType`/`lessonType` for the label, `lessonDate`/`eventDate`/`timestamp` for the date, `reporter`/`lessonReporter`/`teacherName`, `justified` or `justificationId > 0`). The Python client returns them unmodified, tagged with `_source`.

## Python

```python
from scholix.mashov import MashovClient, list_schools

school = next(s for s in list_schools() if "…" in s["name"])
client = MashovClient(semel=school["semel"], username="…", year=2027)
client.login("password")                 # or request_otp(phone) + login_with_otp(code)
client.device_pass                        # keep this; next time:
# client.login_with_device_pass(saved)

for grade in client.grades():
    print(grade.subject, grade.name, grade.value)

for lesson in client.timetable():         # day 0 = Sunday, times from /bells
    print(lesson.day, lesson.period, lesson.start, lesson.subject)

client.select_student(client.students[1].guid)   # parent accounts
```

## Not covered

Messages, homework, the Mashov "events" calendar, and everything a teacher can do. The Android app returns empty lists for messages.
