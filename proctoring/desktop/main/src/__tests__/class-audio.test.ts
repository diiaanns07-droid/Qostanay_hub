import test from "node:test";
import assert from "node:assert/strict";
import { allowClassAudio } from "../class-audio";

test("audio capture requires exact trusted main frame, audio-only request and active lease", () => {
  const main = { mainFrame: { url: "qorgau://app/index.html" }, isDestroyed: () => false } as any;
  const details = { isMainFrame: true, requestingUrl: main.mainFrame.url, mediaTypes: ["audio"] };
  assert.equal(allowClassAudio(main, main, "media", details, null, true), true);
  assert.equal(allowClassAudio(main, main, "media", { ...details, mediaTypes: undefined, mediaType: "audio" }, null, true), true);
  for (const patch of [{ mediaTypes: ["audio", "video"] }, { mediaTypes: ["video"] }, { mediaTypes: [] },
    { isMainFrame: false }, { requestingUrl: "https://exam.example/" }, { requestingUrl: "qorgau://app/other.html" }]) {
    assert.equal(allowClassAudio(main, main, "media", { ...details, ...patch }, null, true), false);
  }
  assert.equal(allowClassAudio(main, main, "media", details, null, false), false);
  assert.equal(allowClassAudio({ ...main }, main, "media", details, null, true), false);
  assert.equal(allowClassAudio(main, main, "display-capture", details, null, true), false);
});
