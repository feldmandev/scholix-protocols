# Security and privacy

## Reporting a problem in this repository

If you find that this repository contains something that should not be public (a real credential, session, national ID, a student's data, a vendor binary), or a vulnerability in the Python code, **do not open a public issue**. Use GitHub's private vulnerability reporting for this repository (Security → Report a vulnerability) so it can be removed and, where needed, the credential revoked and history rewritten.

## Vulnerabilities in the services themselves

These documents describe how services behave. If, while working with them, you discover a flaw in a service (for example an authorization bypass or exposure of other people's data), report it to the vendor or the Israeli National Cyber Directorate (https://www.gov.il/en/departments/israel_national_cyber_directorate) rather than publishing details here. Do not test against accounts that are not yours.

## What this library does to protect you

- TLS certificate verification is never disabled.
- Credentials are sent only to the documented hosts; multi-host flows check every redirect hop.
- Verification codes and passwords are not stored by the library. Session export is explicit (`export_session()` / `export_cookies()`); treat the result as a password.
- Exceptions and `ProtocolError.details` do not include credentials or response bodies.

## What it does not do

It does not encrypt sessions you export, does not rotate secrets for you, and cannot stop a portal from locking an account after repeated failed attempts. Do not put credentials in command lines or scripts you commit.
