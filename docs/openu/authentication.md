# OpenU authentication and sessions

**Evidence:** **App**, used with a real account. No CAPTCHA or second factor has been seen in this flow.

## Inputs

Three values, all required: user name (`p_user`), password (`p_sisma`) and **student ID** (`p_mis_student`, the national ID number).

## Sign-in

1. `GET https://sheilta.apps.openu.ac.il/Main/Courses` to start a session.
2. `POST https://sso.apps.openu.ac.il/process`, `application/x-www-form-urlencoded`:

   | Field | Value |
   | --- | --- |
   | `p_user` | user name |
   | `p_sisma` | password |
   | `p_mis_student` | student ID |
   | `T_PLACE` | where the SSO should return the browser |

   `T_PLACE` has been seen with two values that both work: `https://sheilta.apps.openu.ac.il/Main/Courses` (the Android app) and `https://sheilta.apps.openu.ac.il/pls/dmyopt2/sheilta.main` (the TypeScript client, which lands on the legacy home page and so primes the legacy `/pls/` session as well).
3. `GET` the `T_PLACE` URL again. If the final URL is a login page the credentials were rejected; the page's `#messageBox`, `.error` or `.error-message` text is the portal's reason.
4. Confirm: `GET /Main/api/homeApi/isAuthenticated` → `true`.

The session is a set of cookies spanning `sso.apps.openu.ac.il`, `sheilta.apps.openu.ac.il` and `opal.openu.ac.il`. Opal does not need its own login; visiting it with the SSO cookie authenticates it through a redirect chain, which is why Opal requests should *follow redirects*.

**Stale cookies can shadow fresh ones.** When re-signing in with a jar that already holds SSO cookies, clear the old `sso` and `sheilta` cookies first (the Android app expires them explicitly). A new HTTP session avoids the problem.

## Session checks and expiry

- `isAuthenticated` is the cheap, authoritative check.
- Any request that ends on `sso.apps.openu.ac.il` or `/login/index.php` has lost the session. Treat that as **expired**, not as a normal page.
- `GET /Main/api/coursesApi/get` returning **HTTP 500** is not necessarily expiry. The Sheilta API sometimes needs its page session refreshed: `GET /Main/Courses`, and if that is not a login page, repeat the API call once. A second 500 is a real failure (upstream faults sometimes arrive as an empty array, so never replace known-good cached data with an empty result).

## The `/pls/myopr/` hop

The booklet area (`sheilta.apps.openu.ac.il/pls/myopr/…`) is a **separate SSO target**. An unauthenticated or first request returns HTTP 200 with a tiny page whose script does `top.location.href = '…sso.apps.openu.ac.il/login…'`. Complete the hop once with the cookies you already hold:

1. Detect it: the body contains both `top.location.href` and `sso.apps.openu.ac.il/login`.
2. `GET https://sso.apps.openu.ac.il/login?T_PLACE=<the exact page URL, percent-encoded>`. With a live SSO cookie it redirects straight back, authenticated, and sets the `myopr` cookie.
3. Request the page again. If it still bounces, the session is gone.

## The ASP.NET anti-forgery token

Some Sheilta **writes and token-protected reads** need the header `X-RequestVerificationToken`. Get it from the hidden field `__RequestVerificationToken` in `GET /Main/GlobalRequest`. The Sheilta service-request catalog and every upload/delete/create call carry it; the JSON GETs that list requests or read the exam system do not. See [sheilta-portal](sheilta-portal.md).

## Opal session key

Opal (Moodle) actions that mutate state or call `lib/ajax/*` need a **`sesskey`**: a per-session token embedded in page scripts as `"sesskey":"<value>"` (regex `["']sesskey["']\s*:\s*["']([^"']+)`). Read it from a course or e-book page you just fetched. Treat it as a credential.

## Python

```python
from scholix.openu import OpenUClient

client = OpenUClient()
client.login("username", "password", "123456789")      # raises AuthenticationError on rejection
assert client.is_authenticated()
```

`login(..., target=...)` selects `T_PLACE`.
