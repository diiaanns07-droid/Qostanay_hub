// @ts-check
// "Аудиосвязь" panel for ONE selected student (T05). Mounted by the standalone teacher page or by the class panel
// slot "audio" (T02). All panels share one TeacherAudio controller → one active session per teacher; a panel for
// another student shows who holds the line and offers nothing that could connect a second student.
// Nodes are created once and updated in place (stats arrive twice a second; focus must not jump).

/** @typedef {import("./teacher-audio.js").TeacherAudio} TeacherAudio */
/** @typedef {import("./teacher-audio.js").TeacherAudioState} TeacherAudioState */

/**
 * @param {string} tag
 * @param {Record<string, string>} [attrs]
 * @param {(Node|string)[]} [children]
 */
function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.append(...children);
  return el;
}

/** @param {HTMLElement} el @param {string} text */
function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

const RECOVERABLE = new Set(["student_disconnected", "network_lost", "teacher_disconnected", "accept_timeout", "connect_timeout"]);

/**
 * @param {HTMLElement} root
 * @param {{controller: TeacherAudio, studentId: string, labelOf: (id: string) => string, isOnline?: () => boolean}} o
 * @returns {() => void} unmount
 */
export function mountAudioPanel(root, o) {
  const { controller: c, studentId } = o;
  const who = h("span", { class: "qa-who" });
  const busy = h("p", { class: "qa-busy", role: "status" });
  const status = h("p", { class: "qa-status", role: "status", "aria-live": "polite" });
  const bListen = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "qa-btn" }, ["Слушать"]));
  const bTalk = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "qa-btn" }, ["Говорить"]));
  const bStop = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "qa-btn qa-stop" }, ["Завершить связь"]));
  const actions = h("div", { class: "qa-actions" }, [bListen, bTalk, bStop]);
  const liListen = h("li");
  const meterFill = h("span", { class: "qa-meter-fill" });
  const meter = h("span", { class: "qa-meter", "aria-hidden": "true" }, [meterFill]);
  const liTalk = h("li");
  const liStudent = h("li");
  const live = h("ul", { class: "qa-live" }, [liListen, liTalk, liStudent]);
  const bPlay = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "qa-btn" }, ["Включить звук в браузере"]));
  const problem = h("div", { class: "qa-problem", role: "alert" });
  const bAgain = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "qa-btn" }, ["Подключиться снова"]));
  const note = h("p", { class: "qa-note" }, ["Звук не записывается. Студент видит, когда включён его микрофон. Закрытие карточки завершает связь."]);
  const box = h("section", { class: "qa-audio", "aria-label": "Аудиосвязь" }, [
    h("div", { class: "qa-head" }, [h("h3", {}, ["Аудиосвязь"]), who]),
    busy,
    status,
    actions,
    live,
    bPlay,
    problem,
    bAgain,
    note,
  ]);
  root.replaceChildren(box);

  /** @type {TeacherAudioState} */
  let s = c.state;
  const mine = () => s.studentId === studentId && s.phase !== "idle";
  const active = () => mine() && c.busy;

  bListen.addEventListener("click", () => (active() ? void c.setListen(!s.listen) : void c.start(studentId, { listen: true, talk: false })));
  bTalk.addEventListener("click", () => (active() ? void c.setTalk(!s.talk) : void c.start(studentId, { listen: false, talk: true })));
  bStop.addEventListener("click", () => c.stop());
  bPlay.addEventListener("click", () => void c.resumePlayback());
  bAgain.addEventListener("click", () => void c.start(studentId, { listen: s.listen || !s.talk, talk: s.talk }));

  const render = (/** @type {TeacherAudioState} */ next) => {
    s = next;
    const online = o.isOnline ? o.isOnline() : true;
    const busyOther = c.busy && s.studentId !== studentId;
    setText(who, o.labelOf(studentId));
    busy.hidden = !busyOther;
    if (busyOther) setText(busy, `Сейчас идёт аудиосвязь с «${o.labelOf(s.studentId ?? "")}». Чтобы подключиться к этому студенту, сначала завершите её.`);
    for (const el of [status, actions, live, bPlay, problem, bAgain, note]) el.hidden = busyOther;
    if (busyOther) return;

    const isMine = mine();
    const act = active();
    status.className = `qa-status qa-${isMine ? s.phase : "idle"}`;
    setText(status, statusText(s, isMine, online));

    const base = !s.signalingUp || (!act && !online);
    const listenOn = act && s.listen;
    const talkOn = act && s.talk;
    bListen.disabled = base;
    bTalk.disabled = base;
    bStop.disabled = !act;
    bListen.setAttribute("aria-pressed", String(listenOn));
    bTalk.setAttribute("aria-pressed", String(talkOn));
    bListen.classList.toggle("qa-on", listenOn);
    bTalk.classList.toggle("qa-on", talkOn);
    setText(bListen, listenOn ? "Не слушать" : "Слушать");
    setText(bTalk, talkOn ? "Не говорить" : "Говорить");

    live.hidden = !(isMine && ["connecting", "connected", "reconnecting"].includes(s.phase));
    liListen.className = s.listening ? "qa-yes" : "qa-no";
    setText(liListen, s.listening ? "Слушаю: звук от студента идёт " : s.listen ? "Звука от студента пока нет" : "Прослушивание выключено");
    if (s.listening) liListen.append(meter);
    meterFill.style.width = `${Math.round(Math.min(1, s.inLevel * 4) * 100)}%`;
    liTalk.className = s.speaking ? "qa-yes" : "qa-no";
    setText(liTalk, s.speaking ? "Говорю: звук уходит студенту" : s.talk ? "Мой звук пока не уходит" : "Мой микрофон выключен");
    const sm = s.studentMedia;
    liStudent.className = sm?.indicator_shown ? "qa-yes" : "qa-no";
    setText(
      liStudent,
      sm == null
        ? "Состояние у студента: нет данных"
        : `У студента: индикатор ${sm.indicator_shown ? "показан" : "не показан"}, микрофон ${sm.mic_live ? "включён" : "выключен"}${s.talk ? `, мой голос ${sm.teacher_audio_playing ? "звучит" : "не звучит"}` : ""}`,
    );

    bPlay.hidden = !(isMine && s.needsPlayClick);
    const showProblem = !!s.problem && (isMine || s.phase === "idle" || s.phase === "error");
    problem.hidden = !showProblem;
    if (showProblem && s.problem) setText(problem, `${s.problem.message_ru}${s.problem.hint_ru ? " " + s.problem.hint_ru : ""}`);
    bAgain.hidden = !(isMine && (s.phase === "ended" || s.phase === "rejected") && RECOVERABLE.has(s.reason ?? ""));
    bAgain.disabled = !online || !s.signalingUp;
    setText(bAgain, online ? "Подключиться снова" : "Подключиться снова (студент не на связи)");
  };

  const unsub = c.subscribe(render);
  return () => {
    unsub();
    root.replaceChildren();
  };
}

/** @param {TeacherAudioState} s @param {boolean} mine @param {boolean} online */
function statusText(s, mine, online) {
  if (!s.signalingUp) return "Нет связи с сервером класса — аудиосвязь недоступна.";
  if (!mine) return online ? "Связь не установлена." : "Студент не на связи.";
  switch (s.phase) {
    case "requesting":
      return "Запрос отправлен — ждём подтверждения студента…";
    case "connecting":
      return "Студент подтвердил — устанавливаем соединение…";
    case "connected":
      if (s.listen && s.listening) return s.talk && s.speaking ? "Связь в обе стороны: слушаю и говорю." : "Слушаю студента.";
      if (s.talk && s.speaking && !s.listen) return "Говорю — звук уходит студенту.";
      return "Соединение есть, ждём звук…";
    case "reconnecting":
      return "Связь прервалась — пытаемся восстановить…";
    case "ended":
      return `Связь завершена: ${s.reasonText || "без причины"}.`;
    case "rejected":
      return `Связь не установлена: ${s.reasonText || "студент не подтвердил"}.`;
    case "error":
      return "Связь не начата.";
    default:
      return "Связь не установлена.";
  }
}
