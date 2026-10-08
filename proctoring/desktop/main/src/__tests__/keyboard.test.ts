import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyExamKey, KeyEventThrottle, type KeyInput } from "../environment/keyboard";

const k = (key: string, code: string, mods: Partial<KeyInput> = {}): KeyInput => ({
  type: "keyDown",
  key,
  code,
  control: false,
  alt: false,
  shift: false,
  meta: false,
  ...mods,
});

test("clipboard shortcuts are blocked, also on ru/kk layouts (physical key)", () => {
  assert.equal(classifyExamKey(k("c", "KeyC", { control: true }))?.action, "shortcut_ctrl_c");
  assert.equal(classifyExamKey(k("с", "KeyC", { control: true }))?.action, "shortcut_ctrl_c"); // Cyrillic es
  assert.equal(classifyExamKey(k("м", "KeyV", { control: true }))?.action, "shortcut_ctrl_v");
  assert.equal(classifyExamKey(k("Insert", "Insert", { shift: true }))?.action, "shortcut_ctrl_v");
  assert.equal(classifyExamKey(k("Insert", "Insert", { control: true }))?.action, "shortcut_ctrl_c");
  assert.equal(classifyExamKey(k("x", "KeyX", { control: true }))?.enforcement, "blocked");
  assert.equal(classifyExamKey(k("Delete", "Delete", { shift: true }))?.action, "shortcut_ctrl_x");
});

test("OS-level keys are detected_only (window scope cannot stop them)", () => {
  for (const input of [
    k("Meta", "MetaLeft"),
    k("Tab", "Tab", { alt: true }),
    k("PrintScreen", "PrintScreen"),
    k("d", "KeyD", { meta: true }),
  ]) {
    const d = classifyExamKey(input);
    assert.equal(d?.enforcement, "detected_only", JSON.stringify(input));
  }
});

test("tab switching, close, devtools, reload, new window are blocked", () => {
  assert.equal(classifyExamKey(k("Tab", "Tab", { control: true }))?.action, "shortcut_ctrl_tab");
  assert.equal(classifyExamKey(k("Tab", "Tab", { control: true, shift: true }))?.shortcut, "Ctrl+Shift+Tab");
  assert.equal(classifyExamKey(k("PageDown", "PageDown", { control: true }))?.action, "shortcut_ctrl_tab");
  assert.equal(classifyExamKey(k("F4", "F4", { alt: true }))?.action, "shortcut_alt_f4");
  assert.equal(classifyExamKey(k("F12", "F12"))?.action, "devtools_blocked");
  assert.equal(classifyExamKey(k("C", "KeyC", { control: true, shift: true }))?.action, "devtools_blocked");
  assert.equal(classifyExamKey(k("F5", "F5"))?.action, "navigation_blocked");
  assert.equal(classifyExamKey(k("r", "KeyR", { control: true }))?.action, "navigation_blocked");
  assert.equal(classifyExamKey(k("n", "KeyN", { control: true }))?.action, "new_window_blocked");
  const print = classifyExamKey(k("p", "KeyP", { control: true }));
  assert.equal(print?.prevent, true);
  assert.equal(print?.action, null);
});

test("ordinary typing and editing keys are allowed", () => {
  for (const input of [
    k("a", "KeyA"),
    k("ә", "KeyA"),
    k("A", "KeyA", { shift: true }),
    k("a", "KeyA", { control: true }),
    k("z", "KeyZ", { control: true }),
    k("Backspace", "Backspace"),
    k("Tab", "Tab"),
    k("Enter", "Enter"),
    k("ArrowLeft", "ArrowLeft"),
  ]) {
    assert.equal(classifyExamKey(input), null, JSON.stringify(input));
  }
  assert.equal(classifyExamKey({ ...k("c", "KeyC", { control: true }), type: "char" }), null);
});

test("throttle reports a held shortcut once per window, never key-up or auto-repeat", () => {
  let now = 0;
  const t = new KeyEventThrottle(500, () => now);
  const input = k("v", "KeyV", { control: true });
  const d = classifyExamKey(input)!;
  assert.equal(t.shouldReport(input, d), true);
  assert.equal(t.shouldReport({ ...input, isAutoRepeat: true }, d), false);
  now = 100;
  assert.equal(t.shouldReport(input, d), false);
  now = 700;
  assert.equal(t.shouldReport(input, d), true);
  assert.equal(t.shouldReport({ ...input, type: "keyUp" }, d), false);
});
