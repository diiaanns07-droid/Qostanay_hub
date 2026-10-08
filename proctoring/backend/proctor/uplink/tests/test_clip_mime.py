"""C2 sends the MIME type of A02's actual artifact; no wire-schema changes."""
import asyncio

import pytest

from proctor.uplink.client import Uplink
from proctor.uplink.config import UplinkConfig


@pytest.mark.parametrize("extension,mime", [(".mp4", "video/mp4"), (".avi", "video/x-msvideo")])
def test_clip_upload_uses_actual_extension(tmp_path, extension, mime):
    calls = []
    def upload(url, path, token, content_type):
        calls.append((path, content_type))
        return True, "ok"
    cfg = UplinkConfig(server="127.0.0.1:8765", join_code="123456", student_label="SYNTHETIC",
                       computer_name="test", state_dir=tmp_path / "uplink")
    up = Uplink(cfg, object(), http_post=upload)
    path = tmp_path / ("synthetic" + extension)
    path.write_bytes(b"synthetic test upload")
    up._clips["inc-test"] = path
    assert asyncio.run(up._upload_clip("inc-test")) == (True, None)
    assert calls == [(path, mime)]
