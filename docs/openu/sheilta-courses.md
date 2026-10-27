# Sheilta: courses, grades, meetings, exams, assessment documents

**Evidence:** **App**. Live reads of courses, grades, assignments, calendar, exams and scanned notebooks passed against a connected account in October 2026.

Base: `https://sheilta.apps.openu.ac.il`. Sign in first ([authentication](authentication.md)).

## Course list: `GET /Main/api/coursesApi/get`

Header `Accept: application/json`. Returns a JSON **array** (strip a UTF-8 BOM). A body that is not an array, or HTML, means the session ended. HTTP 500: refresh the page session once (see authentication) and retry.

### Course row

| Field | Meaning |
| --- | --- |
| `courseId` / `id` | Course number (string or number; leading zeros vary). |
| `courseName` / `name` | Title. |
| `semester` | Term, Hebrew or Latin (`2026ב`). Keep the raw value for the meetings API. |
| `statusDesc`, `statusCodeDisp` | Status text, Hebrew. First non-empty wins. |
| `semesterStartDate` | ISO date. |
| `finalGrade`, `examGrade`, `points` | Numbers or numeric strings; `""`/`null`/`"null"` mean absent. |
| `studyCenter`, `studyGroup` | Needed for the meetings API. |
| `activityCode` | Needed for the exam-detail page; default `01`. |
| `assigmentScores` (sic) or `assigmentScoreModel` | The assessments. Use the first that is an array. |

### Status classification

Check in this order (first match wins):

| Text matches | Status |
| --- | --- |
| `בוטל` / `מבוטל` | `canceled` |
| `הצלחה` / `סיים` / `עבר` | `did` |
| `בלימוד` | `this_semester` |
| `נרשם` | `this_semester` if `semesterStartDate` is today or earlier, else `upcoming` |
| anything else | `did` |

(The Android app uses a slightly different 3-way ranking for sorting; both agree on "studying" vs "registered".)

### Assessment (assignment) fields

First present key of each group:

| Output | Source keys |
| --- | --- |
| number | `assignmentNo` |
| type | `assignmentTypeDesc`, `assignmentType`, `typeDesc`, `type` |
| grade | `scoreValue`, `grade`, `score` |
| graded at | `scoreDate`, `scoreUpdateDate`, `scorePublishedDate`, `scorePublicationDate`, `gradeDate`, `gradeUpdateDate`, `updatedAt` |
| date | `registrationDate`, `submitDate`, `submissionDate`, `examinationDate`, `examDate`, `assignmentDate` |
| weight | `weight`, `assignmentWeight` |
| status | `statusDesc`, `status` |
| status code | `statusCode` |
| per-question scores | `questionScores[]` with `questionNo`/`questionNumber`/`number` and `score`/`scoreValue`/`grade` |

Visibility rules are enforced by the client, not the server: skip `isPublic` explicitly false (`false`, `"false"`, `0`); if `isScorePublic` is explicitly false, keep the row but drop its score.

**Exam detection:** the type text contains `בחינ` or `מבחנ`.

**Artifact availability** (what can be downloaded for an exam), derived from the raw row:

| Artifact | Available when |
| --- | --- |
| personal scanned questionnaire | any key matching `questionnaireScanned` is truthy (`true`, `1`, `"true"`, `"yes"`, `"כ"`) |
| exam questionnaire | exam and not the above |
| scanned notebook | exam and (a key matching `examScanned`/`notebook` is truthy, or there are question scores, or the exam has a grade). The hub worker overrides with the explicit booleans `hasExamFileId`, `hasQuestionnaireFileId`, `hasQuestionnaire` when present. |
| appeal | exam, and a URL anywhere in the row whose key or value matches `objection|appeal|irur`, or such a truthy key, or (exam with a grade) |

The raw row also exposes `examinationDate` (the real sitting date) and `hasExamFileId` booleans; prefer them to inferred values.

## Meetings: `GET /Main/api/coursesApi/getStudyGroupMeetings` and `…/getStudyGroupLabMeetings`

Query: `course`, `semester`, `studyCenter`, `studyGroup`, copied **verbatim from the course row** (do not normalize the semester). Each endpoint returns an array; call both and concatenate. A non-2xx or non-array answer from one is skipped, not fatal.

| Field | Use |
| --- | --- |
| `meetingDate` | first 10 characters, `yyyy-MM-dd` |
| `fromHour`, `toHour` | `H:MM[...]`, normalize to `HH:MM` |
| `typeDescription` | e.g. lecture, tutorial |
| `position` | location |
| `onlineMeeting` | boolean |
| `studyGroup` | falls back to the row's |
| `isCanceled` | `true` → drop the meeting |

A meeting without a valid date and both hours is dropped.

## Exams (legacy pages)

Two legacy pages (windows-1255 if the `Content-Type` says so, else UTF-8).

### Summary list: `GET /pls/dmyopt2/course_info.Home_Test`

An accordion of alternative-assessment sittings. For each `button.accordion`:

- Heading text matches `קורס:\s*(\d+)\s*-\s*(.*?)\s*\|\s*סמסטר לימוד:\s*(\S+)` → course id, name, study semester.
- Its panel is the next `div.panel`. Inside it, in document order: `p.covid_p` paragraphs (keep the last two seen) and `table.content_tbl.covid_table`.
- A paragraph containing `תאור מתכונת הבחינה:` is the exam format; one containing `סמסטר בחינה:` is the exam semester.
- The table's header cells map to the first data row's cells: `תאריך בחינה`, `שעת התחלה`, `שעת סיום`, `מקום בחינה`, `פרטי מרכז הוראה`. A row of text containing `נרשמת` is the registration state; one starting `הערה:` is a note.

De-duplicate by course id (first wins).

### Per-course detail: `GET /pls/dmyopt2/bchinot.bchinot_screen`

| Query | Value |
| --- | --- |
| `p_kurs` | course id |
| `p_semester`, `p_semester_bchina` | the normalized semester (`2026b`) |
| `P_SUG_CHALUFA` | `26` |
| `p_KOD_PEILUT_KURS` | the row's `activityCode`, else `01` |
| `p_sug_rashuma` | `GMAR` |

The page carries the **confirmed** sitting, and only once one is published. Over the page text:

| Field | Pattern |
| --- | --- |
| date | `זימון למועד\s+(\d{1,2}/\d{1,2}/20\d{2})` |
| start | `שעת התחלת הבחינה\s+([0-2]?\d:[0-5]\d)` |
| location | `מרכז הבחינה\s+(.*?)\s+חומר עזר המותר בשימוש:`; split on `תחבורה ציבורית:` for the transport text |
| allowed materials | `חומר עזר המותר בשימוש:\s*(.*?)\s+בחר מועד בחינה` |
| sitting | `input[name=p_moed_act]` value |

If date **and** start time are not both found, there is no confirmed exam yet and the course yields nothing.

## Assessment documents: `GET /Main/api/coursesApi/downloadScanned`

Returns the file itself (a PDF; use the response `Content-Type`).

| Query | Value |
| --- | --- |
| `course` | course id |
| `semester`, `assignmentSemester` | the **Hebrew-letter** semester (`2026ב`) |
| `assignmentNo` | the exact number from the assessment row |
| `questionnaire` | `true` for the exam questionnaire |
| `questionnaireScanned` | `true` for the student's personal scanned questionnaire |
| `examScanned` | `true` for the checked notebook |

Exactly one of the three flags is `true`; the others are `false`. Use the exact `semester` and `assignmentNo` the course row returned, not the exam date. A login page means the session ended. Treat the file as private: it is the student's own work.

## Appeals

There is no appeal endpoint to call. If an assessment row carries an `https` link whose key or value matches `objection|appeal|irur`, that is the appeal page; resolve it, check the host against the allow-list, and open it in a browser. Resolving a link submits nothing.

## Assignments (Opal + Sheilta merge)

The assignment list comes from Opal ([opal-content](opal-content.md#assignment-list)) and is merged with the Sheilta rows above, keyed by the assignment number normalized as a number (`"07"` and `7` are the same):

- Opal supplies the due and start dates, status text and code, URL, and a grade when shown.
- Sheilta supplies type, registration and exam dates, weight, per-question scores and artifact flags; its grade wins when non-empty; Opal's status wins when non-empty.
- Rows only in one source are kept as-is. Sort numerically by number when both are numbers, else Hebrew collation.
- A course with no Opal site (`resolve_course_site` returns nothing) still yields its Sheilta assessments.
