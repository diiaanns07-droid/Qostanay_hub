import assert from "node:assert/strict";
import { test } from "node:test";
import { compileExamPolicy, examUrlAllowed, examBounds } from "../security/exam-policy";

test("approved exam and authentication endpoints retain exact origin/path boundaries", () => {
  const result = compileExamPolicy({ exam_id: "exam", mode: "url", allowed_urls: ["https://exam.example/test/*", "https://login.example/oauth", "https://cdn.example/assets/*"] });
  assert.equal(result.kind, "url");
  if (result.kind !== "url") throw new Error("invalid test policy");
  for (const url of ["https://exam.example/test/", "https://exam.example/test/q1?attempt=3#x", "https://login.example/oauth?code=secret", "https://cdn.example/assets/style.css"])
    assert.equal(examUrlAllowed(result.policy, url), true, url);
  for (const url of ["https://exam.example/", "https://exam.example/testing/q", "https://evil.exam.example/test/", "https://exam.example.evil/test/", "https://exam.example:444/test/", "http://exam.example/test/", "https://login.example/other", "https://exam.example/test/../outside", "https://exam.example/test/%2e%2e/outside", "https://exam.example/test/%2foutside", "https://exam.example/test/%252e%252e/outside", "https://user:pass@exam.example/test/", "https://exam.example./test/", "https://exam.example\\@evil.example/test/", "file:///test/a", "javascript:alert(1)", "data:text/html,test", "blob:https://exam.example/test/id", "qorgau://app/", "ms-settings:"])
    assert.equal(examUrlAllowed(result.policy, url), false, url);
  assert.equal(examUrlAllowed(result.policy, "wss://exam.example/test/socket", "webSocket"), true);
  assert.equal(examUrlAllowed(result.policy, "wss://evil.example/test/socket", "webSocket"), false);
  assert.equal(examUrlAllowed(result.policy, "wss://exam.example/test/socket"), false);
});

test("malformed/overbroad patterns invalidate the whole policy; native application mode stays unsupported", () => {
  for (const pattern of ["*", "https://*.example/*", "https://exam.example/test*", "https://exam.example/*?x=1", "https://u:p@exam.example/*", "file:///test/*", "https://exam.example/#x", "https://exam.example/test/%2f/*", "https://exam.example/\n"]) {
    assert.equal(compileExamPolicy({ exam_id: "x", mode: "url", allowed_urls: ["https://good.example/*", pattern] }).kind, "invalid", pattern);
  }
  assert.equal(compileExamPolicy({ exam_id: "x", mode: "url", allowed_urls: [] }).kind, "invalid");
  assert.equal(compileExamPolicy({ mode: "app", allowed_apps: ["exam.exe"] }).kind, "unsupported");
  assert.equal(compileExamPolicy(null).kind, "none");
});

test("exact query restrictions and wildcard landing URL are deterministic", () => {
  const result = compileExamPolicy({ exam_id: "exam", mode: "url", allowed_urls: ["https://exam.example/test/*", "https://login.example/auth?tenant=school"] });
  if (result.kind !== "url") throw new Error("invalid test policy");
  assert.equal(result.policy.entry, "https://exam.example/test/");
  assert.equal(examUrlAllowed(result.policy, "https://login.example/auth?tenant=school"), true);
  assert.equal(examUrlAllowed(result.policy, "https://login.example/auth?tenant=other"), false);
});

test("bounds cannot cover trusted header/footer and reject invalid geometry", () => {
  assert.deepEqual(examBounds({ x: 0, y: 0, width: 2000, height: 2000 }, 1280, 800), { x: 0, y: 112, width: 1280, height: 624 });
  assert.deepEqual(examBounds({ x: 10, y: 100, width: 500, height: 300 }, 1280, 800, 1.25), { x: 13, y: 125, width: 624, height: 375 });
  for (const value of [null, {}, { x: 0, y: 0, width: NaN, height: 800 }, { x: 0, y: 1000, width: 1280, height: 800 }, { x: 0, y: 200, width: -1, height: 300 }])
    assert.equal(examBounds(value, 1280, 800), null);
});

test("the explicit start page is honored only inside the teacher's allowed URLs", () => {
  const base = { exam_id: "exam", mode: "url", allowed_urls: ["https://exam.example/assets/*", "https://exam.example/test/*"] };
  const selected = compileExamPolicy({ ...base, start_url: "https://exam.example/test/attempt?id=7" });
  assert.equal(selected.kind, "url");
  if (selected.kind !== "url") throw new Error("invalid test policy");
  assert.equal(selected.policy.entry, "https://exam.example/test/attempt?id=7");
  for (const start_url of ["https://other.example/test/", "https://exam.example/admin/", "javascript:alert(1)", "https://exam.example/test/*", 7])
    assert.equal(compileExamPolicy({ ...base, start_url }).kind, "invalid");
  const changed = compileExamPolicy({ ...base, start_url: "https://exam.example/test/attempt?id=8" });
  if (changed.kind !== "url") throw new Error("invalid test policy");
  assert.notEqual(selected.policy.key, changed.policy.key, "a new assignment replaces the old page");
});
