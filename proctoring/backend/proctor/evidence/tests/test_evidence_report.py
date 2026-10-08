"""A08 report: escaping, no active content or external URLs, honest labels, integrity manifest, real renderer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proctor.evidence import report
from proctor.evidence.sample import SID, build_sample
from proctor_contracts.v1 import (
    ExportManifest,
    HumanReviewCreate,
    IncidentDetail,
    SessionInfo,
    SessionSummary,
)

EVIL = [
    "<script>alert('x')</script>",
    "\"><img src=x onerror=alert(1)>",
    "</style><svg/onload=alert(1)>",
    "<a href=\"https://evil.example/x\">клик</a>",
    "javascript:alert(1)",
    "<iframe src=\"http://evil.example\"></iframe>",
    "{{7*7}} ${7*7} &amp; &lt;",
]
FORBIDDEN_TAGS = {"script", "iframe", "object", "embed", "svg", "a", "link", "base", "form", "input", "button", "meta-refresh"}


class Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))

    def handle_data(self, data):
        self.text.append(data)


def audit(page: str) -> Audit:
    a = Audit()
    a.feed(page)
    return a


def assert_inert(page: str) -> Audit:
    a = audit(page)
    assert not FORBIDDEN_TAGS & set(a.tags), set(a.tags) & FORBIDDEN_TAGS
    for tag, name, value in a.attrs:
        assert not name.startswith("on"), (tag, name)
        assert name not in ("href", "action", "formaction", "srcset", "background", "style"), (tag, name)
        if name == "src":
            assert tag == "img" and value.startswith("data:image/jpeg;base64,"), (tag, value[:40])
        if tag == "meta" and name == "http-equiv":
            assert value == "Content-Security-Policy"
    assert re.search(r"url\(", page) is None
    assert re.search(r"(src|href)\s*=\s*[\"']?(https?:|//|javascript:)", page, re.I) is None
    return a


@pytest.fixture()
def evil_snapshot(tmp_path):
    store = build_sample(tmp_path / "data", student_label=EVIL[0], review_comment=EVIL[1])
    try:
        store.add_review(SID, "inc-gaze-1", HumanReviewCreate(decision="inconclusive", comment=" ".join(EVIL), operator=EVIL[2][:64]))
        # answers/env text from a malicious client end up in the report too
        conn = store._conn
        conn.execute("UPDATE answers SET value_json=? WHERE session_id=? AND question_id='q3'", (json.dumps(" ".join(EVIL)), SID))
        snap = store.export_snapshot(SID)
        yield store, snap
    finally:
        store.close()


def test_html_escapes_every_dynamic_value(evil_snapshot):
    _, snap = evil_snapshot
    page = report.render_html(snap)
    a = assert_inert(page)
    text = "".join(a.text)
    for payload in EVIL:
        assert payload in text or payload.replace("&amp;", "&").replace("&lt;", "<") in text, payload  # shown as text
    assert "<script" not in page and "<img src=x" not in page and "<svg" not in page
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in page
    assert '<meta http-equiv="Content-Security-Policy"' in page and "default-src &#x27;none&#x27;" in page


def test_report_content_is_honest(evil_snapshot):
    _, snap = evil_snapshot
    page = report.render_html(snap)
    for needle in (
        "СИНТЕТИЧЕСКИЕ ДАННЫЕ",
        "не доказательство нарушения",
        "автоматических санкций нет",
        "«неизвестно», а не «нарушений нет»",
        "камера отключена",
        "результат не определён",
        "не закрыт",
        "Приоритет проверки",
        "История решений сохраняется полностью",
        "не защищают от изменения владельцем компьютера",
        "только зафиксировано",
    ):
        assert needle in page, needle
    assert "вероятность списывания»" in page  # only inside the disclaimer that it is NOT that
    assert page.count("data:image/jpeg;base64,") == 1


def test_json_export_manifest_matches_content(evil_snapshot):
    store, snap = evil_snapshot
    html_bytes = report.render_html(snap).encode("utf-8")
    payload = json.loads(report.dumps_json(report.build_json(snap, html_bytes)))
    manifest = ExportManifest.model_validate(payload["manifest"])
    files = {f.name: f for f in manifest.files}
    assert files["report.html"].sha256 == hashlib.sha256(html_bytes).hexdigest()
    for ev in snap.evidence.values():
        _, data = store.evidence_media(SID, ev.item.evidence_id)
        assert files[f"{ev.item.evidence_id}.jpg"].sha256 == hashlib.sha256(data).hexdigest()
    SessionInfo.model_validate(payload["session"])
    SessionSummary.model_validate(payload["summary"])
    for d in payload["incidents"]:
        IncidentDetail.model_validate(d)
    assert payload["session"]["student_label"] == EVIL[0]
    assert payload["format"] == "qorgau.report.v1" and payload["incidents_total"] == 6
    assert any("владельц" in x for x in manifest.limitations_ru)
    assert manifest.config_versions["producer.bootstrap.phone.version"] == "sample-1"


def test_mode_banners_and_recovery(make_store, fx):
    store = make_store()
    store.open()
    for sid, mode in (("s-replay", "replay"), ("s-live", "live")):
        store.upsert_session(fx.session_info(sid, mode=mode))
        store.upsert_session(fx.session_info(sid, mode=mode, state="finished"))
    replay = report.render_html(store.export_snapshot("s-replay"))
    live = report.render_html(store.export_snapshot("s-live"))
    assert "REPLAY — ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ" in replay and "СИНТЕТИЧЕСКИЕ" not in replay
    assert "ЖИВАЯ СЕССИЯ" in live and "REPLAY" not in live
    store.upsert_session(fx.session_info("s-crash", mode="live"))
    store.close()
    again = make_store(store.data_dir)
    again.open()
    crashed = report.render_html(again.export_snapshot("s-crash"))
    assert "СЕССИЯ ПРЕРВАНА СБОЕМ" in crashed


def test_missing_media_is_reported_not_broken(tmp_path):
    store = build_sample(tmp_path / "data")
    try:
        token = store._conn.execute("SELECT media_token FROM sessions WHERE session_id=?", (SID,)).fetchone()[0]
        for f in (store.vault.root / token).iterdir():
            f.unlink()
        snap = store.export_snapshot(SID)
        page = report.render_html(snap)
        assert_inert(page)
        assert "файл снимка отсутствует" in page and "data:image" not in page
        payload = report.build_json(snap)
        assert [f["name"] for f in payload["manifest"]["files"]] == ["report.html"]
        assert payload["evidence_files"][0]["status"] == "missing"
    finally:
        store.close()


def test_metadata_only_session_has_no_media(make_store, fx):
    store = make_store()
    store.open()
    store.upsert_session(fx.session_info("s", retain_media=False))
    store.record_incident_change(fx.incident_change("s"))
    store.upsert_session(fx.session_info("s", state="finished"))
    page = report.render_html(store.export_snapshot("s"))
    assert "Снимки не сохранялись" in page and "data:image" not in page


def _playwright_env() -> dict | None:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        return None
    root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "NODE_PATH": os.pathsep.join(p for p in (root, os.environ.get("NODE_PATH", "")) if p)}
    probe = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, env=env)
    return env if probe.returncode == 0 else None


def test_report_renders_and_prints_in_chromium(evil_snapshot, tmp_path):
    env = _playwright_env()
    if env is None:
        pytest.skip("Node + Playwright + Chromium not available (real-renderer check runs where they are)")
    _, snap = evil_snapshot
    html_path = tmp_path / "report.html"
    html_path.write_bytes(report.render_html(snap).encode("utf-8"))
    pdf, png = tmp_path / "report.pdf", tmp_path / "report.png"
    proc = subprocess.run(
        ["node", str(Path(__file__).with_name("render_check.cjs")), str(html_path), str(pdf), str(png)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if proc.returncode != 0 and "Executable doesn't exist" in proc.stderr:
        pytest.skip("Playwright browser binary not installed")
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert all(u.startswith(("file://", "data:")) for u in result["requests"]), result["requests"]
    assert result["messages"] == [], result["messages"]  # no CSP violations, no errors
    info = result["info"]
    assert info["scripts"] == 0 and info["links"] == 0
    assert info["images"] and all(i["loaded"] and i["scheme"] == "data" for i in info["images"])
    assert "СИНТЕТИЧЕСКИЕ ДАННЫЕ" in info["banner"]
    assert result["mobileOverflow"] <= 0
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page[^s]", data)) >= 2
