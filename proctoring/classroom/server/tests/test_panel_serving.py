"""The server hosts the T02 class panel (REAL adapter) behind its own login page. Browser chain: tests/chain/."""

from __future__ import annotations

import pytest

from classroom.server.config import CLASS_PANEL_DIR
from classroom.server.tests.harness import TINY_JPEG, ServerProcess, TeacherStream

pytestmark = pytest.mark.skipif(not (CLASS_PANEL_DIR / "index.html").is_file(), reason="T02 class-panel is not in this checkout")


@pytest.fixture(scope="module")
def panel_server(tmp_path_factory):
    srv = ServerProcess(tmp_path_factory.mktemp("paneldata"), {"QORGAU_CLASS_UI": "class-panel"})
    yield srv
    assert srv.stop() == 0, "".join(srv.stderr[-20:])


def test_login_page_guards_the_panel(panel_server):
    anon = panel_server.teacher(login=False)
    r = anon.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon.get("/login").status_code == 200 and 'name="pin"' in anon.get("/login").text
    origin = {"Origin": panel_server.base}
    r = anon.post("/login", content="pin=000000", headers={**origin, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 401 and "Неверный PIN" in r.text and "qorgau_teacher" not in r.headers.get("set-cookie", "")
    r = anon.post("/login", content="pin=1", headers={"Origin": "http://evil.example", "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 403  # cross-site form post never reaches the PIN check
    r = anon.post("/login", content=f"pin={panel_server.pin}", headers={**origin, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    page = anon.get("/")
    assert page.status_code == 200 and "Adal" in page.text and page.headers["cache-control"] == "no-store"
    assert anon.get("/config.json").json() == {"adapter": "real"}  # the repo file says demo; C1 serves real (T02 HANDOFF)
    assert anon.get("/src/adapters/real.js").status_code == 200
    assert anon.post("/logout", headers=origin).status_code == 303
    assert anon.get("/").status_code == 303
    anon.close()


def test_panel_files_are_teacher_computer_only(panel_server):
    anon = panel_server.teacher(login=False)
    assert anon.get("/login", headers={"Host": "192.168.1.10:8765"}).status_code == 403  # DNS-rebinding / foreign host
    anon.close()


def test_class_panel_stream_gets_inline_previews_by_default(panel_server):
    t = panel_server.teacher()
    code = t.post("/api/teacher/session", json={"title": "Панель", "mode": "url"}).json()["join_code"]
    ts = TeacherStream(panel_server, t.cookies.get("qorgau_teacher"))
    s = panel_server.student()
    try:
        ts.wait_for(lambda x: x["type"] == "snapshot")
        s.hello(join_code=code)
        s.preview(TINY_JPEG)
        msg = ts.wait_for(lambda x: x["type"] == "preview")
        assert msg["jpeg_b64"] and msg["url"]  # the T02 v1 adapter renders only inline previews (DEPENDENCIES D5)
    finally:
        s.close()
        ts.close()
        t.close()
