# Open University of Israel — Sheilta

"Sheilta" (שאילתא) is the Open University's student portal: `https://sheilta.apps.openu.ac.il/Main/`. Authentication goes through the university's SSO at `https://sso.apps.openu.ac.il/`.

**Evidence:** **App** (`OpenAUPlatform`) for everything below, and **Live** in the sense that the author's Scholix and a private companion tool read courses, grades, assignments and profiles from a connected account in October 2026. The wider portal surface in [What exists beyond this document](#what-exists-beyond-this-document) was inspected read-only from Sheilta's shipped JavaScript on 2026-10-17 and is **not** reproduced here. No exam attempt, assignment, request or registration was ever submitted. The Python port has not been run live.

**Python:** `scholix.openu.OpenUClient`.

## Sign-in

Three inputs: user name, password and **student ID** (`p_mis_student`).

1. `GET https://sheilta.apps.openu.ac.il/Main/Courses` — starts a session and sets cookies.
2. `POST https://sso.apps.openu.ac.il/process` as a form:

   | Field | Value |
   | --- | --- |
   | `p_user` | user name |
   | `p_sisma` | password |
   | `p_mis_student` | student ID |
   | `T_PLACE` | `https://sheilta.apps.openu.ac.il/Main/Courses` |

3. `GET …/Main/Courses` again. Success means the page is not a login page **and** the portal's own check agrees:

   `GET …/Main/api/homeApi/isAuthenticated` → the plain text `true` or `false`.

**What "is a login page" means** (the Android app's heuristic): the final URL's host is `sso.apps.openu.ac.il`, or its path contains `/login`, or the body contains `name="p_user"` or `name="p_sisma"`.

Stale cookies can shadow fresh SSO cookies; the Android app clears old cookies for both hosts before signing in. A new `requests` session has the same effect.

The Android app handles no CAPTCHA or second factor here. If the SSO ever demands one, the sign-in will not establish a session and the client raises `AuthenticationError`; that case has not been observed or tested.

## Courses and grades

`GET https://sheilta.apps.openu.ac.il/Main/api/coursesApi/get` with `Accept: application/json` → a JSON **array** (the body may start with a UTF-8 BOM; strip it). If the response is not an array, ask `isAuthenticated`: `false` means the session ended.

Each element (fields used):

| Field | Meaning |
| --- | --- |
| `courseId` / `id` | Course number |
| `courseName` / `name` | Title |
| `semester` | Term, e.g. `2026ב`. Normalize: lower-case, `א`→`a`, `ב`→`b`, `ג`→`c`, keep only digits and `a`–`c` → `2026b`. First four digits are the year; the last letter is the semester (`c` = summer). |
| `statusDesc` / `statusCodeDisp` | Status text (e.g. contains `בלימוד` = studying, `נרשם` = registered) |
| `semesterStartDate` | ISO date |
| `finalGrade`, `examGrade`, `points` | Strings; the literal `null` means absent |
| `assigmentScores` (sic) or `assigmentScoreModel` | The assignments |

A course is keyed `"<courseId>:<normalized term>"`; the same course can recur across terms.

### Assignments

Each assignment (the first list present of the two names above) is reduced to, using the first present key of each group:

| Output | Source keys |
| --- | --- |
| `number` | `assignmentNo` |
| `type` | `assignmentTypeDesc`, `assignmentType`, `typeDesc`, `type` |
| `grade` | `scoreValue`, `grade`, `score` |
| `gradedAt` | `scoreDate`, `scoreUpdateDate`, `scorePublishedDate`, `scorePublicationDate`, `gradeDate`, `gradeUpdateDate`, `updatedAt` |
| `date` | `registrationDate`, `submitDate`, `submissionDate`, `examinationDate`, `examDate`, `assignmentDate` |
| `weight` | `weight`, `assignmentWeight` |
| `status` | `statusDesc`, `status` |

**Visibility is enforced client-side.** Skip an assignment whose `isPublic` is explicitly false (`false`, `"false"`, `0`). If `isScorePublic` is explicitly false, keep the assignment but **drop its grade**. Never surface an unpublished score.

### Building the grade list

For one course, in order:

1. each assignment with a non-blank grade, **de-duplicated** by `number` (or, with no number, by `type|grade|date`); name = `type number`;
2. `Final grade` from `finalGrade`, if present;
3. `Exam grade` from `examGrade`, if present **and not already** among the assignments (an assignment whose type contains `exam`, `מבחן`, `בחינה` or `בחינת` and whose grade equals `examGrade` counts as the same grade).

### Course ordering used by the app

Studying first (`בלימוד`, or `נרשם` with a start date on or before today), then others, then future registrations; ties by start date, then name.

## What exists beyond this document

Sheilta's JavaScript exposes far more than courses and grades: request (בקשות) history and catalog with forms and attachments, the new exam and task system, registration offerings and course search, public timetable and course descriptions, personal study programs and degree-requirement data, messages (`/Main/Messages/MessagesSearchResult`, `/Main/Messages/MessageContent`), and the assignments API (`/main/api/assignmentsApi/tasks`, which returned HTTP 500 for one connected account in October 2026). Those contracts were inspected read-only and are intentionally left out until they can be documented with the same care as the above. Writes (submitting requests, uploads) were never exercised.

## Python

```python
from scholix.openu import OpenUClient

client = OpenUClient()
client.login("username", "password", "123456789")
for course in client.courses():
    print(course["key"], course["name"], course.get("status"))
    for grade in client.grades(course):
        print("   ", grade.name, grade.value)
```
