"""Exam sessions, policies, assignment and teacher permissions."""

from __future__ import annotations

import pytest

from proctor_classctl import AccessDenied
from proctor_classctl.exams import StaleEdit
from proctor_classctl.policies import PolicyError, build_policy_content, match_url, normalize_apps, normalize_urls

from .conftest import ASSISTANT, CO_TEACHER, FULL_CAPS, OBSERVER, STRANGER, TEACHER, URL_POLICY, ack, connect

APP_POLICY = {"mode": "app", "allowed_apps": ["ExamClient.exe"]}


# --------------------------------------------------------------------------- policies


@pytest.mark.parametrize("raw,expected", [
    ("exam.kz", "https://exam.kz/*"),
    ("https://exam.kz", "https://exam.kz/*"),
    ("*.exam.kz", "https://*.exam.kz/*"),
    ("https://exam.kz/course/42", "https://exam.kz/course/42/*"),
    ("https://exam.kz/course/", "https://exam.kz/course/*"),
    ("https://EXAM.kz:8443/a/*", "https://exam.kz:8443/a/*"),
    ("https://пример.испытание/", "https://xn--e1afmkfd.xn--80akhbyknj4f/*"),  # IANA IDN test domain
])
def test_url_rules_normalized(raw, expected):
    assert normalize_urls([raw]).rules == [expected]


@pytest.mark.parametrize("raw,code", [
    ("", "empty"), ("localhost", "invalid_host"), ("http://127.0.0.1/", "loopback"), ("*.kz", "invalid_host"),
    ("https://a.kz/te*", "invalid_wildcard"), ("https://a*.kz/", "invalid_wildcard"), ("ftp://a.kz", "invalid_scheme"),
    ("https://user:pw@a.kz", "invalid"), ("https://a.kz/?q=1", "invalid"), ("https://a.kz/#x", "invalid"),
    ("https://a .kz", "invalid"), ("https://*.10.0.0.1/", "invalid_wildcard"),
])
def test_url_rules_rejected(raw, code):
    with pytest.raises(PolicyError) as e:
        normalize_urls([raw])
    assert e.value.code == code


def test_url_matching_reference():
    rules = ["https://exam.kz/test/42/*", "https://*.sso.kz/*"]
    assert match_url(rules, "https://exam.kz/test/42")
    assert match_url(rules, "https://exam.kz/test/42/q/3?x=1")
    assert not match_url(rules, "https://exam.kz/test/420")
    assert not match_url(rules, "http://exam.kz/test/42")  # scheme must match
    assert not match_url(rules, "https://exam.kz:8443/test/42")
    assert match_url(rules, "https://sso.kz/login") and match_url(rules, "https://a.b.sso.kz/")
    assert not match_url(rules, "https://evilsso.kz/") and not match_url(rules, "https://sso.kz.evil.com/")
    assert not match_url(rules, "javascript:alert(1)") and not match_url(rules, "not a url")


def test_url_policy_requires_start_and_adds_site_with_notice():
    with pytest.raises(PolicyError) as e:
        build_policy_content(mode="url", allowed_urls=["exam.kz"])
    assert e.value.field == "start_url"
    c = build_policy_content(mode="url", start_url="https://quiz.kz/t/7?attempt=2", auth_domains=["accounts.google.com"])
    assert c.start_url == "https://quiz.kz/t/7?attempt=2" and c.allowed_urls == ("https://quiz.kz/*",)
    assert any("добавлен" in w for w in c.warnings_ru)
    payload = c.client_payload()
    assert payload["allowed_urls"] == ["https://quiz.kz/*", "https://accounts.google.com/*"]
    assert payload["auth_domains"] == ["https://accounts.google.com/*"] and payload["site_timer_control"] == "none"


def test_mode_mismatch_and_app_rules():
    with pytest.raises(PolicyError):
        build_policy_content(mode="url", start_url="exam.kz", allowed_apps=["a.exe"])
    with pytest.raises(PolicyError):
        build_policy_content(mode="app", allowed_apps=[], start_url=None)
    with pytest.raises(PolicyError):
        build_policy_content(mode="app", allowed_apps=["a.exe"], allowed_urls=["exam.kz"])
    with pytest.raises(PolicyError):
        build_policy_content(mode="exam")
    assert normalize_apps(["ExamClient.EXE", "examclient.exe"]).rules == ["examclient.exe"]
    for bad in (r"C:\Apps\x.exe", "x.bat", "../x.exe", "cmd.exe", "PowerShell.exe"):
        with pytest.raises(PolicyError):
            normalize_apps([bad])
    assert normalize_apps(["chrome.exe"]).warnings_ru


# --------------------------------------------------------------------------- exams and permissions


def test_create_requires_teacher_role(raw_rig):
    with pytest.raises(AccessDenied):
        raw_rig.control.exams.create(OBSERVER, title="X", policy=URL_POLICY)


def test_edit_with_revision_and_conflict(raw_rig):
    ex = raw_rig.control.exams
    exam = ex.get(TEACHER, raw_rig.exam_id)
    ex.update(TEACHER, raw_rig.exam_id, revision=exam.revision, title="Новое название")
    with pytest.raises(StaleEdit):
        ex.update(CO_TEACHER, raw_rig.exam_id, revision=1, title="Устаревшая правка")
    pol = ex.update_policy(TEACHER, raw_rig.exam_id, exam.default_policy_id, version=1, name=None,
                           policy={**URL_POLICY, "auth_domains": ["login.microsoftonline.com"]})
    assert pol.version == 2
    with pytest.raises(StaleEdit):
        ex.update_policy(CO_TEACHER, raw_rig.exam_id, exam.default_policy_id, version=1, name=None, policy=URL_POLICY)


@pytest.mark.parametrize("who,view,edit,command", [
    (TEACHER, True, True, True),
    (CO_TEACHER, True, True, True),
    (ASSISTANT, True, False, True),
    (OBSERVER, True, False, False),
    (STRANGER, False, False, False),
])
def test_permission_matrix(raw_rig, who, view, edit, command):
    connect(raw_rig, "s1")
    c = raw_rig.control

    def ok(fn) -> bool:
        try:
            fn()
            return True
        except AccessDenied:
            return False

    exam = c.exams.raw(raw_rig.exam_id)
    assert ok(lambda: c.student_views(who, raw_rig.exam_id)) is view
    assert ok(lambda: c.journal_view(who, raw_rig.exam_id)) is view
    assert ok(lambda: c.exams.create_policy(who, raw_rig.exam_id, name="Особая", policy=URL_POLICY)) is edit
    assert ok(lambda: c.assign_policy(who, raw_rig.exam_id, policy_id=exam.default_policy_id, student_ids=["s1"],
                                      idempotency_key=None)) is edit
    assert ok(lambda: c.send_command(who, raw_rig.exam_id, kind="unlock", student_ids=["s1"], payload={},
                                     idempotency_key=None)) is command
    if not view:
        assert any(e.action == "access_denied" and e.actor_id == who.teacher_id for e in c.journal.query())


def test_only_owner_changes_staff(raw_rig):
    ex = raw_rig.control.exams
    with pytest.raises(AccessDenied):
        ex.update(CO_TEACHER, raw_rig.exam_id, revision=ex.raw(raw_rig.exam_id).revision, staff={"t-x": "teacher"})


def test_commands_only_for_students_of_this_exam(raw_rig):
    other = raw_rig.control.exams.create(STRANGER, title="Чужой", policy=URL_POLICY)
    raw_rig.transport.online.add("x1")
    raw_rig.control.student_connected("x1", {"student_label": "Чужой студент"}, other.exam_id)
    with pytest.raises(AccessDenied):
        raw_rig.control.send_command(TEACHER, raw_rig.exam_id, kind="lock", student_ids=["x1"],
                                     payload={"reason_ru": "x"}, idempotency_key=None)
    with pytest.raises(AccessDenied):  # owner of another exam cannot reach my students either
        raw_rig.control.send_command(STRANGER, raw_rig.exam_id, kind="unlock", student_ids=["x1"], payload={}, idempotency_key=None)


# --------------------------------------------------------------------------- assignment


def test_welcome_carries_assigned_policy(raw_rig):
    block = connect(raw_rig, "s1")
    exam = raw_rig.control.exams.raw(raw_rig.exam_id)
    assert block["exam_id"] == raw_rig.exam_id and block["mode"] == "url" and block["policy_id"] == exam.default_policy_id
    assert block["start_url"] == "https://exam.example.kz/test/1" and block["allowed_urls"] == ["https://exam.example.kz/*"]
    v = raw_rig.views()["s1"]["policy"]
    assert v["applied"]["via"] == "welcome" and v["applied"]["confirmed"] is False


def test_assign_policy_to_selected_students_with_capabilities(raw_rig):
    connect(raw_rig, "s1")  # full capabilities
    connect(raw_rig, "s2", caps=None)  # v1 client: no apply_policy
    connect(raw_rig, "s3", caps={**FULL_CAPS, "modes": ["url"]})  # cannot run the app mode
    pol = raw_rig.control.exams.create_policy(TEACHER, raw_rig.exam_id, name="Программа", policy=APP_POLICY)
    res = raw_rig.control.assign_policy(TEACHER, raw_rig.exam_id, policy_id=pol.policy_id, student_ids=["s1", "s2", "s3"],
                                        idempotency_key="assign-0001")
    by = {r["student_id"]: r for r in res["results"]}
    assert by["s1"]["assigned"] and by["s1"]["command"]["kind"] == "apply_policy"
    assert by["s2"]["assigned"] and by["s2"]["command"] is None and "следующем подключении" in by["s2"]["delivery_ru"]
    assert not by["s3"]["assigned"] and "не поддерживает режим" in by["s3"]["unavailable_ru"]
    exam = raw_rig.control.exams.raw(raw_rig.exam_id)
    assert exam.assignments == {"s1": pol.policy_id, "s2": pol.policy_id}
    [msg] = [m for m in raw_rig.transport.to("s1") if m["kind"] == "apply_policy"]
    assert msg["payload"]["mode"] == "app" and msg["payload"]["allowed_apps"] == ["examclient.exe"] and msg["payload"]["version"] == 1
    v = raw_rig.views()["s1"]["policy"]
    assert "у клиента" in v["label_ru"]  # not applied yet: the client did not confirm
    ack(raw_rig, "s1", msg["command_id"], result={"policy_id": pol.policy_id, "policy_version": 1})
    v = raw_rig.views()["s1"]["policy"]
    assert v["label_ru"] == "Применена, подтверждено клиентом" and v["applied"]["confirmed"] is True
    # editing the policy makes the applied version stale until it is sent again
    raw_rig.control.exams.update_policy(TEACHER, raw_rig.exam_id, pol.policy_id, version=1, name=None,
                                        policy={"mode": "app", "allowed_apps": ["ExamClient.exe", "calc.exe"]})
    assert "v2, у клиента — v1" in raw_rig.views()["s1"]["policy"]["label_ru"]
    # start_exam with an app policy for a url-only client is explicitly unavailable
    assert raw_rig.views()["s2"]["actions"]["start_exam"]["available"] is True  # v1: modes not reported


def test_start_exam_unavailable_when_client_lacks_mode(raw_rig):
    pol = raw_rig.control.exams.create_policy(TEACHER, raw_rig.exam_id, name="Программа", policy=APP_POLICY)
    connect(raw_rig, "s1", caps={**FULL_CAPS, "modes": ["app", "url"]})
    raw_rig.control.assign_policy(TEACHER, raw_rig.exam_id, policy_id=pol.policy_id, student_ids=["s1"], idempotency_key=None)
    connect(raw_rig, "s1", caps={**FULL_CAPS, "modes": ["url"]})  # reconnects with a smaller client
    a = raw_rig.views()["s1"]["actions"]["start_exam"]
    assert a["available"] is False and "Отдельная программа" in a["reason_ru"]
