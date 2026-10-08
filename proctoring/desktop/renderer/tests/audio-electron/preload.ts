import "../../../preload/src/class-audio";
import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("audioFixture", {
  subscribe(callback: (event: unknown) => void) {
    const listener = (_event: unknown, payload: unknown) => callback(payload);
    ipcRenderer.on("fixture:events", listener);
    return () => ipcRenderer.removeListener("fixture:events", listener);
  },
});
