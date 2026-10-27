# Michlol (מכלול)

Michlol is a commercial student-information system sold to Israeli institutions, each running it on its own host. Scholix has used it for the **Bar-Ilan University mathematics department** at `https://biumath.michlol4.co.il`. It is separate from [Inbar](inbar.md), Bar-Ilan's main portal.

**Evidence:** **App** (`BarIlanPlatform`). Only the `biumath.michlol4.co.il` tenant has been used. Whether other `*.michlol4.co.il` hosts share these routes is unknown; the Python client takes the base URL as an argument so you can try, and an issue with a sanitized result would settle it.

**Python:** `scholix.michlol.MichlolClient`.

## Protocol

JSON `POST` requests to `<base>/api/<Controller>/<Action>`. Authentication is a bearer token returned by login. There are no cookies in the flow.

| Endpoint | Body | Response |
| --- | --- | --- |
| `Login/Login` | `{"captchaToken": null, "loginType": "student", "password": "…", "zht": "<student id>", "deviceDataJson": "{\"isMobile\":true,\"os\":\"Android\",\"browser\":\"Chrome\",\"cookies\":true}"}` | `{"success": true, "token": "…"}`; on failure `success` is `false`/`null` with `errorDescription`. |
| `Account/UserInfo` | `{}` (+ bearer) | `{"userInfo": {"smp": <first name>, "smm": <last name>, …}}` |
| `StudentCourses/Data` | `{"urlParameters": {}}` | `{"courses": {"clientData": [row, …]}}` |
| `Grades/Data` | `{"urlParameters": []}` | `{"collapsedCourses": {"clientData": [course, …]}}`; the response also carries a **`pagedata` header**. |

`zht` is the student's ID number. `captchaToken` is `null`; a deployment that enforces a CAPTCHA would need one, and none has been seen.

### Course rows (`StudentCourses/Data`)

| Field | Meaning |
| --- | --- |
| `krs_shm` | Course name |
| `krs_snl` | Academic year |
| `krs_sms_select` | Semester |
| `all_pms_shm` | Lecturers' names, stored with the **word order reversed** (right-to-left storage); reverse the words to read it |

Field names are Michlol's transliterated column names (`krs` = קורס, `shm` = שם, `snl` = שנה, `sms` = סמסטר, `pms` = מרצה). That reading is inferred from the values.

### Grades (`Grades/Data`)

A three-level tree. Each level uses `__body` for its children:

```
collapsedCourses.clientData[]            course: krs_shm
  __body[]                               grade row: krs_shm (component name), krs_snl (date), bhnzin (grade)
    __body[]                             exam sitting (moed): zin_sug (type), moed_1_zin (grade), krs_snl (date), __buttons[]
```

`__buttons[]` holds actions per sitting. The one whose `description` is **`בחינה סרוקה`** ("scanned exam") has `routeData` with `scan_location`, `scanfilename`, `__hash`, `scan_pt` and `rowkey`. Together with the `pagedata` header that identifies the scan. The download request itself was **not** reverse-engineered; the Python client returns `routeData` and `pagedata` untouched so it can be.

## Python

```python
from scholix.michlol import MichlolClient

client = MichlolClient()                       # or MichlolClient("https://<tenant>.michlol4.co.il")
client.login("123456789", "password")
for course in client.courses():
    print(course["name"], course["year"], course["semester"], course["teacher"])
for grade in client.grades():                  # grade.raw["submissions"] = exam sittings
    print(grade.subject, grade.name, grade.value)
```

## Not covered

Timetable (Bar-Ilan's app reports none for this tenant), attendance, messages, scanned-exam download, and any other institution's Michlol.
