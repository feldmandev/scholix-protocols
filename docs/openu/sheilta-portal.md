# Sheilta portal APIs: requests, exam system, study programs, registration

**Evidence:** **Static** (read from Sheilta's shipped JavaScript on 2026-10-17), then called **read-only** against a connected account. Reads of request history, catalog, forms, the new exam-system permissions, study programs and bundled degree data were verified live. **No write below was exercised live.** The `assignmentsApi/tasks` read returned HTTP 500 for that account.

Base `https://sheilta.apps.openu.ac.il`. Note the two casings: most routes are under `/Main/…`, the new exam system under **`/main/api/assignmentsApi/…`** (lower-case `main`).

Requests carry the SSO cookies. Calls marked 🔑 also need `X-RequestVerificationToken` ([authentication](authentication.md#the-aspnet-anti-forgery-token)). All JSON endpoints answer a login page or non-JSON when signed out; check `Content-Type` before parsing.

## Service requests (פניות)

Students file and track requests (certificates, exemptions, appeals, …).

### Existing requests

| Call | Returns |
| --- | --- |
| `GET /Main/ServiceRequests/GetApplicationRequestsByUser` | Array of every request incl. history: IDs, application number, status, `StatusGroupId` (`1`/`2`/`3`), dates, course, semester. Filter locally. |
| `GET /Main/ServiceRequests/GetApplicationRequestMoreInfo?applicationCode=&applicationNumber=` | `{Url: …}`, the page that shows one request. Fetch that page in a browser (it is a rendered page, often a legacy one) to read the details. |
| `GET /Main/api/serviceRequestsFilesApi/files/{requestId}` | Attachment metadata for a request. |
| `GET /Main/api/serviceRequestsFilesApi/download/{fileId}?requestId=` | The attachment bytes. |

### Catalog 🔑

| Call | Returns |
| --- | --- |
| `GET /Main/api/serviceRequestsApi/getUserData` | Categories, hot requests, instructions, links for this user. |
| `GET /Main/api/serviceRequestsApi/getCategoryData?category=<code>&hotRequest=` | Request types in a category. |
| `GET /Main/api/serviceRequestsApi/searchRequests?freeText=` | Free-text search. |

### General request types and forms

| Call | Returns |
| --- | --- |
| `GET /Main/api/generalApi/getServiceRequestTypes/{group}` | The type/subtype tree. `group` defaults to **`44`** (the website's general group). Nodes: `code`, `description`, `parentCode`, `subTypes[]`, `idToCreate`, `codeToCreate`. |
| `GET /Main/api/generalApi/getGlobalParams?requestCode=<code>` | Dynamic form parameters. |
| `GET /Main/api/generalApi/studentCoursesSpecial?requestTypeCode=<code>` | Courses eligible for this request type. |
| `GET /Main/api/serviceRequestsApi/permissions/{code}` | Whether this account may create it. |
| `GET /Main/api/serviceRequestsFilesApi/types/{code}` | Allowed attachment types. |
| `GET /Main/api/serviceRequestsFilesApi/constraints?requestType=<code>` | Attachment size/count limits. |

`<code>` is `codeToCreate` when present, else `code`. Only **leaf** types (no `subTypes`) can be created. The hierarchy has at most three levels; deeper or legacy/specialized forms are website-only.

### Writes (documented, never exercised)

| Call | Effect |
| --- | --- |
| `POST /Main/api/generalApi/add/` 🔑 | **Creates a real request.** JSON body: `Level1Code`, `Level2Code`, `Level3Code` (the chain of codes, unused levels `""`), `title` (the chain's descriptions joined by ` / `), `userId` `""`, `studentId` `""`, `date` (`dd/MM/yyyy` today), `freeText` (≤ 2000 chars), `files` (`[{contactId, fileId, comment}]`), `anotherData` `""`, `service`, `course`, `semester`, `group`, `idToCreate`, `params` (`[{name, value}]`). The response may demand payment; creating does not pay and checkout must not be followed automatically. |
| `POST /Main/api/serviceRequestsFilesApi/file/upload?requestType=&contactId=` 🔑 | Multipart field `file`; ≤ 10 MiB. Returns the uploaded file reference to put in `files`. |
| `POST /Main/api/serviceRequestsFilesApi/file/delete?fileId=&requestId=` 🔑 | Deletes an attachment. |

Never retry an ambiguous result of any of these.

## The new exam and task system

`GET /main/api/assignmentsApi/<section>` (note lower-case `main`), JSON:

| `section` | Content |
| --- | --- |
| `permissions` | What this account may do. |
| `exams` | Pending exams. |
| `tasks` | Pending tasks (**HTTP 500** observed for one account, October 2026). |
| `exams/submissions`, `tasks/submissions` | Submissions. |
| `messages` | Exam-system messages. |
| `files/constraints` | Upload size/type limits. |

Per exam: `GET /main/api/assignmentsApi/exams/{semester}/{courseId}/{assignmentId}` → details; append `/state` or `/questionnaires/metadata`. These only read; starting an exam is a separate, undocumented action.

## Study programs

Base `GET /Main/StudyPrograms/…`:

| Route | Query | Returns |
| --- | --- | --- |
| `GetIndexData` | | The student's programs, obligations, recognitions, messages, limits. Drop `Menu`, `CurrentUser`, `ActorUser` (navigation and identity). |
| `GetSearchItems` | | The official program index. |
| `SearchProgramsBySubSubjectID` | `subSubjectId` (comma-joined codes from the index) | Matching programs. |
| `GetProgramXML` | `programCode`, `programId`, `parentCode`, `status` (`3` when a `programId` is given, else `1`) | A program's XML with course/credit requirements. |
| `GetCourses` | `subjectIds`, `levelIds`, `freeText`, `mindExpanding` (`true`/`false`) | Courses for the degree planner. |
| `GetAllDepartments`, `GetKnowledgeBranchs` (sic) | | Filter lists. |
| `GetSubjects` | `knowledgeBranch` | |
| `GetLevels` | `subjectId` | |
| `GetCourseById` | `courseId`, `isAdditional=true` | One course. |

Reading these never saves or submits a program.

## Pages rendered by the site

These are HTML pages (some legacy framesets whose child frames load after the outer DOM event). Fetch them and parse, or drive a browser:

| Purpose | URL |
| --- | --- |
| Registration filters | `/CoursesRegistration/Pages/GroupsSearch.aspx` |
| Registration search results | `/CoursesRegistration/Pages/GroupSearchResults.aspx?…` (below) |
| Exam registration (legacy) | `/pls/dmyopt2/course_info.Home_Test` |
| Personal timetable | `/pls/dmyopt2/LUACH_SHANA.first` |
| Academic timetable (semesters, registration periods, holidays, exam periods) | `https://academic.openu.ac.il/yedion/timetable/pages/default.aspx` |
| Public course description | `https://www.openu.ac.il/courses/{5-digit id}.htm?t=h` |

### Registration search query

`GET /CoursesRegistration/Pages/GroupSearchResults.aspx` with:

| Query | Meaning |
| --- | --- |
| `Semester` | offered semester, **including upcoming ones** |
| `CourseName` | text |
| `CourseIDs` | `,id,` |
| `Degree`, `Subject`, `LearningLanguage`, `Openinglevel`, `Credits`, `AD` (department), `GeographicalArea`, `LearningCenter`, `InstructionHours`, `Days`, `InstructionType`, `Page` | filters |

Defaults the site sends: `Degree=0`, `GeographicalArea=,01,02,03,04,05,06,07,09,`, `InstructionHours=00:01`, `Days=111111`, `Page=1`. List-type filters (`Subject`, `LearningLanguage`, `Openinglevel`, `Credits`, `AD`, `GeographicalArea`, `LearningCenter`) are comma-**wrapped** (`,5,`); the value `all` removes the parameter; area `99` means "leave the default". Searching only reads; nothing is added to the registration basket.

## Bundled catalogs (not live)

The author's website ships snapshots of the full course catalog and every degree's requirement tree (mandatory/elective groups, alternatives, credit quotas, constraints). They are static data compiled from the public site, not a live registration view; use `course_offerings`-style search for current availability.

## Python

```python
from scholix.openu import OpenUClient

client = OpenUClient(); client.login("user", "pass", "123456789")
rows = client.service_requests()                 # all requests, history included
tree = client.service_request_types()            # group 44
form = client.service_request_form("<leaf code>")
print(client.exam_portal("permissions"))
from scholix.openu import registration_search_url
print(registration_search_url("2027a", query="חדווא"))     # a URL; fetch and parse the HTML page
```

Create/upload/delete are intentionally not implemented.
