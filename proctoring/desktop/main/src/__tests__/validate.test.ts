import assert from "node:assert/strict";
import { test } from "node:test";
import { fixtures } from "@contracts/fixtures.generated";
import * as v from "../ipc/validate";

const SessionCreateSynthetic = fixtures["SessionCreate.synthetic"];

test("id accepts contract ids and rejects paths/oversize/unicode", () => {
  assert.equal(v.id("sess-01:a.b_c"), "sess-01:a.b_c");
  for (const bad of ["../etc", "..", ".", "a/b", "a\\b", "", "x".repeat(129), "сессия", "a b", 12, null, undefined, {}]) {
    assert.throws(() => v.id(bad), v.ValidationError, String(bad));
  }
});

test("sessionCreate accepts the contract fixture and rejects extra/missing keys", () => {
  const body = structuredClone(SessionCreateSynthetic) as unknown as Record<string, unknown>;
  assert.deepEqual(v.sessionCreate(body), SessionCreateSynthetic);
  assert.throws(() => v.sessionCreate({ ...body, extra: 1 }), /unknown field/);
  const { retain_media: _omit, ...missing } = body;
  assert.throws(() => v.sessionCreate(missing), /missing field/);
  const replay = structuredClone(body) as { source: Record<string, unknown> };
  replay.source.mode = "replay";
  replay.source.replay_id = null;
  assert.throws(() => v.sessionCreate(replay), /required for replay/);
  const badDate = structuredClone(body) as { consent: Record<string, unknown> };
  badDate.consent.accepted_at = "2026-10-08T10:00:00.000"; // naive datetime
  assert.throws(() => v.sessionCreate(badDate), /offset/);
  assert.throws(() => v.sessionCreate([]), /object/);
  class Fake {
    constructor() {
      Object.assign(this, body);
    }
  }
  assert.throws(() => v.sessionCreate(new Fake()), /object/, "class instances are not plain objects");
});

test("bodies: reason, answer, review, calibration target, format, pin", () => {
  assert.deepEqual(v.pauseRequest({ reason: "x" }), { reason: "x" });
  assert.throws(() => v.abortRequest({ reason: "" }));
  assert.throws(() => v.abortRequest({ reason: "x".repeat(201) }));
  assert.deepEqual(v.answerUpsert({ value: ["opt-a", "opt-b"], client_seq: 3 }), { value: ["opt-a", "opt-b"], client_seq: 3 });
  assert.throws(() => v.answerUpsert({ value: ["../x"], client_seq: 1 }));
  assert.throws(() => v.answerUpsert({ value: "a", client_seq: -1 }));
  assert.throws(() => v.answerUpsert({ value: "a".repeat(4001), client_seq: 1 }));
  assert.deepEqual(v.humanReviewCreate({ decision: "dismissed", comment: "", operator: "T1" }), {
    decision: "dismissed",
    comment: "",
    operator: "T1",
  });
  assert.throws(() => v.humanReviewCreate({ decision: "guilty", comment: "", operator: "T1" }));
  assert.equal(v.calibrationTarget("left"), "left");
  assert.throws(() => v.calibrationTarget("behind"));
  assert.equal(v.exportFormat("html"), "html");
  assert.throws(() => v.exportFormat("pdf"));
  assert.equal(v.pin("1234"), "1234");
  assert.throws(() => v.pin("12"));
  assert.throws(() => v.pin("12 34"));
});

test("checkSize caps serialized arguments", () => {
  v.checkSize([{ reason: "x" }]);
  assert.throws(() => v.checkSize(["x".repeat(v.MAX_ARG_JSON_BYTES)]), /larger/);
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  assert.throws(() => v.checkSize([cyclic]), /serializable/);
});
