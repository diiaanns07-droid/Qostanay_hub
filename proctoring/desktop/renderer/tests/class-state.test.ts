import assert from "node:assert/strict";
import { test } from "node:test";
import { parseClassState } from "../src/lib/classState";
import { LiveStore } from "../src/lib/liveStore";
import type { StreamEnvelope } from "../../../contracts/ts/qorgau-v1.generated";

const state = { type: "class_state", connection: "connected", server: "test:8765", locked: true, lock_reason_ru: "Контрольная пауза", mic_active: true, audio_direction: "listen" };
const env = (seq: number, message: unknown) => ({ contract: "qorgau.v1", seq, sent_at: new Date().toISOString(), session_id: null, message }) as StreamEnvelope;

test("class state accepts the C2 fields and explicit unlock", () => {
  assert.equal(parseClassState(state)?.locked, true);
  assert.equal(parseClassState({ ...state, locked: false })?.locked, false);
});
test("malformed flags cannot silently become unlock or hide the microphone banner", () => {
  for (const field of ["locked", "mic_active"] as const) {
    for (const value of [undefined, null, "false", "true", 0, 1]) assert.equal(parseClassState({ ...state, [field]: value }), null);
  }
});
test("invalid class state preserves a previous lock", () => {
  const store = new LiveStore();
  store.ingest(env(1, state));
  store.ingest(env(2, { ...state, locked: "false" }));
  assert.equal(store.classState?.locked, true);
});
test("class state survives session changes and valid unlock still works", () => {
  const store = new LiveStore();
  store.ingest(env(1, state));
  store.reset("another-session");
  assert.equal(store.classState?.locked, true);
  store.ingest(env(2, { ...state, locked: false, mic_active: false }));
  assert.equal(store.classState?.locked, false);
  assert.equal(store.classState?.mic_active, false);
});
test("class messages participate in envelope sequence without false resyncs", () => {
  const store = new LiveStore();
  store.ingest(env(1, { type: "hello" }));
  store.ingest(env(2, state));
  store.ingest(env(3, { type: "future_additive_type" }));
  assert.equal(store.seqGaps, 0);
});
test("stale unlock is ignored; reconnect resets sequence for fresh class state", () => {
  const store = new LiveStore();
  store.ingest(env(1, { type: "hello" }));
  store.ingest(env(2, state));
  store.ingest(env(3, state));
  store.ingest(env(2, { ...state, locked: false }));
  assert.equal(store.classState?.locked, true);
  store.ingest(env(1, { type: "hello" }));
  store.ingest(env(2, { ...state, locked: false }));
  assert.equal(store.classState?.locked, false);
});
