# Lemida — Bar-Ilan's Moodle

Lemida (`https://lemida.biu.ac.il`) is Bar-Ilan University's Moodle. It uses Microsoft sign-in with MFA. **There is no Python client for it in this repository**, by design: see [Why not](#why-not-an-http-client).

**Evidence:** **App** and **Live**. Course discovery and homework reading ran on the author's phone against a real account (a full synchronization of 19 homework items on 2026-10-03). The Moodle method names below were exercised through a signed-in browser session.

## Why not an HTTP client

Two facts make a plain-`requests` client unreliable:

1. **Sign-in is Microsoft** (`login.microsoftonline.com`) with Authenticator number-matching or SMS. Passwords and MFA must be entered by the person; nothing here replays a Microsoft login form.
2. **Lemida's Perfdrive anti-bot verification rejects native HTTP replays** even when given the same cookies. The Android app therefore reads pages and calls the AJAX service **from inside a WebView** that holds the browser session, and does not use OkHttp for Lemida.

What *is* reusable is the shape of the Moodle conversation, below. If you already have a signed-in browser (Playwright, a WebView, a browser extension), you can drive it the same way.

## Sign-in shape

- Open `/my/`. Not signed in if the `<body>` has class `notloggedin` or no `a[href*="/login/logout.php"]` link exists.
- The Moodle entry point is a link to `/auth/multioauth/login.php`, which hands off to Microsoft.
- On the Microsoft page, SMS verification is offered through the "sign in another way" control (`#signInAnotherWay`) and a `OneWaySMS` choice (`[data-value="OneWaySMS"]`). The code box is `#idTxtBx_SAOTCC_OTC` and its submit button `#idSubmit_SAOTCC_Continue`.
- Do not request more than one SMS per attempt: record that you selected SMS *before* clicking, so a navigation error cannot trigger a second request. Never retry a failed code automatically. CAPTCHA and Authenticator approval need a person.

## Moodle web-service calls

All are `POST /lib/ajax/service.php?sesskey=<M.cfg.sesskey>` with `Content-Type: application/json`, same-origin, with the browser's cookies. Body: `[{"index": 0, "methodname": "<name>", "args": {…}}]`. The response is an array with one object; `error: true` means failure, with `exception.errorcode`. `invalidsesskey`, `requireloginerror` and `servicerequireslogin` mean the session ended. A response that ends up on another origin means a verification page intervened.

`sesskey` and the user id come from the page's `M.cfg`. Treat the `sesskey` like a session credential.

| Method | Use |
| --- | --- |
| `core_course_get_enrolled_courses_by_timeline_classification` | Course discovery. Args `{classification: "allincludinghidden", limit: 50, offset: N, sort: "fullname"}`. Response `{courses: […], nextoffset}`. Paginate until `nextoffset == offset`; reject a `nextoffset` that goes backwards, or equals `offset` while the batch is non-empty. |
| `core_courseformat_get_state` | Per-course state, including visible assignments, quizzes and workshops, completion and dates. |

Observed to return `servicenotavailable` on this deployment (read-only probes): `core_course_get_contents`, `mod_assign_get_assignments`, `mod_quiz_get_quizzes_by_courses`. Moodle's standard mobile/web-service functions are **not** a given here.

Homework in the Android app comprises assignments, quizzes and workshops; plain resource links are not homework. New activities are detected by Moodle activity ID; the first successful sync after enrollment (or an account change) sets a baseline and does not announce old items.

## Constraints worth knowing

- Treat "the session expired" as a normal state; recover once, then ask the person to sign in.
- The Android app treats a different Moodle `userId` as a new account and starts a fresh baseline instead of merging.
- Dates learned from an activity's detail page are not in the list JSON; keep them across refreshes.
- Read-only. The app never submits an assignment or starts a quiz attempt.
