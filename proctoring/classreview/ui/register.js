// @ts-check
// Registers the T03 module in the T02 class panel (slot "history"), whichever loads first.
// The panel page must load this file (<script type="module" src=".../register.js">) and allow
// media-src 'self' in its CSP — see proctoring/handoffs/T03/DEPENDENCIES.txt.
import { createReviewModule } from "./review-module.js";

const mod = createReviewModule();
const w = /** @type {any} */ (window);
if (w.QorgauClassPanel?.registerStudentModule) w.QorgauClassPanel.registerStudentModule(mod);
else (w.QorgauClassPanelModules ||= []).push(mod);
