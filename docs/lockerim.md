# Lockerim (לוקרים)

Lockerim rents lockers to schools and residential buildings. Pupils use the Android app **LockerimRB** (`com.lockerimrb`) to sign in by SMS, see their locker subscriptions, and open or lock a school locker over Bluetooth Low Energy.

**Evidence**

- Cloud API and BLE frame format: **Static**. Read from LockerimRB **5.1.0.2354** (version code 2354; fetched from the APKPure latest-package download on 2026-10-18; base APK SHA-256 `60267e1685a51ddd2394e01acbdad0bf05672de94bef068543b890fb1410dc5f`). The app is React Native; the bundle is Hermes bytecode version 96, and the authentication service, subscription mapper, PEM command builder and Bluetooth transport were disassembled.
- An empty login request answered HTTP 200 with a validation error, and an unauthenticated subscription request answered HTTP 401: **Live** (no SMS was sent).
- The AES command format: **Fixture** against a vector computed independently with Node's crypto (below).
- Real SMS delivery, authenticated subscription responses, first-time lock enrollment, and a physical open/lock: **not verified end to end**. They need a registered account and hardware.

No vendor binary, asset, identity, SMS code or token is in this repository.

**Python:** `scholix.lockerim` — the cloud client (`LockerimClient`) and the frame codec (functions). The Bluetooth transport is intentionally not included.

## Scope

School PEM lockers. Residential SmartBox deliveries, permission sharing and firmware updates are not covered.

## Cloud API

Origin `https://rbapi.lockerim.co.il`. JSON. Bearer token after sign-in. Every response is wrapped:

```json
{ "content": …, "isSucceeded": true, "errors": [ { "message": "…" } ] }
```

Treat the call as failed if the HTTP status is not 2xx, `isSucceeded` is `false`, or `errors` is non-empty. `content` is the payload; a missing `content` is an error.

| Method | Path | Body → `content` |
| --- | --- | --- |
| POST | `/Account/Login` | `{phoneNumber, RecaptchaKey}` → `{otpToken}` |
| POST | `/Account/ResendLoginOtp` | `{otpToken, RecaptchaKey}` → `{otpToken}` (replacement) |
| POST | `/Account/ValidateLoginOtp` | `{otpToken, otp, RecaptchaKey}` → `{token, refreshToken}` |
| POST | `/Account/RefreshToken` | `{refreshToken}` → rotated `{token, refreshToken}` |
| GET | `/Subscription/GetMySubscriptions` | → `{subscriptions: […]}` |
| POST | `/Subscription/ECRecognition` | first-contact report for a lock (below) |

### reCAPTCHA

`RecaptchaKey` is required on every SMS request, resend and verification. The official app obtains it with a reCAPTCHA WebView using its own public site key, a WebView origin of `https://localhost:3000/` and an empty action. **This library cannot create one**; get a token from a browser or WebView yourself and pass it in. The site key is not reproduced here; it is readable in the app.

### Validating the SMS challenge

The `otpToken` returned by `Login` and `ResendLoginOtp` must be a **non-empty JSON string** that is not the text `null`. Reject JSON `null`, numbers, blanks and the literal string `"null"` *before* showing a code box. A pitfall: Android's `JSONObject.getString` turns JSON `null` into the string `"null"`, which a naive check accepts. A returned challenge proves only that Lockerim accepted the request, not that an SMS reached the phone.

### Tokens

The access token is a JWT. Its **`UserECId`** claim is the user id that goes into lock commands (below). The refresh token is single-use: store the pair returned by each refresh. On HTTP 401 the Android app refreshes once and repeats the call; a 400/401/403 from `RefreshToken` means the session is gone.

### Subscription fields used

`subscriptionId`, `cellNumber`, `closetNumber`, `siteNameHE`/`siteNameEN`, `address`, `startDate`/`endDate`, `macAddress` (the lock's Bluetooth address), `skda` (six hex digits; **per-lock key material**), `ec` (hex; first-contact value) and `isRecognized`. Only the signed-in account's own subscriptions are offered. Academic subscriptions are separate from residential SmartBox ones.

## BLE: PEM locks

| | UUID |
| --- | --- |
| Service | `6e406060-b5a3-f393-e0a9-e50e24dcca9e` |
| Notifications (lock → phone) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |
| Writes (phone → lock) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |

A lock advertises its name as `PEM` + its MAC address without colons (`PEMAABBCCDDEEFF`). Enable notifications through the standard CCCD (`00002902-…`), and write without response.

### Command frame

`[` + 16 encrypted bytes + `]`. The 16-byte plaintext, as hex:

```
cccc uuuuuuuu vv ssssss 000000000000
```

| Part | Width | Meaning |
| --- | --- | --- |
| `cccc` | 2 B | Command counter, big-endian, 1…65535 |
| `uuuuuuuu` | 4 B | `UserECId`, big-endian |
| `vv` | 1 B | `01` = unlock, `00` = lock |
| `ssssss` | 3 B | the subscription's `skda` |
| padding | 6 B | zeros |

Cipher: **AES-128-ECB, no padding.** Key: the fixed 16 bytes `ABC532744BBF456532833D368682D560` with the three `skda` bytes **overlaid** starting at offset `(last byte of skda) mod 12`. The fixed bytes come from the app's `buildEncryptKey`; the only secret per lock is `skda`, which only that lock's subscriber receives from the server.

Reference vector (synthetic ids, computed independently with Node `crypto`):

| `skda` | `UserECId` | counter | action | frame (hex) |
| --- | --- | --- | --- | --- |
| `123456` | `0x01020304` | 1 | unlock | `5b6e5a30a9b5833168bddbead8f63d184d5d` |

Decrypting the 16 bytes gives `00010102030401123456000000000000`.

### Counter

The counter must increase for each `(user, lock)`. The official client persists it and **increments before transmitting**, including for attempts that fail. A reply beginning `<PIC` carries the value the lock expects: bytes 4–5 big-endian, **plus one** is what to use next.

### Replies

Success is plain ASCII: `<PUS>` (opened) or `<PLS>` (locked). Report success only on these. Error replies:

| Reply | Meaning |
| --- | --- |
| `<PIC…>` | counter out of sync (carries the right value) |
| `<PIS>` | rental has not started |
| `<PES>` | rental has ended |
| `<PLF>` | lock not latched; push it in, then lock |
| `<PCB>` | battery needs replacing |
| `<PIU>`, `<PIP>` | permission unavailable or used up |
| `<PIL>`, `<PUF>`, `<PUA>`, `<PDF>`, `<ECR>` | rejected; try again |

### First contact (enrollment)

If `isRecognized` is false: write `<` + the `ec` bytes + `>`, then read replies. A `^…` frame decrypts (with `skda`) to a recognition body: characters 6–8 are the PEM type, 8–12 the keypad password, 12–18 the OTP id (hex), 18–22 the keypad OTP password. A `%…` frame decrypts to the remote-control data. Report `{subscriptionId, macAddress, ec, pemType, KeypadPassword, otpId, keypadOtpPassword, fullRecognitionResponse, remoteControlData}` to `POST /Subscription/ECRecognition`. The field names are from the app; the semantics of the decrypted fields are as the app names them and were not independently checked. **Not verified on hardware.**

## Python

```python
from scholix import lockerim

api = lockerim.LockerimClient()
challenge = api.send_code("0501234567", recaptcha=token_from_browser)
api.verify_code(challenge, input("code: "), recaptcha=another_token)

for sub in api.subscriptions():
    frame = lockerim.command(sub["skda"], api.user_ec_id(), counter=1, unlock=True)
    # write `frame` to lockerim.WRITE_UUID with any BLE library (e.g. bleak),
    # read notifications from lockerim.NOTIFY_UUID, then:
    #   lockerim.succeeded(reply, unlock=True) / lockerim.pem_error(reply) / lockerim.next_counter(reply)
```

## Responsible use

The command format is useful for interoperability and for checking that your own subscription works. Every lock's key material is issued to that lock's authorized renter by the service; this library has no way around that and is not a bypass. Only operate lockers you are entitled to.
