import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DESK_SCAN_SECONDS,
  FIXED_CAMERA_REASON,
  deskScanHint,
  deskScanView,
  isDeskScanFinal,
  modeForVariant,
  objectsList,
  pollDeadlineMs,
} from "../src/lib/deskScan";
import { FixtureBridge } from "../src/bridge/fixtureBridge";
import type { DeskScanResult, SessionCreate } from "../../../contracts/ts/qorgau-v1.generated";

const base: DeskScanResult = { scan_id: "d1", state: "clear", started_t_ms: 0, duration_ms: 12_000, objects: [], evidence_id: null, message_ru: null, skip_reason: null };
const obj = (label_ru: string, class_name = "x") => ({ class_name, label_ru, max_confidence: 0.8, seen_ms: 1000 });

test("desk scan: laptop hints follow the 12 s countdown (0–4 tilt, 4–8 left, 8–12 right)", () => {
  assert.equal(deskScanHint("laptop", 0), "Медленно наклоните экран назад, чтобы камера увидела стол");
  assert.equal(deskScanHint("laptop", 3.9), "Медленно наклоните экран назад, чтобы камера увидела стол");
  assert.equal(deskScanHint("laptop", 4), "Плавно поверните ноутбук влево");
  assert.equal(deskScanHint("laptop", 7.5), "Плавно поверните ноутбук влево");
  assert.equal(deskScanHint("laptop", 8), "…и вправо, затем верните на место");
  assert.equal(deskScanHint("laptop", 11.9), "…и вправо, затем верните на место");
  assert.equal(DESK_SCAN_SECONDS, 12);
});

test("desk scan: USB camera has one hint; fixed camera means the teacher checks in the room", () => {
  const usb = "Снимите камеру с монитора и медленно проведите ею над столом слева направо, затем верните на место";
  for (const s of [0, 5, 11]) assert.equal(deskScanHint("usb", s), usb);
  assert.equal(deskScanHint("fixed", 3), "Осмотр места проведёт преподаватель в аудитории");
});

test("desk scan: variant -> query mode (fixed = no camera scan)", () => {
  assert.equal(modeForVariant("laptop"), "laptop");
  assert.equal(modeForVariant("usb"), "usb");
  assert.equal(modeForVariant("fixed"), null);
  assert.equal(pollDeadlineMs(12), 32_000);
});

test("desk scan: student texts are built from state + objects, never 'нарушение'", () => {
  assert.deepEqual(deskScanView({ ...base, state: "clear" }), { tone: "ok", text: "Стол осмотрен: посторонних предметов не замечено" });
  const found = deskScanView({ ...base, state: "objects_found", objects: [obj("телефон", "cell phone"), obj("книга", "book"), obj("телефон", "cell phone")] });
  assert.deepEqual(found, { tone: "warn", text: "Замечено: телефон, книга — уберите их и повторите осмотр" });
  const failed = deskScanView({ ...base, state: "failed", message_ru: "Камера не дала кадров." });
  assert.equal(failed?.text, "Осмотр не удался: Камера не дала кадров. Повторите осмотр или обратитесь к преподавателю");
  assert.notEqual(failed?.tone, "ok");
  assert.ok(!/чисто|не замечено/.test(failed?.text ?? ""));
  assert.equal(deskScanView({ ...base, state: "skipped", skip_reason: "болен" })?.text, "Осмотр пропущен: болен");
  assert.equal(deskScanView({ ...base, state: "skipped", skip_reason: "x", message_ru: "Вариант: камера не двигается. Подтверждено." })?.text, "Вариант: камера не двигается. Подтверждено.");
  assert.match(deskScanView({ ...base, state: "skipped", skip_reason: FIXED_CAMERA_REASON })?.text ?? "", /преподаватель в аудитории/);
  for (const st of ["clear", "objects_found", "failed", "skipped"] as const) {
    assert.ok(!/наруш/i.test(deskScanView({ ...base, state: st, objects: [obj("телефон")] })?.text ?? ""), st);
  }
  assert.equal(deskScanView({ ...base, state: "recording" }), null);
  assert.equal(deskScanView({ ...base, state: "not_started" }), null);
  assert.equal(isDeskScanFinal("recording"), false);
  assert.equal(isDeskScanFinal("not_started"), false);
  assert.equal(isDeskScanFinal("objects_found"), true);
  assert.equal(objectsList([obj("", "cell phone")]), "cell phone");
});

const createBody = (label: string | null): SessionCreate => ({
  source: { mode: "synthetic", camera_index: 0, replay_id: null, width: 640, height: 480, fps: 30 },
  exam_id: "demo-exam-1",
  student_label: label,
  consent: { accepted: true, text_version: "consent-ru-1", accepted_at: new Date().toISOString() },
  retain_media: false,
});

async function fixtureSession(label: string | null) {
  const b = new FixtureBridge();
  const s = await b.createSession(createBody(label));
  assert.ok(s.ok);
  const sid = s.data.session_id;
  assert.ok((await b.runPreflight(sid)).ok);
  return { b, sid };
}

test("FIXTURE desk scan: start -> recording -> clear; 'phone' label -> objects_found; skip needs the PIN", async () => {
  const clean = await fixtureSession("Студент");
  const phone = await fixtureSession("phone-demo");
  try {
    const g0 = await clean.b.getDeskScan(clean.sid);
    assert.ok(g0.ok && g0.data.state === "not_started");
    const bad = await clean.b.startDeskScan(clean.sid, { duration_s: 4, mode: "laptop" });
    assert.ok(!bad.ok);
    const [r1, r2] = await Promise.all([
      clean.b.startDeskScan(clean.sid, { duration_s: 5, mode: "laptop" }),
      phone.b.startDeskScan(phone.sid, { duration_s: 5, mode: "usb" }),
    ]);
    assert.ok(r1.ok && r1.data.state === "recording");
    assert.ok(r2.ok && r2.data.state === "recording");
    const again = await clean.b.startDeskScan(clean.sid, { duration_s: 5, mode: "laptop" });
    assert.ok(!again.ok && again.error.code === "INVALID_STATE", "a second start while recording is refused");
    await new Promise((r) => setTimeout(r, 5_300));
    const c = await clean.b.getDeskScan(clean.sid);
    assert.ok(c.ok && c.data.state === "clear");
    const p = await phone.b.getDeskScan(phone.sid);
    assert.ok(p.ok && p.data.state === "objects_found");
    assert.deepEqual(p.data.objects, [{ class_name: "cell phone", label_ru: "телефон", max_confidence: 0.82, seen_ms: 1500 }]);
    assert.equal(deskScanView(p.data)?.text, "Замечено: телефон — уберите его и повторите осмотр");

    const locked = await clean.b.skipDeskScan(clean.sid, { reason: FIXED_CAMERA_REASON });
    assert.ok(!locked.ok && locked.error.code === "INVALID_STATE", "skip without PIN is refused");
    assert.ok((await clean.b.operatorUnlock(clean.b.operatorPin)).ok);
    const sk = await clean.b.skipDeskScan(clean.sid, { reason: FIXED_CAMERA_REASON });
    assert.ok(sk.ok && sk.data.state === "skipped" && sk.data.skip_reason === FIXED_CAMERA_REASON);
  } finally {
    clean.b.dispose();
    phone.b.dispose();
  }
});
