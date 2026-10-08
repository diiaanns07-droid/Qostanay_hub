import assert from "node:assert/strict";
import { test } from "node:test";
import { examChecks } from "../src/lib/examChecks";
import type { EnvironmentCapabilities, Health, HealthReport, PreflightReport } from "../../../contracts/ts/qorgau-v1.generated";

const part = (component: Health["component"], status: Health["status"] = "ok", code = "ok"): Health => ({ component, status, code, message: null, details: {}, since_t_session_ms: null });
const health = (components: Health[]): HealthReport => ({ backend_version: "test", contract_version: "1.1.0", server_time: new Date().toISOString(), overall: "ok", active_session_id: null, components });
const caps: EnvironmentCapabilities = { reported_at: new Date().toISOString(), platform: "win32", shell_version: "test", exam_mode_supported: true, items: [
  { action: "shortcut_ctrl_c", status: "blocked", mechanism: "test", note_ru: null, verified_on: null },
  { action: "shortcut_alt_tab", status: "detected_only", mechanism: "test", note_ru: null, verified_on: null },
  { action: "shortcut_win", status: "unsupported", mechanism: "test", note_ru: null, verified_on: null },
] };
const input = { health: health([part("capture"), part("phone"), part("attention")]), caps, mode: "live" as const, report: null };

test("before camera consent, module readiness does not claim a working camera or missing 1.1 modules", () => {
  const rows = examChecks(input).modules;
  assert.equal(rows.find((r) => r.id === "capture")?.status, "Ожидает проверки");
  assert.equal(rows.find((r) => r.id === "phone")?.status, "Готово");
  for (const id of ["audio", "identity"]) assert.equal(rows.find((r) => r.id === id)?.status, "Не заявлено сервисом");
});
test("camera readiness follows preflight and a later health failure wins", () => {
  const report = { checks: [{ check_id: "camera", status: "pass", message_ru: "Камера проверена" }] } as PreflightReport;
  assert.equal(examChecks({ ...input, report }).modules[0].status, "Готово");
  assert.equal(examChecks({ ...input, report, health: health([part("capture", "error", "camera_disconnected")]) }).modules[0].status, "Ошибка проверки");
  assert.equal(examChecks({ ...input, report, health: health([part("capture", "degraded")]) }).modules[0].status, "С ограничениями");
});
test("keys keep separate blocked, detected-only, unsupported and unverified statuses", () => {
  const rows = examChecks(input).keys;
  assert.equal(rows.find((r) => r.id === "shortcut_ctrl_c")?.status, "блокируется");
  assert.equal(rows.find((r) => r.id === "shortcut_alt_tab")?.status, "только фиксируется");
  assert.equal(rows.find((r) => r.id === "shortcut_win")?.status, "не поддерживается");
  assert.equal(rows.find((r) => r.id === "shortcut_print_screen")?.status, "не проверено");
});
test("no capabilities, unsupported exam mode and backend loss cannot claim protection", () => {
  for (const variant of [{ caps: null }, { caps: { ...caps, exam_mode_supported: false } }, { backendLost: true }]) {
    assert(examChecks({ ...input, ...variant }).keys.every((r) => r.status === "не проверено"));
  }
  assert(examChecks({ ...input, backendLost: true }).modules.every((r) => r.status === "Нет связи"));
  assert(examChecks({ ...input, health: null }).modules.every((r) => r.status === "Не проверено"));
});
test("synthetic and replay never claim to use the live camera", () => {
  const synthetic = examChecks({ ...input, mode: "synthetic" }).modules;
  assert.equal(synthetic[0].status, "Камера не используется");
  for (const id of ["phone", "attention"]) assert.equal(synthetic.find((r) => r.id === id)?.status, "Имитация");
  assert.equal(examChecks({ ...input, mode: "replay" }).modules[0].status, "Камера не используется");
});
test("present audio/identity honor module health rather than the class mic flag", () => {
  const rows = examChecks({ ...input, health: health([part("audio", "degraded"), part("identity", "unavailable", "module_not_integrated")]) }).modules;
  assert.equal(rows.find((r) => r.id === "audio")?.status, "С ограничениями");
  assert.equal(rows.find((r) => r.id === "identity")?.status, "Модуль не подключён");
});
