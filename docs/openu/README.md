# Open University of Israel (OpenU)

The Open University is not one website. A student touches at least five systems that share one login but differ in technology, encoding and failure behavior. Knowing which system a page belongs to explains most of the quirks.

| System | Host / path | What it is | Document |
| --- | --- | --- | --- |
| **SSO** | `sso.apps.openu.ac.il` | Single sign-on form and session. | [authentication](authentication.md) |
| **Sheilta** (שאילתא), modern | `sheilta.apps.openu.ac.il/Main/…` | ASP.NET MVC app with JSON APIs: courses, grades, meetings, service requests, study programs, the new exam/task system. | [sheilta-courses](sheilta-courses.md), [sheilta-portal](sheilta-portal.md) |
| **Sheilta**, legacy | `sheilta.apps.openu.ac.il/pls/…` | Oracle PL/SQL pages, server-rendered HTML, often **windows-1255**, sometimes framesets. Exams, timetable, booklets. | [sheilta-courses](sheilta-courses.md#exams-legacy-pages), [opal-content](opal-content.md#course-booklet) |
| **Opal** (אופל) | `opal.openu.ac.il` | Moodle with OpenU plugins (`ouilassign`, `ouilforum`, `ouilvideocollection`, `ouil_ebook`, `oufcard`, `combopage`). Course sites, assignments, forums, quizzes, e-books, recordings, profile. | [opal-content](opal-content.md), [opal-activities](opal-activities.md), [recordings](recordings.md) |
| **Public site** | `www.openu.ac.il`, `academic.openu.ac.il` | Course descriptions and the academic timetable. | [sheilta-portal](sheilta-portal.md#pages-rendered-by-the-site) |

## Evidence

| Part | Label |
| --- | --- |
| Login, course/grade API, meetings, exam pages, assessment documents, the Opal readers (assignments, sections, activities, forums, quizzes, books, booklets, recordings, profile) | **App**: the author's OpenU client (TypeScript, in a private repository) and the Scholix Android app. Live reads passed against a connected account in October 2026 for courses, grades, assignments, calendar, notebooks, course pages, recording collections, forums, books and booklets, and the exam system. |
| Service requests, study programs, registration search, the new exam/task system, timetables | **Static**: contracts read from Sheilta's shipped JavaScript on 2026-10-17, then called read-only. Reads were verified live; the upstream `/main/api/assignmentsApi/tasks` returned **HTTP 500** for the connected account in October 2026. |
| Every operation that **writes** (create a request, upload/delete a file, answer or finish a quiz, accept book terms) | Contract is documented. Only the quiz and book-terms calls are implemented in the author's client; **no write was exercised live in this documentation's research** except as noted in their sections. Never retry an ambiguous write. |
| The Python client | **Fixture**: offline tests on synthetic data. |

Each document says what it covers and what it leaves out.

## What the Python client covers

`scholix.openu.OpenUClient`:

| Area | Python | Notes |
| --- | --- | --- |
| Sign-in, session check | yes | |
| Courses, grades, assignment scores | yes | |
| Weekly meetings (lectures, labs) | yes | |
| Exams (alternative assessments) | yes | |
| Course site discovery, sections, activities | yes | |
| Assignment list (Opal + Sheilta merge) | yes | |
| Scanned exam documents, appeal link | yes | |
| Profile | yes | |
| Forum list | yes | |
| E-books list, book PDF | yes | accepting terms is **not** implemented |
| Course booklet | yes | |
| Recordings and HLS stream | yes | |
| Service requests, study programs, registration (read) | yes (JSON endpoints) | the rendered-page ones are URL builders only |
| Activity detail HTML, assignment/forum/quiz parsing | documented only | |
| Quizzes (start/answer/finish) | documented only | start consumes an attempt and may start a timer |
| Any write | no | |

## Host allow-list

All of the author's tooling restricts every request to HTTPS on these hosts, so a redirect or a link in page content can never send the session elsewhere:

```
opal.openu.ac.il   opsrv.apps.openu.ac.il   sheilta.apps.openu.ac.il
sso.apps.openu.ac.il   www.openu.ac.il   www-e.openu.ac.il
```

Recording playback adds the CDN suffixes `bynetcdn.com` and `openu.ac.il` (HTTPS only, no user-info in the URL). The Python client enforces the same lists.

## Cross-cutting rules

### Semesters

OpenU terms are `YYYY` + a letter: **a** (א), **b** (ב), **c** (ג, summer). They arrive as Hebrew (`2026ב`) or Latin and are used in both forms:

- `normalize_semester("2026ב")` → `2026b`. Lower-case, replace the **first** `א`/`ב`/`ג` with `a`/`b`/`c`, then take the leading match of `^20\d{2}[abc]`.
- **Fallback.** If nothing matches, the TypeScript client substitutes a *guess from today's date* (`a` before May, `b` before October, else `c`) instead of failing. That hides bad input. The Python client raises `ValueError` instead and offers the guess as a separate helper.
- The Sheilta document API wants the **Hebrew** letter back (`sheilta_semester("2026b")` → `2026ב`); Opal's entry URL wants the Latin one.
- Match courses by `(courseId, semester)`, never by id alone: a student can have the same course in several terms. Course ids appear with and without leading zeros; compare after stripping them.

### Detecting a lost session

Sheilta and Opal do not return 401. A signed-out request ends up on a login page with **HTTP 200**. A response is a login page when the final URL's host is `sso.apps.openu.ac.il` or its URL contains `/login/index.php`. Check the *final* URL after redirects. The JSON course API additionally answers a non-array/non-JSON body when signed out; `GET /Main/api/homeApi/isAuthenticated` returns the plain text `true`/`false`.

### Encodings

Legacy `/pls/` pages are `windows-1255`. Decode by the `Content-Type` charset when present; the booklet area omits it, so always decode those as windows-1255. Everything else is UTF-8.

### Secrets inside OpenU URLs

Some OpenU links embed a one-time `p_token=` (or `token=`) in the query string. Never log them, never store them, and drop them from anything you republish. The author's client removes any string containing `p_token=` and any key matching `url$|token|payment|secretariat|secretary|contact|email|phone|mobile` from course rows before exposing them.

### Politeness

OpenU is a university system under exam-period load. The Android app debounces course-list refreshes, and the author's worker never retries a write automatically. Do the same: one request per user action, no retry loops.
