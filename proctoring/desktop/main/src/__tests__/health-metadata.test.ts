import assert from "node:assert/strict";
import { test } from "node:test";
import { createApi } from "../ipc/api";
import type { BackendClient } from "../backend/client";
import { OperatorAuth } from "../shell/operator";
import { ShellStateMachine } from "../shell/state";
import { FakeGuard } from "./helpers";

test("health preserves backend report and adds only local display metadata", async () => {
  let response = { ok: true, data: { overall: "ok", server_time: "2026-10-08T00:00:00Z" } } as unknown;
  let called = "";
  const api = createApi({
    client: { json: async (_m: string, path: string) => { called = path; return response; } } as unknown as BackendClient,
    machine: new ShellStateMachine(new FakeGuard(), { shell_version: "test", platform: "test" }),
    operator: new OperatorAuth({}), capabilities: () => null, emergencyExit: async () => {}, saveFile: async () => null,
    healthMetadata: () => ({ computer_name: "LOCAL-PC", class_configured: false }),
  });
  assert.deepEqual(await api.health(), { ok: true, data: { overall: "ok", server_time: "2026-10-08T00:00:00Z", computer_name: "LOCAL-PC", class_configured: false } });
  assert.equal(called, "/v1/health");
  response = { ok: false, error: { code: "INTERNAL" } };
  assert.equal(await api.health(), response, "health failure must not become a successful metadata-only report");
});
