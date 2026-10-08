import type { EnvironmentAction } from "@contracts/qorgau-v1.generated";

export const CONTENT_ACTIONS = {
  print: { action: "clipboard_blocked", label: "Печать (window.print)" },
  context_menu: { action: "clipboard_blocked", label: "Контекстное меню" },
  selection: { action: "clipboard_blocked", label: "Выделение текста" },
  drag: { action: "clipboard_blocked", label: "Перетаскивание текста" },
  zoom: { action: "navigation_blocked", label: "Масштаб страницы" },
  new_window: { action: "new_window_blocked", label: "Новое окно сайта" },
  navigation: { action: "navigation_blocked", label: "Адрес вне белого списка" },
  download: { action: "clipboard_blocked", label: "Сохранение файла" },
} satisfies Record<string, { action: EnvironmentAction; label: string }>;
export type ContentAction = keyof typeof CONTENT_ACTIONS;
export function isContentAction(id: unknown): id is ContentAction {
  return typeof id === "string" && Object.hasOwn(CONTENT_ACTIONS, id);
}

// Static sandboxed preload, materialized by main. No file/network/exam/auth bridge.
// Only fixed operation ids travel to main; selection/text/URLs are never sent.
export const CONTENT_PRELOAD = String.raw`
const { contextBridge, ipcRenderer } = require('electron');
const blocked = id => ipcRenderer.sendSync('qorgau:content-policy', id) === true;
for (const [name, id] of [['contextmenu','context_menu'], ['selectstart','selection'], ['dragstart','drag']]) {
  window.addEventListener(name, event => {
    if (blocked(id)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
}
window.addEventListener('wheel', event => {
  if (event.ctrlKey && blocked('zoom')) { event.preventDefault(); event.stopImmediatePropagation(); }
}, {capture:true, passive:false});
// This deliberately exposes only a boolean operation gate, not ipcRenderer or the exam bridge.
contextBridge.exposeInMainWorld('__adalContentPolicy', Object.freeze({blocked}));
contextBridge.executeInMainWorld({func: () => {
  const original = window.print.bind(window);
  const gate = window.__adalContentPolicy.blocked;
  Object.defineProperty(window, 'print', {configurable:false, writable:false, value: function () {
    if (!gate('print')) original();
  }});
}});
`;
