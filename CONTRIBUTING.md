# Contributing

## The most useful contribution: "the portal does something else now"

Open an issue with:

1. which document and which claim,
2. what you observed, with the date,
3. a **sanitized** request/response pair or HTML structure.

### Sanitizing a capture

Before you paste or commit anything, remove or replace:

- cookies, `Authorization` headers, `devicePass`, `X-Csrf-Token`, `csrt`, `token`, `key`, SAML `wresult` assertions, `webToken`;
- passwords, one-time codes, SMS text;
- national ID numbers, phone numbers, e-mail addresses, student/user GUIDs, school codes that identify a person's school;
- names (yours, teachers', classmates'), grades, course lists, timetables, message bodies;
- any URL carrying one of the above in a query string.

Keep the **shape**: field names, nesting, status codes, header *names*, and structure of HTML (class/id/header text). Replace values with obvious placeholders (`"sample-01"`, `"Lecturer"`).

Never attach a HAR file, a screenshot of a logged-in page, or a vendor APK/IPA. If the maintainers ask for a binary hash, give the SHA-256 and where you got it.

## Code changes

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

- Tests are **offline** and use **synthetic** data. Add the failing case before the fix. The harness in `tests/conftest.py` provides a fake transport.
- A new platform needs three things: `docs/<platform>.md`, `src/scholix/<platform>.py`, `tests/test_<platform>.py`, and a row in the README table.
- Parsers must raise `ProtocolError` for shapes they do not recognize. Do not return partial data from a response you could not fully read.
- No CAPTCHA solving, bot-defense bypass, automatic SMS/OTP resending or retry loops, rate-limit evasion, or disabled TLS verification. Pull requests that add them will be closed.

## Writing documentation

- Every claim carries its evidence: **Live** (with a date), **App**, **Static** or **Fixture**. If you do not know, say "unknown" or "unverified".
- Prefer tables of endpoints and fields over prose. Give a request and a response for every call.
- Record quirks that cost time (encoding, ordering, off-by-one days, look-alike field names).
- Do not describe an inference as a fact. "Inferred from the field names" is a fine sentence.
- State what is **not** covered.

## Commit hygiene

Small commits, one concern each. Do not commit generated files, virtual environments, captures or credentials; `.gitignore` already blocks the common ones.
