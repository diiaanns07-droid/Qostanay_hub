import { contextBridge, ipcRenderer } from "electron";

// One fixed route; no tokens, ports, IPC event objects or generic network calls reach the renderer.
contextBridge.exposeInMainWorld("qorgauAudio", {
  send: (body: unknown) => ipcRenderer.invoke("qorgau:class-audio", body),
});
