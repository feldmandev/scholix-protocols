# Opal: activity pages, forums, quizzes

**Evidence:** **App**, from the author's client and its tests. Reading activities and forums passed live in October 2026. The quiz write path (start, answer, finish) is implemented there but **starting a quiz can consume an attempt and start a timer**; it is documented here, not exercised for this documentation, and not in the Python client.

Base `https://opal.openu.ac.il`. Every page check: ends on SSO/login → session expired; `<body>` lacks class `course-{moodleId}` → not in this course (404).

## Reading one activity

`GET /mod/{module}/view.php?id={cmid}&from=nav`. `module` comes from the section list; accept only `^[a-z][a-z0-9_]{0,40}$` before splicing it into a URL.

Special case: a `url` module that is really a forum (its parsed type is `forum`) contains a link to `/mod/ouilforum/view.php`; follow it, by `id` (→ activity) or `f` (→ forum id).

The returned detail has: `title` (first `h1`/`h2`), `type`, sanitized `html`, `links` (only `https://opal.openu.ac.il/` URLs containing `/pluginfile.php/` or `/mod/resource/view.php`), `embeds`, and a module-specific block below.

### Sanitizing content (do this if you render it)

Course content is authored HTML. Remove `script, style, input, button, iframe, object, embed, video`; unwrap `form`; delete every `on*` attribute; keep only a `color` declaration of `style` (and only if it is a plain color: `#hex`, a name, `rgb()/hsl()`); make `href`/`src` absolute; drop non-HTTPS targets. The author's client maps the two common author colors to semantic classes (dark red/`#C00000`/`#FF0000` → error, blue/`#0070C0` → accent) instead of passing raw colors.

### Embeds

`iframe[src]` is classified by its class/parent class/URL:

| Match | Type |
| --- | --- |
| `cmpage_book`, `bookview`, `application/pdf` | `document` |
| `ouil.?video`, `video.?player`, `player.php`, `player/`, `jwplayer` | `video` (needs resolving, see [recordings](recordings.md#embedded-course-videos)) |
| host is `youtube.com`, `youtube-nocookie.com`, `player.vimeo.com` or `opal.openu.ac.il` | `iframe` |
| anything else | ignored |

`video[src]`/`video source[src]` become `video` embeds; `hls` is true for `.m3u8` or `/hls` URLs. Height is clamped to 240–1000 (default 560). Embed titles come from the surrounding `.cmpage_itemtitle`.

### File-card pages (`oufcard`)

`table.oufcard` with an `a[href*='/pluginfile.php/']` is one file. Kind from `.headerrec img[title]` (default `קובץ`), updated text from the `.dataTitle` row, description from the first remaining text row. Output `files[]` with `title`, `url`, `description`, `meta`.

## Assignment activity (`ouilassign`)

Selectors (all under `#region-main`):

| Output | Selector |
| --- | --- |
| `details[]` | `#ouilassign_view_section_details [index-item]`, label `.ouilassign_inner_title` |
| `submission[]` | `#ouilassign_view_submission_content .ouilassign_submission_info_row`, label `.ouilassign_section_row_title` (rows whose label contains `אסמכתא`, a receipt reference, are dropped) |
| `instructions` | `#ouilassign_view_section_guide .ouilassign_section_content` (paragraph/list text, newline-joined) |
| `original_files[]` | `#ouilassign_view_section_assignment a[href]` |
| `submitted_files[]` | `#ouilassign_view_submission_content .ouilassign_section_row_file_item a[href]:not([aria-hidden='true'])` |
| `checked_files[]` | `[id^='ouilassigntype_file_feedbacks_list'] a[href]`, `.ouilassign_view_section_feedback a[href]` |

**Download links of feedback files are built by script, not in `href`.** With the page's `"submission": <id>` (regex `["']submission["']\s*:\s*(\d+)`):

- annotated feedback (`#ouilassigntype_file_feedbacks_list_annotated a`): `/mod/ouilassign/view/{data-viewtype or annotated}/download.php?submissionid={id}&filenum={data-filepath or 1-based index}`
- checked/personal feedback (`…_checked a[data-filepath]`, `…_personal a[data-filepath]`): `/pluginfile.php/{data-filepath}?forcedownload=1`, or, if the path contains `/viewfeedback.php`, that URL with `forcedownload=1`.

Submitting an assignment is **not** implemented by the author's tooling.

## Forums (`ouilforum`)

| Call | Page |
| --- | --- |
| `GET /mod/ouilforum/index.php?id={moodleId}&quicklinks=1` | Forum list |
| `GET /mod/ouilforum/view.php?f={forumId}[&page=N]` | Discussions of one forum (page is 0-based) |
| `GET /mod/ouilforum/discuss.php?d={discussionId}` | One discussion with all posts |

**List:** `li.forumslist[data-forumid]`: title `.forum_list_title a`, description `.forum_body_content`, discussion count from `.forum_footer` text `דיונים:\s*(\d+)`, unread `.forum_unread .forumlist_number`, last update `.forum_clean_date`.

**Discussions:** `.ouilforum_discussion` (`data-discussionid`, title `.post_subject`) containing `.ouilforum_post`:

| Field | Source |
| --- | --- |
| id | `data-postid` |
| depth | class `level{n}`/`postlevel{n}`; else the number of `.indent` ancestors |
| title | `.post_subject` |
| author | `.of_post_author a` own text |
| date | `.of_postdate`, minus the prefix `פורסם בתאריך` |
| body | `.post_content` text |
| staff | the post contains `.role_icon` |
| links | `https` anchors inside `.post_content` |

**Paging:** `current_page` is the `page` query parameter (0 default); `page_count` is the largest integer in `.ouilforum_paging li` (min 1); `has_next` if `.ouilforum_paging a[rel='next']` exists.

Posting and replying are website-only (not implemented).

## Quizzes (`quiz`)

Standard Moodle quiz with OpenU's wrapper. **All quiz state changes below are real**: starting consumes an attempt and may start a countdown; finishing submits for grading. Only do them on explicit instruction and never retry an ambiguous response.

| Step | Request |
| --- | --- |
| Overview | `GET /mod/quiz/view.php?id={cmid}&from=nav` |
| Start / continue | If the page has `a[href*='/mod/quiz/attempt.php']`, follow it (continue). Else submit `form[action*='/mod/quiz/startattempt.php']` (POST the form's successful controls). No form → no attempt is available. |
| Read attempt | `GET /mod/quiz/attempt.php?attempt={id}&cmid={cmid}` |
| Save answers | The page's `form#responseform` (action must be `https://opal.openu.ac.il/mod/quiz/processattempt.php`). Its `input[name=attempt]` must equal the attempt id. POST the form's controls, replacing each answer's field (`delete` then `append` when non-empty), plus the first named submit control. |
| Summary | `GET /mod/quiz/summary.php?attempt={id}&cmid={cmid}` |
| Finish | The form containing `input[name='finishattempt'][value='1']` on the summary page, POSTed to `processattempt.php`. |
| Review | `GET /mod/quiz/review.php?attempt={id}&cmid={cmid}` |

Form action URLs are accepted only if HTTPS, host `opal.openu.ac.il` and the exact expected path.

### Parsed quiz page

`state`: `review` if the URL is `review.php`; else `attempt` if any `.que`; else `summary` if `.quizsummaryofattempt`; else `overview`. `can_start`: a start form or attempt link exists. `can_finish`: `finishattempt=1` exists.

- **Info:** lines from `#intro .no-overflow` and `.quizinfo p`; each split into a label/value: `…יפתח ב…` / `…יסגר|ייסגר|מסתיים ב…` as open/close times, else `label: value`, else label `מידע`.
- **Attempts:** `.quizattemptsummary tbody tr`: status `td.c0` (date in `.statedetails`), grade `td.c2`, review link `a[href*='/mod/quiz/review.php']` (attempt id from its `attempt` parameter).
- **Final grade:** `#feedback h3`, matched as `n / m` when possible.
- **Questions:** each `.que`: number `.qno`; type = the first CSS class not in `{que, deferredfeedback, notyetanswered, answered, correct, incorrect}`; text `.qtext`; options `.answer input[type=radio|checkbox]` and `.answer option` with their `name`, `value`, label and `selected`; free-text control `.answer input[type=text|number], .answer textarea`; feedback `.feedback, .outcome`.
- **Math:** rendered MathJax (`.mjx-chtml`) is replaced by MathML (from `data-mathml` or the inner `<math>`) or by the adjacent `script[type^='math/tex']` source rendered to MathML; `MathJax_Preview` and assistive nodes are removed; unknown attributes on rich question HTML are stripped to `href src alt title dir`.
