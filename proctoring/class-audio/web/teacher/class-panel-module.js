// @ts-check
// Plug-in for the class panel (T02, proctoring/class-panel): fills the student card slot "audio".
// Load as a module AFTER the panel created window.QorgauClassPanel (or push into window.QorgauClassPanelModules).
// One shared controller → one active audio session for the whole panel.
import { TeacherAudio } from "./teacher-audio.js";
import { TeacherSignaling } from "./teacher-signaling.js";
import { mountAudioPanel } from "./audio-panel.js";

/** @type {{controller: TeacherAudio, sig: TeacherSignaling} | null} */
let shared = null;

function ensureShared() {
  if (shared) return shared;
  const audio = document.createElement("audio");
  audio.autoplay = true;
  audio.hidden = true;
  document.body.append(audio);
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("./audio.css", import.meta.url).href;
  document.head.append(link);
  const sig = new TeacherSignaling();
  sig.connect();
  shared = { controller: new TeacherAudio({ signaling: sig, audioElement: audio }), sig };
  return shared;
}

export const audioModule = {
  id: "t05-audio",
  slot: "audio",
  title: "Аудиосвязь",
  /** @param {HTMLElement} el @param {any} ctx */
  mount(el, ctx) {
    if (ctx.mode !== "real") {
      el.textContent = "В DEMO-режиме аудиосвязь недоступна: нужен сервер класса и приложение студента.";
      return;
    }
    const { controller } = ensureShared();
    const labelOf = (/** @type {string} */ id) => {
      const s = id === ctx.studentId ? ctx.getStudent() : null;
      return s ? `${s.label ?? s.student_label ?? id}` : id;
    };
    const unmount = mountAudioPanel(el, { controller, studentId: ctx.studentId, labelOf, isOnline: () => ctx.getStudent()?.connected !== false });
    const unsub = ctx.subscribe(() => controller.nudge());
    return () => {
      unsub();
      unmount(); // closing the card does NOT end the call; "Завершить связь" does
    };
  },
};

const w = /** @type {any} */ (window);
if (w.QorgauClassPanel?.registerStudentModule) w.QorgauClassPanel.registerStudentModule(audioModule);
else (w.QorgauClassPanelModules = w.QorgauClassPanelModules ?? []).push(audioModule);
