# Opal: course sites, sections, assignments, books, booklets, profile

**Evidence:** **App**. Live reads of course pages, sections, assignment lists, books, booklets and the profile passed against a connected account in October 2026. All of this is HTML scraping of Moodle pages with OpenU plugins; selectors are only as stable as the theme.

Base `https://opal.openu.ac.il`. Follow redirects. A final URL on `sso.apps.openu.ac.il` or containing `/login/index.php` means the session ended.

## Finding a course's Moodle site

Sheilta knows a course by `(courseId, semester)`; Opal by a numeric **Moodle course id**.

`GET /ouil/course.php?course=c{courseId}&semester={normalized, e.g. 2026b}` is a legacy entry URL that redirects to the course page. (Sheilta's own `getLinks` API is unreliable and returns HTTP 500, so build this URL directly.) The Moodle id is the `id` query parameter of the **final URL**, or, failing that, the number in a `course-<n>` class in the page. No id means the course has no site (many do not): return "no course site", not an error.

Use the same numeric id for every other Opal call. Many readers verify ownership: the response `<body>` must carry the CSS class `course-<moodleId>`, otherwise the activity is not in that course.

## Sections and activities

`GET /course/view.php?id={moodleId}` (optionally `&sectionid={s}&from=nav`).

**Sections** (first selector set that yields anything wins):

1. `.sections-nav-list .dropdown-menu a[href*='sectionid=']`, `a.dropdown-item[href*='sectionid=']`
2. `[role='main'] a.section[href*='sectionid=']` (title from `.sectionname:not(.hidden)`)
3. `li.section[data-sectionid]` (title from `.sectionname, .section-title, h3`)

The id is the `sectionid` query parameter (or `data-sectionid`). The **current** section is `[data-sectionid].current|.active`, else `a.nav-current-section` / `li.type_structure.current > a`.

**Booklet hint:** the page contains `booklet.viewbook|tafrit|download`, `pls/myopr/booklet`, or the text `חוברת הקורס`.

**Activities of one section** come from the OUIL navigation block:

- `.block_ouil_navigation li.type_structure.current_parent|contains_current|contains_direct_current > ul > li.type_activity`
- `.course-content li.activity`
- when a section id is requested: the `li.type_structure` whose first `<p>` link has that `sectionid` (or whose text equals the section title) → its child `li.type_activity`
- plus any `#region-main [role='main'] a[href*='/mod/'][href*='id=']`

For each: take the `a[href*='/mod/']`; `cmid` = its `id` parameter, `module` = the path segment after `/mod/`, `title` = `.item-content-wrap`/`.instancename` text or the anchor text, `completed` = the item has `mod_completed` or contains `.completion-btn-success`. De-duplicate by `cmid`.

**When a section page lists no activities** (the nav is lazy-loaded), ask Moodle's navigation endpoint: `POST /lib/ajax/getnavbranch.php`, form `elementid=expandable_branch_30_{sectionId}`, `id={sectionId}`, `type=30`, `sesskey={sesskey}`, headers `X-Requested-With: XMLHttpRequest`, `Accept: application/json`. Response `{children: [{name, link, class}]}`: `link` (unescape `&amp;`) gives `cmid` and `module`; `class` containing `mod_completed` means completed.

### Activity type from module

| Module / title | Type |
| --- | --- |
| `combopage`, `page`, `resource` | `content` |
| `quiz` | `quiz` |
| `ouilforum`, `forum`, or title contains `פורום` | `forum` |
| contains `video` | `video` |
| `folder` | `file` |
| `assign`, `ouilassign` | `assignment` |
| anything else (`url`, `oufcard`, …) | `link` |

## Assignment list

`GET /mod/ouilassign/index.php?id={moodleId}&quicklinks=1`. Rows: `#ouilassign_index_list_container > li` (and `> span > li`). Per row:

| Field | Source |
| --- | --- |
| number | first `span` inside `[index-item="name"] a` |
| name | that anchor's text without the span |
| type | `data-assigh-type` (sic) attribute; else `online` if the link is under `/mod/quiz/`, else `file` |
| due date | `[index-item="duedate"]` text, minus `.ouilassign_index_list_title` |
| start date | `[index-item="timeopen"]` |
| status text | `[index-item="status"]` |
| status code | `ouilassign-status` attribute of `[index-item="status"] [ouilassign-status]` |
| grade | `[index-item="grade"]` |
| url | the anchor `href`, made absolute (HTTPS only) |

Merging with the Sheilta rows is described in [sheilta-courses](sheilta-courses.md#assignments-opal--sheilta-merge).

## E-books

`GET /local/ouil_ebook/index.php?id={moodleId}&type=digital&quicklinks=1`. Each `.ebook_cell[id^='ebook']`:

| Field | Source |
| --- | --- |
| id | the cell id minus `ebook`; digits only, else skip |
| title / part / subtitle | `.ebook_name`, `.ebook_part`, `.ebook_subtitle` |
| file | `a.ebook_digital_link` `href-content` or `href` when it points at `/local/ouil_ebook/signedpdf.php` |

No signed link means the student has **not accepted the book's terms** (`requires_consent`).

The file is a **signed** URL: `https://opal.openu.ac.il/local/ouil_ebook/signedpdf.php?…`. Accept only that exact host and path before fetching it. Fetching it with the session cookies returns the PDF.

**Accepting terms** is a write (consent): `POST /local/ouil_ebook/ebook_actions.php`, `application/x-www-form-urlencoded`, `action=subscribe&course={moodleId}&book={bookId}&sesskey={sesskey}`, headers `X-Requested-With: XMLHttpRequest`, `Referer: <the e-books page>`. JSON `{file: "<signed url>"}`. Verify the book is in the page's list first; a missing `sesskey` means the session ended. This records the student's consent: only do it on an explicit instruction. (Implemented in the author's tooling; not in the Python client.)

## Course booklet

The course booklet (חוברת הקורס) is in a legacy area, `https://sheilta.apps.openu.ac.il/pls/myopr/…`, windows-1255, its own SSO target ([authentication](authentication.md#the-plsmyopr-hop)). Use the course id **without leading zeros** (`p_kurs`) and the normalized semester (`p_semester`).

| Page | Purpose |
| --- | --- |
| `booklet.viewbook?p_semester=&p_kurs=` | A frameset. Fetch it **first**: it primes the booklet session, and its main frame points at `booklet.download`. |
| `booklet.tafrit?p_semester=&p_kurs=` | The menu; send `Referer` = the viewbook URL. Fallback if viewbook lists nothing. |
| `booklet.download?in_file_name={file}&p_kurs=&p_semester=` | The PDF. |

Items: every `a[href*="booklet.download"]`, `frame[src*=…]`, `iframe[src*=…]`. Take `in_file_name`; **accept only `^[\w.-]+\.pdf$`** and rebuild the download URL yourself rather than trusting a link; the label is the link text (default `חוברת הקורס`). De-duplicate by file name.

## Profile

`GET /user/profile.php`:

- Name: from `<title>` or `.page-header-headings h1`, stripping the prefix `פרופיל:` and suffixes like `: פרופיל ציבורי…` and `שימו לב!…`.
- User id: `id`/`userid` parameter of `a[href*="user/edit.php"]` or `a[href*="preferences.php?userid="]`.
- E-mail: first `a[href^="mailto:"]`.
- Sections: each `.profile_tree section.node_category`: title from `h3`; `dt`/`dd` pairs are labeled entries (with the links in the `dd`); bare navigation links `li:not(.contentnode) > span > a`.

The profile contains personal data. Do not log it.
