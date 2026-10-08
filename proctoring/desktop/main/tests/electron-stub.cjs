// Electron API STUB for wiring checks of dist/main/main.cjs WITHOUT the Electron binary (owner: A06).
// It records calls; it does not render, focus, block keys or emulate any OS behaviour. Results
// obtained with it prove main-process wiring only and are never reported as Electron/OS checks.
"use strict";
const { EventEmitter } = require("node:events");
const path = require("node:path");
const Module = require("node:module");

const state = {
  handles: new Map(),
  ons: new Map(),
  protocolHandlers: new Map(),
  windows: [],
  shortcuts: new Map(),
  appListeners: new Map(),
  sent: [],
  calls: [],
  quitRequested: 0,
  exited: null,
};

class WebContents extends EventEmitter {
  constructor(win) {
    super();
    this.win = win;
    this.mainFrame = { url: "about:blank", parent: null };
    this.devtools = false;
    this.windowOpenHandler = null;
  }
  isDestroyed() {
    return false;
  }
  send(channel, ...args) {
    state.sent.push({ channel, args });
  }
  setWindowOpenHandler(fn) {
    this.windowOpenHandler = fn;
  }
  isDevToolsOpened() {
    return this.devtools;
  }
  closeDevTools() {
    this.devtools = false;
  }
}

class BrowserWindow extends EventEmitter {
  constructor(opts) {
    super();
    this.opts = opts;
    this.flags = {};
    this.destroyed = false;
    this.webContents = new WebContents(this);
    state.windows.push(this);
    state.appEmit("web-contents-created", {}, this.webContents);
  }
  static getAllWindows() {
    return state.windows.filter((w) => !w.destroyed);
  }
  loadURL(url) {
    this.webContents.mainFrame.url = url;
    state.calls.push(`loadURL ${url}`);
    return Promise.resolve();
  }
  isDestroyed() {
    return this.destroyed;
  }
  isMinimized() {
    return false;
  }
  isFocused() {
    return true;
  }
  restore() {}
  focus() {}
  show() {}
  moveTop() {}
  close() {
    const ev = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    this.emit("close", ev);
    if (!ev.defaultPrevented) {
      this.destroyed = true;
      this.emit("closed");
    }
    return !ev.defaultPrevented;
  }
}
for (const m of ["Kiosk", "FullScreen", "AlwaysOnTop", "ContentProtection", "Minimizable", "Closable", "Resizable", "Movable"]) {
  BrowserWindow.prototype[`set${m}`] = function (v, level) {
    this.flags[m] = v && level ? level : v;
  };
}

function makeSession() {
  const s = new EventEmitter();
  s.handlers = {};
  for (const m of ["setPermissionRequestHandler", "setPermissionCheckHandler", "setDevicePermissionHandler", "setDisplayMediaRequestHandler"]) {
    s[m] = (fn) => (s.handlers[m] = fn);
  }
  s.setSpellCheckerEnabled = () => {};
  s.webRequest = {
    onBeforeRequest: (filter, fn) => (s.handlers.onBeforeRequest = fn),
    onHeadersReceived: (filter, fn) => (s.handlers.onHeadersReceived = fn),
  };
  s.protocol = { handle: (scheme, fn) => state.protocolHandlers.set(scheme, fn) };
  return s;
}
const sessions = new Map();

let readyResolve;
const ready = new Promise((r) => (readyResolve = r));

const app = {
  enableSandbox: () => state.calls.push("enableSandbox"),
  requestSingleInstanceLock: () => true,
  getAppPath: () => path.resolve(__dirname, "..", ".."),
  isPackaged: false,
  getVersion: () => "0.1.0",
  getPath: () => require("node:os").tmpdir(),
  commandLine: { hasSwitch: () => false },
  on(ev, fn) {
    if (!state.appListeners.has(ev)) state.appListeners.set(ev, []);
    state.appListeners.get(ev).push(fn);
  },
  whenReady: () => ready,
  quit() {
    state.quitRequested += 1;
    const ev = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    state.appEmit("before-quit", ev);
    if (!ev.defaultPrevented) {
      state.appEmit("will-quit", {});
      state.exited = state.exited ?? 0;
    }
  },
  exit(code) {
    state.exited = code;
  },
};
state.appEmit = (ev, ...args) => {
  for (const fn of state.appListeners.get(ev) ?? []) fn(...args);
};

const electron = {
  app,
  BrowserWindow,
  protocol: { registerSchemesAsPrivileged: (s) => state.calls.push(`privileged ${s.map((x) => x.scheme).join(",")}`) },
  clipboard: { clear: () => state.calls.push("clipboard.clear") },
  dialog: { showSaveDialog: async () => ({ canceled: true }) },
  globalShortcut: {
    register: (acc, cb) => (state.shortcuts.set(acc, cb), true),
    isRegistered: (acc) => state.shortcuts.has(acc),
    unregister: (acc) => state.shortcuts.delete(acc),
    unregisterAll: () => state.shortcuts.clear(),
  },
  ipcMain: {
    handle: (ch, fn) => state.handles.set(ch, fn),
    on: (ch, fn) => state.ons.set(ch, fn),
  },
  Menu: { setApplicationMenu: (m) => state.calls.push(`menu ${m}`) },
  screen: new EventEmitter(),
  session: {
    fromPartition: (p) => {
      if (!sessions.has(p)) sessions.set(p, makeSession());
      return sessions.get(p);
    },
  },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "electron") return electron;
  return origLoad.call(this, request, parent, isMain);
};

module.exports = { state, electron, sessions, start: () => readyResolve() };
