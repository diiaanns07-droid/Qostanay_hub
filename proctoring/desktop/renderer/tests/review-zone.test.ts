import assert from "node:assert/strict";
import { test } from "node:test";
import { parseReviewZone } from "../src/components/ReviewZone";
import { LiveStore } from "../src/lib/liveStore";
import type { StreamEnvelope } from "../../../contracts/ts/qorgau-v1.generated";

test("REPLAY end reaches the main UI and never leaks into another session", () => {
  const store = new LiveStore();
  store.reset("replay-current");
  const observation = (seq: number, sid: string, component = "capture") => ({
    contract: "qorgau.v1", seq, sent_at: new Date().toISOString(), session_id: sid,
    message: { type: "observation", observation: { kind: "health", session_id: sid, t_session_ms: 30000,
      health: { component, status: "stopped", code: "replay_ended" } } },
  }) as StreamEnvelope;
  let notifications = 0;
  const unsubscribe = store.subscribe("main")(() => notifications++);
  store.ingest(observation(1, "other"));
  store.ingest(observation(2, "replay-current", "phone"));
  assert.equal(store.captureHealth, null);
  assert.equal(notifications, 0);
  store.ingest(observation(3, "replay-current"));
  assert.equal(store.captureHealth?.code, "replay_ended");
  assert.equal(notifications, 1);
  store.reset("next");
  assert.equal(store.captureHealth, null);
  unsubscribe();
});

test("summary shows each reported zone without classifying incidents", () => {
  for (const zone of ["green", "yellow", "red", "grey"]) {
    assert.deepEqual(parseReviewZone({ review_zone: zone, review_zone_reasons_ru: ["Причина сервиса"], review_zone_rule_version: "zone-rule-1" }), { zone, reasons: ["Причина сервиса"], ruleVersion: "zone-rule-1" });
  }
});
test("missing or malformed zone is never shown as green", () => {
  for (const value of [null, {}, { review_zone: "GREEN" }, { review_zone: "toString" }, { review_zone: true }]) assert.equal(parseReviewZone(value).zone, null);
  assert.deepEqual(parseReviewZone({ review_zone_reasons_ru: [12, "A", "B", "C", "D"] }).reasons, ["A", "B", "C"]);
});
