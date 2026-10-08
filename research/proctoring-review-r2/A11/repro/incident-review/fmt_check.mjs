import { build } from "esbuild";
import { pathToFileURL } from "node:url";
const W = new URL(".", import.meta.url).pathname;
await build({ entryPoints: [`${W}ui/proctoring/desktop/renderer/src/lib/format.ts`], bundle: true, format: "esm", platform: "node",
  outfile: `${W}build/format.mjs`, alias: { "@contracts": `${W}ui/proctoring/contracts/ts` }, logLevel: "error" });
const f = await import(pathToFileURL(`${W}build/format.mjs`).href);
const s = JSON.parse((await import("node:fs")).readFileSync(`${W}out_backend/session.json`, "utf8"));
const inc = JSON.parse((await import("node:fs")).readFileSync(`${W}out_backend/rest_incidents_final.json`, "utf8")).find(i => i.rule_id === "phone_visible");
const d = inc.t_start_ms - s.exam_started_t_ms;
console.log(`exam_started_t_ms=${s.exam_started_t_ms} phone t_start_ms=${inc.t_start_ms} delta=${d.toFixed(1)} ms -> sessionT(delta)="${f.sessionT(d)}"; sessionT(-250)="${f.sessionT(-250)}"; sessionT(-1500)="${f.sessionT(-1500)}"`);
console.log("first observation id:", inc.observation_ids[0]);
