import { contextBridge, ipcRenderer } from "electron";
import { LOCK_ACK_CHANNEL, type LockReceipt } from "../../shared/class-lock";

contextBridge.exposeInMainWorld("qorgauLock", Object.freeze({
  confirmApplied: (receipt: LockReceipt) => ipcRenderer.invoke(LOCK_ACK_CHANNEL, receipt),
}));
