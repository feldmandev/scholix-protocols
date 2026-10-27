# Opal: lecture recordings and video streams

**Evidence:** **App**. Live: recording collection metadata and resolving available HLS playback URLs passed against a connected account in October 2026.

Recordings live in OpenU's video-collection plugin. Playing one is a four-hop chain ending on a CDN, and every hop is host-restricted.

## Collections and recordings

1. Open the course page `GET /course/view.php?id={moodleId}` and find the first link whose `href` contains `ouilvideocollection`, or whose text matches `הקלטות|שיעורים מוקלטים`. None → no recordings.
2. `GET {that URL}` with `quicklinks=1` added. This is the collection page (`/mod/ouilvideocollection/view.php?id={cmid}`).
3. Collections are `.collection_link` elements; `cmid` is their `cmid` attribute, title their text. Several collections → the active one (`#ovc_collections_list li.active .collection_link`, else the page's `id`, else the first) is already rendered on this page; the others need `GET /mod/ouilvideocollection/view.php?id={cmid}`. No `.collection_link` at all → the page itself is a single collection called `הקלטות`.

### Recording fields

Each `.ovc_playlist_border`:

| Field | Source |
| --- | --- |
| title | `.ovc_playlist_title` |
| instructor | `.pl_instructor` |
| date | `.pl_recorddate` |
| description | `.ovc_descr_short` |
| thumbnail | `.ovc_thumb img` `src` (dropped if its query has `p_token`/`token`) |
| `playlistId` | the digits of the element id `playlist{n}` (fallback: the n-th `id="playlist(\d+)"` in the page) |
| `contextId` | the `cid` attribute of a `.collection_link` (regex `collection_link[^>]*c-value="\d+"[^>]*cid="(\d+)"`) |
| `mid` | the `mid` attribute of the same link |
| `courseDbId` | script variable `"courseid":"(\d+)"` (an internal id, **not** the Moodle course id in the URL) |

These four ids are the **hints** needed to resolve a stream without reloading everything. Keep them with the recording.

**Gotcha.** `cid`, `mid` and `cmid` sit side by side on the same `.collection_link` element. Read them as HTML attributes of the *active* link (else the first). A regex such as `cid="…"[^>]*mid="…"` is greedy and silently returns the `cmid` value instead of `mid`, which yields a playlist request the server rejects.

## Resolving a stream

1. `POST /mod/ouilvideocollection/actions.php`, `application/x-www-form-urlencoded; charset=UTF-8`, `X-Requested-With: XMLHttpRequest`:

   `action=getplaylist&context={contextId}&playlistid={playlistId}&course={courseDbId}&mid={mid}`

   → JSON `{media: {cipher: "…"}}`. No `cipher` → not playable.
2. `GET /local/ouil_video/player.php?mediaid={cipher}` → HTML containing `"file":"https:\/\/…"` (unescape `\/`). That URL is the **redirector**.
3. Fetch the redirector **without following redirects automatically**: up to 5 hops, each target checked against the CDN allow-list (`bynetcdn.com` or `openu.ac.il`, HTTPS, no user-info), with `Referer: https://opal.openu.ac.il/`. More than 5 hops → fail.
4. The final response is either an RSS/XML body whose `<source file="…">` (unescape `&amp;`) is the **`.m3u8`** playlist, or the playlist itself (body starts `#EXTM3U`, or the URL contains `.m3u8`).

The `.m3u8` is an HLS playlist; play it with any HLS player (it is ordinary HLS). Entire lectures are large: stream, do not buffer.

If hints are missing, rediscover the recordings and match by title (exact, or equal after replacing every run of non-letter/digit characters with `_`).

**The proxy rule.** The only `POST` the author's tooling allows against Opal for this is `actions.php` with `action=getplaylist`. Anything else is refused, because an arbitrary POST could change state.

## Embedded course videos

`mod/…/view.php` pages can embed a player iframe (see [opal-activities](opal-activities.md#embeds)). To resolve one: `GET` the iframe URL; read the stream source from the script variable `var jwplayer_parameters = {…};` → JSON `file` (must start with `https://`), or from `"media":"…"` (unescape `\/` and `&amp;`). Then follow steps 3–4 above. If resolution fails, treat it as a plain iframe.

## Python

```python
from scholix.openu import OpenUClient

client = OpenUClient(); client.login("user", "pass", "123456789")
course = client.courses()[0]
site = client.resolve_course_site(course["id"], course["raw_term"])   # Moodle id, or None
for rec in client.recordings(site):
    print(rec.title, rec.date)
url = client.resolve_stream(rec)           # an .m3u8 URL; pipe to ffmpeg or an HLS player
```
