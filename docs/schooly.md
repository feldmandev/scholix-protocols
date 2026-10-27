# Schooly (סקולי)

Schooly is a school-management platform with a web app at `https://app.schooly.co.il/` and a Flutter mobile app (`il.co.schoolapps.schoolyapp`). Both talk to `https://api.schooly.co.il`.

**Evidence**

- Endpoint list: **Static**. Extracted from the Flutter AOT strings of the official Google Play build **1.2.6** (version code 51, updated 2026-10-21; base APK SHA-256 `42e42fae61e54a9dd8835d13d5c4763bae5f468bb5eec835e74daed8aaa58679`) and cross-checked against the public production web client, including its pupil-card and weekly-timetable parsers.
- Request/response shapes used in grades and timetable parsing: **App** (`SchoolyApi`, `SchoolyParsing`) and **Fixture**.
- Unauthenticated `GET /api/users/me` → JSON with HTTP 401: **Live** (the only live check recorded).
- Authenticated flows: **no live run recorded**. Whether a given school exposes grades or a timetable to an account is not something the protocol can tell you.

**Python:** `scholix.schooly.SchoolyClient`.

## Transport

- JSON over HTTPS with cookies **and** a bearer token (the server sets cookies; the client also keeps the returned token and sends `Authorization: Bearer <token>`).
- Headers used by the client: `Accept: application/json`, `Origin: https://app.schooly.co.il`, `X-Client-Platform: android`.
- Responses are usually `{"success": true, "data": {…}}`; some payloads are bare. A non-2xx status or `success: false` is an error with `message` or `error` text.
- HTTP 401 on a non-`/api/auth/` path means the session expired.

## Sign-in: national ID + one-time code

Schooly signs in by national ID (`userId`), then a code delivered to a contact method registered at the school (phone or e-mail).

1. **`POST /api/auth/identify`** `{"userId": "012345678"}` → `contactOptions[]`, each `{type: "phone"|"email", masked: "05*****12", …}`. Keep only `phone` and `email`.
2. **`POST /api/auth/send-otp`** `{"userId", "contactType", "contactValue"}`. `contactValue` is the **masked** string as returned in step 1 (falling back to `value`).
3. **`POST /api/auth/verify-otp`** `{"userId", "otpCode": "123456", "otp": "123456", ["school_id": "…"]}`. The code is six digits and is sent under **both** `otpCode` and `otp`. The response carries the session token as `signedToken` or `token`, either at the top level or nested one level (`data.data` with `data.token`). It also carries `schoolEntities[]`.
4. If the account belongs to **several schools** and you sent no `school_id`, `schoolEntities` has more than one entry: choose one and submit step 3 again with `school_id`. The Android app re-submits the same code; whether Schooly accepts a reused code is its server's behavior and is not documented here.
5. With a school chosen, **`POST /api/schools/switch-school`** `{"school_id": "…"}` sets the school scope.
6. **`GET /api/users/me`** restores the profile. It must contain `student_login_id`; if not, treat the sign-in as failed.

### Profile and linked students

`users/me` returns the signed-in person. A **parent** account lists children under `current_user.childrenPupils` (or `.children`); a student account is its own single "child". The pupil key everywhere else is `student_login_id`. Names are `student_f_name` / `student_l_name`.

## Data endpoints

All `GET` on `https://api.schooly.co.il/`, authenticated.

| Path | Query | Returns |
| --- | --- | --- |
| `v1/schools/periods` | | `{periods: [{_id, academicYear, …}]}` — assessment periods. |
| `v1/academic/pupil-card` | `student_login_id`, `year`, `period` | The pupil's assessments for a year/period. |
| `v1/schedule/weeklyTimeTable` | `ttDate=yyyyMMdd` (the week's **Sunday**), `pupilId`, `includeOverlay=true`, `overlayEventsMode=published` | Weekly timetable with published overlays. |
| `v1/schedule/lesson-hours` | `scope_id` | Bell times for a scope. |

### Grades (`pupil-card`)

Request once per period of the chosen academic year (`periods[].academicYear == year`; use no `period` if none match) and merge, removing duplicate assessment IDs. Each element of `assessmentDetails[]`:

```json
{ "_id": "…", "studentId": "…", "value": 90, "text": "…", "freeText": "…",
  "event": { "name": "…", "subjectName": "…", "typeName": "…", "dt": "20261015",
             "weight": 20, "maxValue": 100, "published": true, "draft": false, "_deleted": false } }
```

Rules (**App**/**Fixture**):

- Skip rows whose `event` is `_deleted`, `draft`, or has `published: false`.
- Skip rows owned by a different student (`studentId`/`student_login_id` set and unequal).
- Skip rows with neither `value` nor `text`/`freeText`. **A numeric `0` is a real grade**; textual evaluations are kept.
- `event.dt` is `yyyyMMdd` (shown as `dd/MM/yyyy`) or an ISO timestamp (first 10 characters).
- `maxValue` defaults to 100 when absent.

### Weekly timetable

The response carries (at top level, in `data[]`, or in `data.byDate[]`) these arrays: `subjects` (`_id`, `shortName`/`name`), `groups` (`_id`, `teachers[]` ids, `CODE_RAMAT_KVUTSA`), `teachers` (`_id`, `name` or `student_f_name`/`student_l_name`), and the lesson lists:

| List | Meaning |
| --- | --- |
| `ttGroups` | The **base** timetable. |
| `ttSubjects` | The timetable **with published overlay applied** (changes, cancellations, moves). When non-empty, use it for the "updated" view. |

Lesson fields: `day` (**`0` = Sunday**), `slot` (0-based period index), `group_id`, `group_name`, `subject`/`subjectName`, `effectiveRoom_name`, `teacherNames`, `note`, `isVoided`, `voidReason`, `movedTo`, `_id`.

- **Period number** = `slot + firstHour`, where `firstHour` comes from the top-level `firstHour`, else `scope.firstHour`, else `scope.blob.firstHour`, else 0.
- **Cancelled:** `isVoided` is true or `voidReason` is non-empty. **Moved away:** `movedTo` is non-empty — drop it from the updated view (the lesson shows up at its new slot as another entry).
- **Times:** from `lesson-hours`: choose the hour set whose `class_ids` is empty (else the first), then `periods[slot]` gives `{start, end}`.
- `scope_id` is `data[0].scope._id` or `data[0].scope_id`.
- The week starts on Sunday, and `ttDate` must be that Sunday. The Android app, when it is Saturday and the user asks for Sunday, requests the following week instead (the Python client takes the week start as an argument and does not do this).

## Python

```python
from scholix.schooly import SchoolyClient

client = SchoolyClient()
contacts = client.identify("012345678")
client.send_code(contacts[0])                     # masked phone or e-mail
schools = client.verify_code(input("code: "))
if schools:                                       # several schools on the account
    client.verify_code(code, school_id=schools[0]["_id"])

for g in client.grades(2027):
    print(g.subject, g.name, g.value)
for lesson in client.timetable():                 # published changes applied
    print(lesson.day, lesson.period, lesson.start, lesson.subject, lesson.cancelled)

saved = client.export_session()                   # token + cookies; treat as a secret
client.restore_session(saved)                     # raises SessionExpired if rejected
```

## Not covered

Attendance, messages, assessments planning, and every teacher/administrator screen visible in the web bundle (`AssessmentPlanner`, `Lessons`, `ScheduleConsole`, …).
