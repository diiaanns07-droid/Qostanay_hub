// Mounts t02-module.js like the T02 drawer does (fake ctx, real DEV API). Used by tests/e2e.cjs only.
import { createCommandsModule } from "../t02-module.js";

const q = new URLSearchParams(location.search);
const mod = createCommandsModule({ getTeacher: () => q.get("teacher") || "t-aigerim", pollMs: 300 });
const ctx = { studentId: q.get("student") || "sim-01", mode: q.get("mode") || "real", getStudent: () => null, subscribe: () => () => {} };
mod.mount(/** @type {HTMLElement} */ (document.getElementById("slot")), ctx);
