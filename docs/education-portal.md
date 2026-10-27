# Education Portal — matriculation (bagrut) grades

The Ministry of Education's **Students & Graduates Portal** (פורטל תלמידים ובוגרים), `https://students.education.gov.il`, shows a pupil's matriculation (bagrut) grades. It has no timetable, attendance or messages.

**Evidence:** **App** (`StudentsPortalPlatform`), verified by its author against a captured browser session. The data endpoints below are plain; the sign-in is the hard part.

**Python:** `scholix.eduportal.EducationPortalClient`. **It cannot sign in.** You supply the session from a browser.

## Why there is no login in the library

Sign-in goes through the MOE identity provider ([moe-sso.md](moe-sso.md)). For *this* portal that IdP is behind F5 Distributed Cloud Bot Defense (`server: volt-adc`). Its login POST requires an `x-security-csrf-token` header and a `csrt` query token produced by obfuscated `/TSbd/` browser script; neither appears in the served HTML or any cookie. Observed behavior with a headless client: the password is validated (a wrong one still returns `isError:true`) but the resulting session is never honored.

This contrasts with Webtop, whose MOE flow *did* work headlessly (**Live**, 2026-09-08). The difference between the two deployments has not been characterized. If you can make the portal work without a browser, a documented trace would be a welcome contribution.

The Android app signs in with an invisible browser engine, then makes plain HTTP calls with the cookie it captured. Do the equivalent: sign in at

```
https://apps2.education.gov.il/EduLogin/login.aspx?ReturnUrl=https://students.education.gov.il/matriculation-exams/grades-page
```

and, once back on the portal, copy the `Cookie` header and the `csrt` value.

## What the data endpoints need

Exactly two things:

1. the `education.gov.il` session cookie, sent as the `Cookie` header;
2. the **`csrt`** query parameter on every `/api/` URL. Without it the API answers **404**.

Also send `X-Requested-With: XMLHttpRequest`, `Accept: application/json, text/plain, */*` and `Referer: https://students.education.gov.il/matriculation-exams/grades-page`.

`csrt` is injected into the page by script. If it is missing or stale (a 404), re-fetch `/matriculation-exams/grades-page` (or `/`) with the cookie and read it with `csrt=([0-9]{6,})`, then retry **once**.

## Endpoints

`GET https://students.education.gov.il/api/<path>?csrt=<csrt>`

| Path | Response |
| --- | --- |
| `public/getuserinfo` | `{TZehut, FirstName, LastName, …}`. `TZehut` is the national ID; **`-1` or less means "not authenticated"** (that is how the SPA decides). |
| `bgr/fullgradespage` | `{grades[], hasmachot[], miktzoa[], config}` |
| `bgr/getgrades/<codes>` | Per-exam records. `<codes>` is the `CODE_MAARECHET` values joined with `-`. |

An expired session does **not** return an error status: it returns an HTML login redirect page. A client must treat "not JSON" (or a 3xx) as session expiry.

### Getting the grades

1. `bgr/fullgradespage` → for each `hasmachot[]`, collect `CODE_MAARECHET`, **excluding `1`**, de-duplicated, in order.
2. If any: `bgr/getgrades/<c1>-<c2>-…` → an array of exam records.
3. If there were no such codes (or the drill-down failed), fall back to `fullgradespage.miktzoa[]`, the per-subject summary.

### Exam record fields

| Field | Meaning |
| --- | --- |
| `SHEM_MIKTZOA` (also spelled `SHEM_MIKZOA`) | Subject name |
| `SHEM_SHEELON` | Exam form (שאלון) name |
| `TEUR_CODE_MOED` / `TEUR_MOED` | Sitting (moed) |
| `TEUR_SHANA` / `SHANA` | Year |
| `TZIYUN_SOFI` | Final grade |
| `TZIYUN_BCHINA` | Exam grade |
| `TZIYUN_T`, `TZIYUN_M`, `TZIYUN_BEIT_SEFER` | Other components; the last is the school-assessed grade |

The grade shown is the **first non-blank, non-`0`** of `TZIYUN_SOFI`, `TZIYUN_BCHINA`, `TZIYUN_T`, `TZIYUN_M`, `TZIYUN_BEIT_SEFER`. Numbers arrive as JSON numbers or strings; `88.0` is shown as `88`. The component meanings are from the field names; they were not checked against the portal's UI.

## Python

```python
from scholix.eduportal import EducationPortalClient

portal = EducationPortalClient(cookie_header="<copied Cookie header>", csrt="<copied csrt>")
portal.user_info()                  # raises SessionExpired if the cookie lapsed
for g in portal.bagrut_grades():
    print(g.subject, g.name, g.value)
```
