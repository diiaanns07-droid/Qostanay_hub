// @ts-check
// One student card. Built once, then updated field by field (no re-render, fixed height → no layout jumps).
// Every status is TEXT + ICON SHAPE + colour. Nothing is shown that the data did not say: an unknown camera
// is "нет данных", never "работает"; "заблокирован" appears only when the data says locked=true.
import { ago, CAMERA_LABEL, displayName, EXAM_STATE_LABEL, ORIGIN_LABEL, ZONE_LABEL } from "../model.js";
import { h, setAttr, setText, svg, toggleClass } from "./dom.js";
import { ICON } from "./icons.js";

/** @typedef {NonNullable<ReturnType<import("../store.js").PanelStore["derived"]>>} Derived */

/**
 * @typedef {object} CardEls
 * @property {HTMLElement} root
 * @property {HTMLButtonElement} hit
 * @property {HTMLElement} zoneIcon
 * @property {HTMLElement} zoneText
 * @property {HTMLElement} link
 * @property {HTMLElement} source
 * @property {HTMLElement} linkIcon
 * @property {HTMLElement} linkText
 * @property {HTMLElement} media
 * @property {HTMLImageElement} img
 * @property {HTMLElement} mediaNote
 * @property {HTMLElement} name
 * @property {HTMLElement} computer
 * @property {HTMLElement} cam
 * @property {HTMLElement} camIcon
 * @property {HTMLElement} camText
 * @property {HTMLElement} exam
 * @property {HTMLElement} episodes
 * @property {HTMLElement} last
 * @property {HTMLElement} flags
 * @property {string} zone
 * @property {string} iconKey
 * @property {string} linkKey
 * @property {string} camKey
 */

/** @param {string} id @returns {CardEls} */
export function createCard(id) {
  const zoneIcon = h("span", { class: "zone-ico" });
  const zoneText = h("span", { class: "zone-text" });
  const linkIcon = h("span", { class: "link-ico" });
  const linkText = h("span", { class: "link-text" });
  const link = h("span", { class: "link" }, [linkIcon, linkText]);
  const source = h("span", { class: "source-label" });
  const img = /** @type {HTMLImageElement} */ (h("img", { alt: "", decoding: "async", draggable: "false", hidden: true }));
  const mediaNote = h("span", { class: "media-note" });
  const media = h("div", { class: "media" }, [img, mediaNote]);
  const name = h("span", { class: "name" });
  const computer = h("span", { class: "computer" });
  const camIcon = h("span", { class: "cam-ico" });
  const camText = h("span", {});
  const cam = h("span", { class: "cam" }, [camIcon, camText]);
  const exam = h("span", { class: "exam" });
  const episodes = h("span", { class: "episodes" });
  const last = h("span", { class: "last" });
  const flags = h("span", { class: "flags" });
  media.append(h("div", { class: "media-top" }, [
    h("div", { class: "zone" }, [zoneIcon, zoneText]),
    computer,
  ]));
  const hit = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "card-hit", "data-id": id, tabindex: "-1" }));
  const body = h("div", { class: "card-body", "aria-hidden": "true" }, [
    media,
    h("div", { class: "card-info" }, [
      h("div", { class: "card-id" }, [name, episodes]),
      h("div", { class: "card-line card-status" }, [link, exam]),
      h("div", { class: "card-line card-extra" }, [cam, source, flags]),
      h("div", { class: "card-line card-reason" }, [last]),
    ]),
  ]);
  const root = h("li", { class: "card", "data-id": id }, [body, hit]);
  return {
    root, hit, zoneIcon, zoneText, link, source, linkIcon, linkText, media, img, mediaNote, name, computer, cam, camIcon, camText,
    exam, episodes, last, flags, zone: "", iconKey: "", linkKey: "", camKey: "",
  };
}

/** @param {HTMLElement} holder @param {string} key @param {string} markup @param {CardEls} c @param {"iconKey"|"linkKey"|"camKey"} slot */
function setIcon(holder, key, markup, c, slot) {
  if (c[slot] === key) return;
  c[slot] = key;
  holder.replaceChildren(svg(markup));
}

/**
 * @param {CardEls} c
 * @param {Derived} x
 * @param {number} now
 * @param {{ showPreview: boolean, refreshPreview: boolean, demo: boolean }} o  showPreview = layout (density);
 *        refreshPreview = touch the image now (false for cards outside the viewport)
 */
export function updateCard(c, x, now, o) {
  const v = x.entry.view;
  const d = x.d;
  if (c.zone !== d.zone) {
    if (c.zone) c.root.classList.remove(`z-${c.zone}`);
    c.root.classList.add(`z-${d.zone}`);
    c.zone = d.zone;
  }
  setIcon(c.zoneIcon, d.zone, ICON[d.zone], c, "iconKey");
  setText(c.zoneText, { red: "Проверить", yellow: "Внимание", grey: "Нет данных", green: "Без замечаний" }[d.zone]);
  setAttr(c.zoneText, "title", ZONE_LABEL[d.zone]);

  const linkText = d.link === "online" ? "на связи" : d.link === "offline" ? `нет связи${d.ageMs !== null ? ` ${fmtAge(d.ageMs)}` : ""}` : "нет данных";
  setIcon(c.linkIcon, d.link, ICON[d.link], c, "linkKey");
  setText(c.linkText, linkText);
  setAttr(c.link, "data-link", d.link);

  // preview
  const p = x.entry.preview;
  // A preview may belong to a previous source; use its own provenance, never the current card's.
  const frameSource = p && p.origin !== "real" ? ORIGIN_LABEL[p.origin] : "";
  const fresh = p !== null && !d.stale && v.camera === "ok";
  toggleClass(c.root, "no-preview", !o.showPreview);
  if (o.showPreview && o.refreshPreview) {
    if (p && fresh) {
      if (c.img.getAttribute("src") !== p.url) c.img.src = p.url;
      c.img.hidden = false;
      toggleClass(c.media, "stale", false);
      setText(c.mediaNote, frameSource);
    } else if (p) {
      // keep the last picture visible but clearly marked as old — never as current
      if (c.img.getAttribute("src") !== p.url) c.img.src = p.url;
      c.img.hidden = false;
      toggleClass(c.media, "stale", true);
      setText(c.mediaNote, [frameSource, `превью устарело${p.at ? ` · ${ago(p.at, now)}` : ""}`].filter(Boolean).join(" · "));
    } else {
      c.img.hidden = true;
      toggleClass(c.media, "stale", false);
      setText(c.mediaNote, v.examState === "running" ? "превью не получено" : "превью нет");
    }
  }

  setText(c.name, displayName(v));
  setText(c.source, ORIGIN_LABEL[v.origin]);
  c.source.hidden = v.origin === "real";
  setAttr(c.source, "data-origin", v.origin);
  setText(c.computer, v.computerName ?? "компьютер —");
  const camKey = v.camera === null ? "unknown" : v.camera === "ok" ? "ok" : "bad";
  setIcon(c.camIcon, camKey, v.camera === "ok" ? ICON.camera : v.camera === null ? ICON.unknown : ICON.cameraOff, c, "camKey");
  // an old camera state is not presented as the current one
  setText(c.camText, v.camera === null ? "камера: нет данных" : d.stale ? "камера: устарело" : CAMERA_LABEL[v.camera]);
  setAttr(c.cam, "data-cam", d.stale && v.camera !== null ? "stale" : camKey);
  c.cam.hidden = !d.stale && v.camera === "ok";
  setText(c.exam, v.examState ? EXAM_STATE_LABEL[v.examState] : "этап: нет данных");

  const total = v.incidentsTotal;
  const unrev = v.unreviewed;
  setText(
    c.episodes,
    total === null ? "— эпизодов" : `${total} эпиз.${unrev !== null && unrev > 0 ? ` · ${unrev} без решения` : ""}`,
  );
  setAttr(c.episodes, "data-unreviewed", unrev !== null && unrev > 0 ? "1" : null);
  setAttr(c.episodes, "title", total === null ? "Число эпизодов не получено" : `Эпизодов: ${total}. Без решения: ${unrev ?? "неизвестно"}`);
  const flags = [];
  if (v.locked === true) flags.push("экран заблокирован");
  if (v.micActive === true) flags.push("микрофон включён");
  setText(c.flags, flags.join(" · "));
  setText(c.last, (d.zone === "red" || d.zone === "yellow") && d.reasons[0] ? d.reasons[0] : v.lastEventAt === null ? "" : `Событие ${ago(v.lastEventAt, now)}`);

  toggleClass(c.root, "flash", x.flashing);
  toggleClass(c.root, "in-queue", x.inQueue);
  toggleClass(c.root, "stale", d.stale);

  const label = [
    displayName(v),
    v.computerName && v.computerName !== displayName(v) ? v.computerName : null,
    ZONE_LABEL[d.zone],
    d.zone === "grey" && d.reasons[0] ? d.reasons[0] : null,
    d.link === "unknown" ? "связь неизвестна" : linkText,
    v.camera === null ? "камера: нет данных" : d.stale ? "состояние камеры устарело" : CAMERA_LABEL[v.camera],
    total === null ? "эпизоды: нет данных" : `эпизодов ${total}${unrev !== null ? `, без решения ${unrev}` : ""}`,
    `последнее событие ${v.lastEventAt === null ? "нет" : ago(v.lastEventAt, now)}`,
    o.demo ? "демо-данные" : ORIGIN_LABEL[v.origin],
  ]
    .filter(Boolean)
    .join(". ");
  setAttr(c.hit, "aria-label", `Открыть карточку: ${label}`);
}

/** @param {number} ms */
export function fmtAge(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч`;
}
