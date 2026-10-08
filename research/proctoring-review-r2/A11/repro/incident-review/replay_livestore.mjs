// A11 ISOLATED REPRO: A07's real LiveStore (renderer/src/lib/liveStore.ts @3fef6fb, bundled unchanged with esbuild)
// fed with the stream + REST responses recorded from the real A01r2+A05+A08 backend (out_backend/).
// Reproduces what Operator.tsx shows in its list/filters and what Summary.tsx computes from SessionSummary.
import { build } from "esbuild";
import { readFileSync, mkdirSync } from "node:fs";
import { pathToFileURL } from "node:url";

const W = new URL(".", import.meta.url).pathname;
const UI = `${W}ui/proctoring/desktop`;
mkdirSync(`${W}build`, { recursive: true });
await build({
  entryPoints: [`${UI}/renderer/src/lib/liveStore.ts`],
  bundle: true,
  format: "esm",
  platform: "node",
  outfile: `${W}build/liveStore.mjs`,
  alias: { "@contracts": `${W}ui/proctoring/contracts/ts` },
  nodePaths: [`${UI}/node_modules`],
  logLevel: "error",
});
const { LiveStore } = await import(pathToFileURL(`${W}build/liveStore.mjs`).href);

const OUT = `${W}out_backend`;
const env = JSON.parse(readFileSync(`${OUT}/envelopes.json`, "utf8"));
const restAfterReview = JSON.parse(readFileSync(`${OUT}/rest_incidents_after_review.json`, "utf8"));
const restFinal = JSON.parse(readFileSync(`${OUT}/rest_incidents_final.json`, "utf8"));
const summary = JSON.parse(readFileSync(`${OUT}/summary.json`, "utf8"));
const session = JSON.parse(readFileSync(`${OUT}/session.json`, "utf8"));

const live = new LiveStore();
live.reset(session.session_id);
const phoneOpenedIdx = env.findIndex((e) => e.message.type === "incident" && e.message.change.incident.rule_id === "phone_visible" && e.message.change.change === "opened");
const iid = env[phoneOpenedIdx].message.change.incident.incident_id;
const show = (label) => {
  const all = [...live.incidents.values()].sort((a, b) => a.t_start_ms - b.t_start_ms);
  // Operator.tsx:96 and :66 (verbatim expressions)
  const pending = all.filter((i) => i.review_status === "pending").length;
  const autoSelect = (all.find((i) => i.review_status === "pending") ?? all[0])?.incident_id;
  const reviewedFilter = all.filter((i) => i.review_status !== "pending").map((i) => i.rule_id);
  console.log(
    label.padEnd(58),
    JSON.stringify(all.map((i) => `${i.rule_id}#${i.update_seq}:${i.review_status}:ev${i.evidence_ids.length}`)),
    `| list "ожидают проверки": ${pending} | filter "проверенные": ${JSON.stringify(reviewedFilter)} | auto-select: ${autoSelect?.split(".").slice(-2).join(".")}`,
  );
};

// 1) live operator console: stream up to the phone OPENED change
for (const e of env.slice(0, phoneOpenedIdx + 1)) live.ingest(e);
show("after WS opened (seq 0)");
// 2) teacher records 'confirmed' (IncidentCard.submit -> getIncident only); then any REST refetch of the list
//    (Operator.load on remount, App.resync on seq-gap/reconnect) delivers the A08 overlay:
live.replaceIncidents(restAfterReview);
show("after REST list refetch post-review (A08: seq 0 confirmed)");
// 3) rest of the stream (CLOSED seq 1 with review_status=pending, gaze incident, finish)
for (const e of env.slice(phoneOpenedIdx + 1)) live.ingest(e);
show("after WS closed (seq 1, pending) + rest of stream");
// 4) post-exam review screen mounts: Operator.load() -> listIncidents -> replaceIncidents
live.replaceIncidents(restFinal);
show("post-exam review: REST list (A08: seq 1 confirmed)");
console.log("REST truth (A08):", JSON.stringify(restFinal.map((i) => `${i.rule_id}#${i.update_seq}:${i.review_status}:ev${i.evidence_ids.length}`)));

// Summary.tsx:87-88 (verbatim)
const reviewed = Object.values(summary.reviews_by_decision).reduce((a, b) => a + b, 0);
const pendingSummary = Math.max(0, summary.incidents_total - reviewed);
console.log(
  `Summary.tsx: incidents_total=${summary.incidents_total} reviews_by_decision=${JSON.stringify(summary.reviews_by_decision)} ->`,
  `reviewed=${reviewed}, row "Ожидает проверки"=${pendingSummary}, banner "Не проверено эпизодов" shown=${pendingSummary > 0}`,
  `(A08 says pending=${summary.reviews_by_decision.pending ?? 0})`,
);

// Variant B: REST first (renderer reload / console opened after the review -> App.resync or Operator.load),
// then the next stream change for the same incident (CLOSED, seq 1, review_status=pending from A05).
const live2 = new LiveStore();
live2.reset(session.session_id);
live2.replaceIncidents(restAfterReview);
const b0 = live2.incidents.get(iid);
const closedEnv = env.find((e) => e.message.type === "incident" && e.message.change.incident.incident_id === iid && e.message.change.change === "closed");
live2.ingest({ ...env[0] }); // hello
live2.ingest({ ...closedEnv, seq: env[0].seq + 1 });
const b1 = live2.incidents.get(iid);
console.log(`Variant B: REST seq ${b0.update_seq} review_status=${b0.review_status} ev=${b0.evidence_ids.length} -> after WS '${closedEnv.message.change.change}' seq ${b1.update_seq}: review_status=${b1.review_status} ev=${b1.evidence_ids.length}`);
