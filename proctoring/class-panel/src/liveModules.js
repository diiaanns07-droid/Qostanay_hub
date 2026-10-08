// @ts-check
// Only the server's mounted capabilities enable UI extensions. Sources are fixed, never supplied URLs.
const MODULES = /** @type {Record<string, {url: string, label: string}>} */ ({
  history: { url: "/api/teacher/history/assets/register.js", label: "историю и клипы" },
  audio: { url: "/api/teacher/audio/assets/teacher/class-panel-module.js", label: "аудиосвязь" },
});

/** @param {string[]} names @param {HTMLElement} root */
export async function loadClassModules(names, root) {
  const entries = [...new Set(names)].map(name => MODULES[name]).filter(Boolean);
  const results = await Promise.allSettled(entries.map(entry => import(entry.url)));
  const failed = entries.filter((_, index) => results[index]?.status === "rejected");
  if (!failed.length) return;
  const notice = document.createElement("p");
  notice.className = "module-notice";
  notice.setAttribute("role", "status");
  notice.textContent = `Не удалось загрузить ${failed.map(entry => entry.label).join(", ")}. Обновите страницу; наблюдение за классом продолжает работать.`;
  root.querySelector(".workspace-toolbar")?.before(notice);
}
