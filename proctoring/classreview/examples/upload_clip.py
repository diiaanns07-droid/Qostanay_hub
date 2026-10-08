"""Reference uploader for the STUDENT side (qorgau.class.v1 §5) — stdlib only, copy into the uplink module.

    from upload_clip import upload_clip
    result = upload_clip("http://192.168.1.10:8765", resume_token, incident_id, clip_bytes, source="live")

Rules (see ../CLIENT_CLIP_UPLOAD.md):
  * upload ONLY after `command request_clip {incident_id}` and only for an incident you sent before;
  * the same bytes on every retry (never re-encode between attempts) -> a retry after a lost response is a
    harmless "duplicate";
  * retry only network errors / 5xx (backoff 1, 2, 4, 8, 16 s); 4xx are final (fix the cause, do not loop).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

FINAL = {400, 401, 404, 409, 413, 415, 422}


class UploadFailed(Exception):
    def __init__(self, status: int | None, code: str, message_ru: str):
        super().__init__(f"{status} {code}: {message_ru}")
        self.status, self.code, self.message_ru = status, code, message_ru


def upload_clip(
    server: str,
    resume_token: str,
    incident_id: str,
    data: bytes,
    *,
    media_type: str = "video/mp4",
    source: str = "live",
    attempts: int = 5,
    timeout_s: float = 30.0,
    sleep=time.sleep,
) -> dict:
    """Returns the server JSON: {"status": "stored"|"duplicate", "sha256", "size_bytes", ...}."""
    if media_type not in ("video/mp4", "video/x-msvideo"):
        raise ValueError("media_type must be video/mp4 or video/x-msvideo")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("clip larger than 8 MB: re-encode (lower bitrate/fps/size) before uploading")
    url = f"{server.rstrip('/')}/api/student/clips/{urllib.parse.quote(incident_id, safe='')}"
    headers = {
        "Authorization": f"Bearer {resume_token}",  # never put the token in the URL
        "Content-Type": media_type,
        "X-Qorgau-Clip-Source": source,  # live | replay | synthetic | test (pending T01 approval; optional)
    }
    last: UploadFailed | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))  # 201 stored / 200 duplicate
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read().decode("utf-8")).get("error", {})
            except Exception:
                err = {}
            last = UploadFailed(exc.code, err.get("code", "http_error"), err.get("message_ru", str(exc)))
            if exc.code in FINAL:
                raise last from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = UploadFailed(None, "network", str(exc))
        if attempt + 1 < attempts:
            sleep(min(16.0, 2.0**attempt))
    assert last is not None
    raise last
