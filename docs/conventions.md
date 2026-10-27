# Conventions

How to read the other documents, and what the Python library does the same way everywhere.

## Evidence labels

Every claim in these documents comes from one of these sources. A document says which, because "the app does this" and "I watched the server do this" are not the same strength of evidence.

| Label | Meaning |
| --- | --- |
| **Live** | Run against the real service with a real account, with the date given. Strongest. |
| **App** | Implemented in the shipping Scholix Android app, which real users run. The request shapes are right; the app is not a spec, so edge cases may be untested. |
| **Static** | Read from a vendor's app or website bundle (decompiled/AOT strings, shipped JavaScript) without exercising it. The shape is right for that build; behavior is inferred. |
| **Fixture** | Covered only by tests on synthetic data. Proves the parser follows the documented shape, not that the portal still emits it. |

A statement with no label is a description of what the code in this repository does.

**What the Python tests prove.** Everything under `tests/` is offline. No test contacts a portal, and no real account data is in the repository. The Python clients are ports of the Android implementation; where the Android side has a **Live** record, it is cited, but the Python port itself has been run live only where a document says so. If a portal changed since the date given, trust the portal.

## Shared behavior

### Errors

All exceptions derive from `scholix.ScholixError`.

| Exception | Raised when |
| --- | --- |
| `AuthenticationError` | Credentials or a one-time code were rejected. |
| `SessionExpired` | A session that used to work is no longer accepted (a subclass of `AuthenticationError`). Sign in again. |
| `CaptchaRequired` | The portal wants a CAPTCHA. The library never solves or bypasses one. |
| `VerificationRequired` | The portal wants a second factor or a password change that the called method cannot supply. |
| `RateLimited` | The portal refused for now (HTTP 429, a lockout, an SMS cap). Back off; do not loop. |
| `ProtocolError` | The response did not have the documented shape. Usually means the portal changed. `.details` carries a sanitized description for an issue report. |
| `NetworkError` | Transport failure or timeout. |

Parsers prefer failing loudly to guessing. A changed table layout raises `ProtocolError` instead of returning an empty list, because an empty timetable and "the portal changed" must not look the same.

### Days, school years and semesters

- **Day index.** `Lesson.day` is `0` = Sunday … `6` = Saturday. Portals differ: Mashov and Webtop count Sunday as `1`, Schooly counts it as `0`, and Inbar prints Hebrew day names. Each client converts.
- **School year.** Each portal names the school year by the calendar year in which it *ends*, but they roll over in different months: Mashov in July, Webtop in September. The helpers `mashov.current_school_year()` and `webtop.study_year()` document their own cut-off. Pass the year explicitly when it matters.
- **Semester.** Webtop and Michlol use `a` / `b`; Open University adds `c` (summer); Inbar uses a numeric period code that you read from the page.

### Sessions

Clients hold a `requests` session. `HttpSession.export_cookies()` / `import_cookies()` persist cookies with domain and path intact; some clients add their own `export_session()`. Store these like passwords: a session is as powerful as the credentials that created it.

### Transport

- TLS verification is on. No client disables it.
- Every request has a default timeout (30 s).
- Clients that follow a multi-host sign-in pin an allow-list of hosts and check it on **every redirect hop**, so a redirect can never send credentials somewhere unexpected.

## What the library will not do

It does not solve CAPTCHAs, replay bot-defense tokens, send SMS codes automatically, retry a rejected one-time code, or work around a rate limit. Where a portal needs one of those, the client raises and says so.
