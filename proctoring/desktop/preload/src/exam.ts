import { contextBridge, ipcRenderer } from "electron";
import { EXAM_CHANNEL, type ExamSurfaceBridge, type ExamSurfaceStatus } from "../../main/src/exam/channels";
const exam: ExamSurfaceBridge = {
  viewport: (bounds) => ipcRenderer.send(EXAM_CHANNEL.viewport, bounds),
  getStatus: () => ipcRenderer.invoke(EXAM_CHANNEL.getStatus),
  reload: () => ipcRenderer.send(EXAM_CHANNEL.reload),
  onStatus: (listener) => {
    if (typeof listener !== "function") throw new TypeError("listener required");
    const wrapped = (_event: unknown, status: ExamSurfaceStatus) => listener(status);
    ipcRenderer.on(EXAM_CHANNEL.status, wrapped);
    return () => ipcRenderer.removeListener(EXAM_CHANNEL.status, wrapped);
  },
};
contextBridge.exposeInMainWorld("qorgauExam", exam);
