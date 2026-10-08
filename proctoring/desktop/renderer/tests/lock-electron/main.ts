import { app, BrowserWindow, ipcMain } from "electron";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import assert from "node:assert/strict";
import { ClassLockController, LOCK_ACK_CHANNEL } from "../../../main/src/class-lock";
import { receiptFor, type LockRequest } from "../../../shared/class-lock";
import type { BackendClient } from "../../../main/src/backend/client";

// Deliberately does not import production main/guard or open media devices.
app.setPath("userData", join(__dirname, "profile"));
const applied: Record<string, unknown>[] = [], blocked: boolean[] = [];
const results: string[] = [];
let window: BrowserWindow;
const base = { type: "class_state", connection: "connected", server: "fixture-only", student_id: "student-1", mic_active: false };
const make = (locked: boolean, cid: string): LockRequest => ({ command_id: cid, student_id: "student-1", class_session_id: "class-1",
  source_session_id: "local-1", backend_instance_id: "backend-1", request_token: `${cid}-token`, locked,
  reason_ru: locked ? "Телефон на столе — проверка преподавателя" : null, expires_at: new Date(Date.now() + 5000).toISOString(), recovery: false });
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function until(check: () => boolean | Promise<boolean>) {
  const end = Date.now() + 6000;
  while (Date.now() < end) { if (await check()) return; await delay(25); }
  throw new Error("fixture condition timed out");
}
let controller: ClassLockController;
async function display(request: LockRequest | null, locked: boolean) {
  const state = { ...base, locked, lock_request: request, lock_reason_ru: locked ? "Телефон на столе — проверка преподавателя" : null };
  controller.consumeClassState(state);
  await window.webContents.executeJavaScript(`window.fixtureUpdate(${JSON.stringify(state)})`);
}
app.whenReady().then(async () => {
  try {
    window = new BrowserWindow({ width: 900, height: 650, show: true, title: "Adal safe lock fixture (no OS restrictions)",
      webPreferences: { preload: join(__dirname, "preload.cjs"), contextIsolation: true, sandbox: true, nodeIntegration: false } });
    window.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    window.webContents.session.setPermissionCheckHandler(() => false);
    const client = { json: async (_method: string, path: string, body: Record<string, unknown>) => {
      assert.equal(path, "/v1/class/lock/ack");
      applied.push(body);
      return { ok: true, data: { accepted: true } };
    } } as unknown as BackendClient;
    controller = new ClassLockController({ client, window: () => window, setExamBlocked: value => blocked.push(value) });
    ipcMain.handle(LOCK_ACK_CHANNEL, (event, body) => event.sender === window.webContents && event.senderFrame === event.sender.mainFrame
      ? controller.confirmApplied(body) : { accepted: false, reason: "untrusted_sender" });
    await window.loadFile(join(__dirname, "index.html"));
    await until(() => window.webContents.executeJavaScript("typeof window.fixtureUpdate === 'function'"));
    const lock = make(true, "cmd-1");
    await display(lock, false);
    await until(() => applied.length === 1);
    if (!applied[0].applied) console.error(await window.webContents.executeJavaScript(`(() => { const e=document.querySelector('[data-adal-lock]'), a=document.querySelector('[data-adal-app]'); return {receipt:${JSON.stringify(applied[0])},visibility:document.visibilityState,inert:a?.inert,hidden:a?.getAttribute('aria-hidden'), token:e?.getAttribute('data-lock-token'), reason:document.querySelector('[data-lock-reason]')?.textContent, box:e?.getBoundingClientRect().toJSON(), width:innerWidth,height:innerHeight,css:e?{opacity:getComputedStyle(e).opacity,visibility:getComputedStyle(e).visibility,display:getComputedStyle(e).display}:null,hit:e?.contains(document.elementFromPoint(innerWidth/2,innerHeight/2))};})()`));
    assert.equal(applied[0].applied, true); assert.equal(applied[0].locked, true);
    assert.equal(blocked[0], true); results.push("real Electron IPC + painted React lock acknowledged");
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-lock-reason]').textContent"), lock.reason_ru);
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-adal-app]').inert"), true);
    results.push("custom reason visible; underlying app inert");
    writeFileSync(join(__dirname, "lock.png"), (await window.webContents.capturePage()).toPNG());
    const wrong = await window.webContents.executeJavaScript(`window.qorgauLock.confirmApplied(${JSON.stringify({ ...receiptFor(lock, true), student_id: "wrong" })})`);
    assert.equal(wrong.accepted, false); assert.equal(applied.length, 1); results.push("wrong student rejected before backend");
    await display(null, true);
    const unlock = make(false, "cmd-2");
    await display(unlock, true);
    await until(() => applied.length === 2);
    assert.equal(applied[1].applied, true); assert.equal(applied[1].locked, false);
    assert.equal(blocked.at(-1), false); results.push("unlock acknowledged only after overlay removed");
    await display(null, false);
    await window.webContents.executeJavaScript("document.querySelector('button').click()");
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('button').textContent"), "Clicked");
    results.push("app interaction restored");
    controller.reset();
    assert.equal((await controller.confirmApplied(receiptFor(lock, true))).accepted, false);
    results.push("old receipt rejected after shell/backend reset");
    const expired = { ...make(true, "expired"), expires_at: new Date(Date.now() - 1000).toISOString() };
    await display(expired, false); await delay(120);
    assert.equal(applied.length, 2); results.push("expired intent does not produce success");
    console.log(JSON.stringify({ ok: true, tests: results, nativeHooks: false, mediaDevices: false }));
    writeFileSync(join(__dirname, "results.json"), JSON.stringify({ ok: true, tests: results }, null, 2));
    window.destroy(); app.exit(0);
  } catch (error) { console.error(error); window?.destroy(); app.exit(1); }
});
setTimeout(() => { console.error("lock fixture watchdog"); app.exit(2); }, 25000).unref();
