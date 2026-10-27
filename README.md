# scholix-protocols

Reverse-engineered documentation and Python clients for the portals Israeli pupils and students use every day: Mashov, Webtop, Shahaf, Schooly, the Ministry of Education sign-in, Bar-Ilan's Inbar and Michlol, Lockerim, the Open University, and more.

It comes from the Scholix Android app, which reads grades and timetables from all of them. The protocols are written down here so you can build your own tools without repeating the reverse engineering.

- **`docs/`** — one protocol write-up per platform: hosts, sign-in, every endpoint and field, quirks, and what is **not** known. Every claim says how it is known (see [conventions](docs/conventions.md#evidence-labels)).
- **`src/scholix/`** — a small Python client per platform with a consistent shape.
- **`tests/`** — offline tests on synthetic data. No test touches a portal and no real account data is in this repository.

> **Unofficial.** This project is not affiliated with, endorsed by, or supported by any of these services or by the Ministry of Education. Portals change without notice; a client that works today may stop working. See [Responsible use](#responsible-use).

## Platforms

| Platform | What you can read | Sign-in | Python | Evidence | Docs |
| --- | --- | --- | --- | --- | --- |
| **Mashov** (משוב) | grades, timetable, bells, attendance/behavior | password · SMS code · device token | `scholix.mashov` | App | [mashov](docs/mashov.md) |
| **Webtop** (Smart School) | grades, class and pupil timetable, events, discipline, inbox | Webtop password · Ministry of Education | `scholix.webtop` | Live (MOE login) · App | [webtop](docs/webtop.md) |
| **Shahaf** (שחף) | published timetable views | Ministry of Education (password/SMS) · Shahaf account | `scholix.shahaf` | Live (login) · Static | [shahaf](docs/shahaf.md) |
| **Schooly** (סקולי) | grades, weekly timetable with changes | national ID + one-time code | `scholix.schooly` | Static · Fixture | [schooly](docs/schooly.md) |
| **Inbar** (Bar-Ilan) | grades, period schedule | SMS only | `scholix.inbar` | Live (app) · Fixture | [inbar](docs/inbar.md) |
| **Michlol** (Bar-Ilan math dept.) | courses, grades, scanned-exam metadata | ID + password | `scholix.michlol` | App | [michlol](docs/michlol.md) |
| **Open University** (Sheilta + Opal) | courses, grades, meetings, exams, scanned exams, course sites, forums, e-books, booklets, recordings, service requests, study programs | SSO form | `scholix.openu` | App · Static | [openu](docs/openu/README.md) |
| **Education Portal** (bagrut) | matriculation grades | *browser session required* | `scholix.eduportal` | App | [education-portal](docs/education-portal.md) |
| **Lockerim** (לוקרים) | locker subscriptions, BLE command frames | SMS + reCAPTCHA | `scholix.lockerim` | Static · Fixture | [lockerim](docs/lockerim.md) |
| **Lemida** (Bar-Ilan Moodle) | documentation only | Microsoft + MFA | — | App · Live | [lemida](docs/lemida.md) |
| Ministry of Education SSO | shared by Webtop, Shahaf, Education Portal | — | `scholix.moe` | Live · App | [moe-sso](docs/moe-sso.md) |

**Evidence** — *Live*: run against the real service, with a date. *App*: implemented in the shipping Scholix app. *Static*: read from a vendor's app or bundle, not exercised. *Fixture*: tested on synthetic data only. The Python clients are ports of the Android implementation and are covered by offline tests; unless a document says otherwise, treat a portal's behavior as authoritative over this repository.

Google Classroom and Google Drive are used by Scholix through Google's official, documented APIs, so there is nothing to reverse-engineer and they are not part of this repository.

## Install

```bash
pip install git+https://github.com/feldmandev/scholix-protocols
```

Python 3.10+. Dependencies: `requests`, `beautifulsoup4`, `cryptography`.

## Quick start

```python
from scholix.mashov import MashovClient, list_schools

schools = list_schools()                                   # public list
client = MashovClient(semel="123456", username="your-user", year=2027)
client.login("your-password")

for grade in client.grades():
    print(grade.subject, grade.name, grade.value)

for lesson in client.timetable():                          # day 0 = Sunday
    print(lesson.day, lesson.period, lesson.start, lesson.subject)
```

Sign-in that needs a person (SMS, CAPTCHA, an MFA prompt) is a method call or an exception, never a hidden loop:

```python
from scholix.inbar import InbarClient

inbar = InbarClient()
inbar.request_sms("012345678", "0501234567")      # sends exactly one SMS
page = inbar.verify_sms(input("code from SMS: "))
```

More runnable scripts are in [`examples/`](examples/). Credentials are read with `getpass` or the environment, never from the command line.

## Design rules

- **Fail loudly.** A table or JSON shape the library does not recognize raises `ProtocolError` rather than returning an empty list, so "no grades yet" and "the portal changed" are never confused.
- **Never bypass.** No CAPTCHA solving, no bot-defense token forgery, no automatic SMS resends, no retry of a rejected code, no rate-limit evasion.
- **Verified TLS, pinned hosts.** Certificate validation is on, and multi-host sign-ins check every redirect hop against an allow-list.
- **Raw data is kept.** Normalized records (`Grade`, `Lesson`) carry the portal's original payload in `.raw`.
- **Sessions are secrets.** `export_session()` output is as sensitive as a password.

Details: [docs/conventions.md](docs/conventions.md).

## Responsible use

- Use accounts you are entitled to use: your own, or your child's as a parent or guardian.
- Be gentle. Do not poll, and never loop on SMS requests or failed codes: several of these portals lock an account or stop sending codes, and the lock may have no stated expiry.
- Respect each service's terms. Reverse engineering for interoperability is common, but terms and local law differ and this is not legal advice.
- Never publish captures. Do not open issues or pull requests containing real cookies, tokens, national IDs, names, grades or school codes tied to a person. See [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

## Contributing

Corrections are the most valuable contribution: if a portal behaves differently from a document, say so with a **sanitized** trace. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
