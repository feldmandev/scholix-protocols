# Ministry of Education sign-in (`lgn.edu.gov.il`)

The Ministry of Education (MOE) runs a single identity provider for pupils. Webtop and Shahaf both federate to it, and the Education Portal uses it too. This document describes the HTTP conversation; the code is `scholix/moe.py`.

**Evidence:** the password flow for Webtop is **Live** (two independent fresh sessions, 2026-09-08, one authorized account, password-only branch; a sanitized trace was kept by the author). The Shahaf variants, the SMS flow and the Shahaf hand-off are **App**. CAPTCHA, OTP-on-Webtop and password-change branches were not exercised and are never automated.

## Roles

- **Identity provider (IdP):** `https://lgn.edu.gov.il`, a WS-Federation endpoint (the `/nidp/wsfed/` paths match NetIQ Access Manager's layout; that is an inference from the URLs, not something the service states). It is served behind F5 bot-defense scripts (`/TSbd/…`).
- **Relying party (RP):** the service you want, here Webtop (`webtop.smartschool.co.il`) or Shahaf (`timetable.shahaf.info`).

The client starts at the RP, is bounced to the IdP, authenticates there, and is bounced back with a signed assertion that the RP turns into its own session.

## The conversation

| # | Request | What happens |
| --- | --- | --- |
| 1 | `GET <RP entry URL>` | The RP redirects to `lgn.edu.gov.il/nidp/wsfed/ep?…` (`wa`, `wtrealm`, `wctx`, `wreply`). |
| 2 | follow interstitials until a page defines `_serverUrl` | The IdP serves auto-submitting forms. The credential page carries three script literals: `_serverUrl` (where to POST), `_loginType` (`usernamePassword`, `usernameSMSOTP`, …) and `_target`. |
| 3 | `POST _serverUrl` `option=credential&initiateLoginSequence=true&isAjax=true` | The page's own script always does this first. On a fresh session it answers `isError:true` (`INVALID_INPUT`). **Ignore the result**; the next call succeeds regardless. |
| 4 | `POST _serverUrl` `option=credential&isAjax=true&HIN_USERID=…&Ecom_Password=…&g-recaptcha-response=%20` | JSON: `isError`, `isCaptchaNeeded`, `isPasswordExpiring`, `errorCode`. Success is `isError:false`. |
| 5 | `POST _serverUrl` `option=credential&target=<_target>` | HTML with `top.location.href='ep?sid=<n>'`. |
| 6 | `GET` that URL, **resolved against the response URL** | An auto-submitting form carrying `wa`, `wresult` (the signed XML assertion) and `wctx`. |
| 7 | `POST` the form to its `action` (the RP) | The RP redirects into its app. |

Then the RP-specific exchange:

- **Webtop** lands on `https://webtop.smartschool.co.il/account/loginMoe?key=<key>`. POST that key to `…/server/api/user/LoginMoe` (see [webtop.md](webtop.md)).
- **Shahaf** lands on `https://timetable.shahaf.info/SignInSuccess.aspx?s=<payload>`. The payload *is* the auth response (see [shahaf.md](shahaf.md)). It can appear on an intermediate redirect, so the client scans the whole redirect chain.

### Details that were each a bug once

- **The session id changes.** Step 6 must follow the URL step 5 returned. Hard-coding `sid=0` works until it doesn't.
- **Decode HTML attributes exactly once.** The assertion is XML inside an HTML attribute, so it is full of `&amp;`/`&lt;`. A regex that copies the attribute raw posts escaped text and the RP rejects it; a decoder that unescapes twice corrupts it. A real HTML parser (BeautifulSoup here) unescapes once.
- **Empty reCAPTCHA field is a single space**, not empty and not `+`.
- **Header profile matters on the SMS endpoint.** MOE answers `isCaptchaNeeded:true` to requests that do not look browser-issued, even when a real browser would not be shown a CAPTCHA. Send a desktop-Chrome `User-Agent`, `Accept-Language: he-IL,…`, and for the AJAX posts `X-Requested-With: XMLHttpRequest`, `Accept: application/json, …`, and `Referer`/`Origin` of the **page** URL (not the `_serverUrl`; a browser never posts with `Referer == _serverUrl`). **App**
- **Do not replay the F5 scripts.** An earlier theory held that a browser-generated `x-security-csrf-token` was mandatory. The two successful Webtop runs used no browser, no `TSbd` execution and no TLS impersonation, which disproves it *for that account and those conditions*. Treat "needs a browser" as something to re-test, not assume.

## SMS sign-in (`EduCombinedAuthSms`) — App

For accounts that sign in by SMS instead of a password:

1. Reach the credential page as above.
2. Re-request the same page URL with `id=EduCombinedAuthSms` substituted for the `id=` query value (exactly what the SPA's SMS tab does). The page must report `_loginType` containing `SMSOTP`.
3. `POST` `HIN_ID=<national id>&HIN_MOBILE=<10-digit 05… mobile>` (plus the usual `option`, `isAjax`, empty reCAPTCHA). A good answer has `isError:false` and `otpDestMasked`.
4. `POST` `Ecom_Token=<code>`.
5. Complete with the `target` POST (step 5 above) and follow the hand-off.

Verification errors: `TOKEN_TOKEN_EXPIRED` / `TOKEN_NOT_FOUND` (code expired; request a new one) and `INTRUDER_DETECTED` (locked after too many attempts).

In `scholix.moe`, `MoeSignIn.request_sms()` returns a challenge you pass to `submit_sms()`.

## What stops automation

- `isCaptchaNeeded:true` → `CaptchaRequired`. Sign in in a browser.
- `isPasswordExpiring:true` → `VerificationRequired`. Change the password on the MOE site.
- An account whose contract is `usernameSMSOTP` cannot use the password path → `VerificationRequired` before any credential is sent.
- The Education Portal (`students.education.gov.il`) is protected more tightly: the IdP validates the password but never honors the session without browser-produced F5 headers. See [education-portal.md](education-portal.md).

## Security notes

- Credentials are posted only to hosts in the allow-list (`lgn.edu.gov.il` plus the RP's hosts), and every redirect hop is checked.
- The MOE username/password is a national-ID-linked credential. Keep it out of logs and shell history.
