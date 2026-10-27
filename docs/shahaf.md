# Shahaf (שחף) timetables

Shahaf is a school-timetable product. Schools publish their timetable to it, and pupils read it through the "Shahaf" mobile app (`com.shahafsoft.timetableapp`, a Flutter app) backed by `https://view.shahaf.info/api/`.

**Evidence**

- Endpoint names, parameters and the view schema: **Static**, from the Flutter AOT snapshot of app version 1.2.2850 (the Dart snapshot retains named API methods; extraction used radare2 with r2flutter). Anchors in that build: `Api._downloadString` (`0x332508`), `Api.getAppData` (`0x34ebb8`), `Api.getClassesAsync` (`0x3522d0`), `Api.getStudentSchoolsAsync` (`0x3d185c`), the SSO payload decoder (`0x3eab68`).
- MOE sign-in and the token being accepted: **Live**, 2026-10-13. The account's school did not use Shahaf, so `school/student/` answered `HTTP 200 []`. Login success does **not** imply a timetable exists.
- Rendering of non-empty published data: **not verified**; no linked school with data was available.
- Shahaf's own username/password route (`user/authstudent/`): **Static**, never run against a real Shahaf account.

**Python:** `scholix.shahaf.ShahafClient`.

## What Shahaf returns

Shahaf publishes **presentation rows**: titled blocks of text and links with styling, arranged in tabs. It does *not* publish structured lessons. The rows have no reliable subject/teacher/period fields, so this library returns the view tree and does not invent a normalized timetable. If you need structured lessons, use the school's information system (Mashov, Webtop, …).

## Sign-in

Every authenticated call carries `token` as a **query parameter**. (An earlier hypothesis of cookies or a Bearer header was wrong, as was a guessed `view/student/<school>/classes/` route.)

### Ministry of Education

Entry URL: `https://timetable.shahaf.info/ShfSignIn.aspx?sid=shfapp&cid=<device UUID>`. Run the MOE conversation ([moe-sso.md](moe-sso.md), password or SMS). It ends on:

```
https://timetable.shahaf.info/SignInSuccess.aspx?s=<payload>
```

`s` is **not** a token. It is the whole auth response, encoded as Base64 with the alphabet changed (`.` for `=`, `_` for `/`, `-` for `+`), UTF-8, JSON:

| Key | Meaning |
| --- | --- |
| `Token` | The session token for every other call. |
| `Name` | Display name. |
| `Tz` | National ID. |
| `Schools` | Linked schools. |
| `Student` | `Semel`, `Grade`, `Class`, `ClassMid` — the pupil's own school and class. |
| `VCode` | Observed; purpose unknown. |

The payload can appear on an *intermediate* redirect in the chain, so the client checks every hop, not only the final URL. Decode with `scholix.shahaf.decode_sign_in_payload`.

### Shahaf account

`GET user/authstudent/?usr=<username>&pwd=<password>` → an `AuthResponseExt` with the same keys. **Not verified live.** The password is in the query string; that is how the official app sends it, so it will appear in any URL logging.

### Errors

A failed call returns JSON `{"Status": "failed", "Error": "<code>"}`:

| `Error` | Meaning |
| --- | --- |
| `NO_TOKEN`, `INVALID_TOKEN` | Session expired — sign in again. |
| `BAD_CREDENTIALS`, `NO_USER_OR_PASSWORD` | Wrong Shahaf username or password. |

## Endpoints

Base `https://view.shahaf.info/api/`; all `GET`; add `token=<Token>`.

| Path | Query | Returns |
| --- | --- | --- |
| `school/student/` | | Bare array of schools: `name`, `semel`, `code`, `status`. May be `[]`. |
| `student/<semel>/<code>/classes/` | | `{"Classes": [{Id, Name, Grade, Number}]}` |
| `view/<semel>/<code>/appprms/` | `clsid` | The view parameters (pickers) for a class. |
| `view/<semel>/<code>/appdata/` | `clsid`, plus any view parameters | The published views (below). |
| `settings/0/0/create/` | `appid`, `platform=2`, `appt=1` | Registers an official-app *instance*. Not used by this library. |
| `settings/<semel>/<code>/appprms/` | `appid`, `tz`, `cid`, `devtkn`, `msg`, `platform=2`, `appt=1` | Push-notification registration. Not used by this library. |

`classes[].Id` is the `clsid`. For the pupil's own class use `Student.ClassMid`.

## Published view schema (**Static**)

Short JSON keys, as recovered from the snapshot:

```
AppData     { version, views[] }
AppView     { h (heading), t, i, c, tp, k, d (data blocks), tb (tabs), p (parameters) }
ViewTabs    { l, r, dt, tb }
ViewTab     { t (title), id, k, i (data blocks) }
ViewParameter { id, l (label), i, o (options: {id, l}) }
ViewData    { t (title), k, r (rows) }
ViewDataRow { h (header elements), i (content elements), sep, rc,
              hbc, hw, he, hta, mbc, mw, me, mta }
DataElement { t (text), u (link), s (style: object or string) }
```

The meanings of the styling keys (`hbc`, `hw`, `he`, `hta`, `mbc`, …) are inferred from their names — most plausibly header/main background color, width, element and text-align — and have not been checked. Treat anything beyond `t`/`u`/`h`/`i`/`d`/`tb` as opaque.

A view parameter's chosen option is sent back as a query parameter named by `ViewParameter.id`.

## Python

```python
from scholix.shahaf import ShahafClient

client = ShahafClient()
client.login_moe_password("moe-user", "moe-password")   # may raise CaptchaRequired

for school in client.schools():                          # [] if your school is not on Shahaf
    classes = client.classes(school["semel"], school["code"])
    data = client.app_data(school["semel"], school["code"], classes[0]["Id"])
    for view in data["views"]:
        print(view.get("h"))

# SMS instead of a password:
challenge = client.request_moe_sms("national-id", "05XXXXXXXX")
client.submit_moe_sms(challenge, input("code: "))
```

## Unknowns

How styling keys render; filter semantics with real data; whether `user/authstudent/` works; token lifetime.
