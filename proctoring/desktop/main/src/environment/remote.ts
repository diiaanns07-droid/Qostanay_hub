import { execFile } from "node:child_process";
import { resolveHelperCommand, type HelperCommand } from "./native";

export const REMOTE_NAMES = new Set(["rustdesk", "anydesk", "teamviewer", "tv_w32", "tv_x64",
  "ultraviewer_desktop", "remoting_host", "parsecd", "winvnc", "tvnserver", "rutserv"]);
export interface RemoteSnapshot { available: boolean; processes: string[]; remoteSession: boolean }
export const unavailable = (): RemoteSnapshot => ({ available: false, processes: [], remoteSession: false });

export function parseRemoteSnapshot(text: string): RemoteSnapshot {
  if (text.length > 4096) return unavailable();
  try {
    const raw = JSON.parse(text);
    if (raw.type !== "environment" || !Array.isArray(raw.processes) || raw.processes.length > 32 ||
        typeof raw.remote_session !== "boolean") return unavailable();
    const processes: string[] = [];
    for (const name of raw.processes) {
      if (typeof name !== "string" || !REMOTE_NAMES.has(name.toLowerCase().replace(/\.exe$/, ""))) return unavailable();
      processes.push(name.toLowerCase().replace(/\.exe$/, "") + ".exe");
    }
    return { available: true, processes: [...new Set(processes)].sort(), remoteSession: raw.remote_session };
  } catch { return unavailable(); }
}

/** Short-lived read-only helper mode; never installs the enforcement hook. */
export function checkRemoteEnvironment(cmd: HelperCommand, platform: NodeJS.Platform = process.platform): Promise<RemoteSnapshot> {
  if (platform !== "win32" && !cmd.prefixArgs) return Promise.resolve(unavailable());
  const resolved = resolveHelperCommand(cmd, platform);
  return new Promise(resolve => {
    execFile(resolved.command, [...(resolved.prefixArgs ?? []), "--environment-check"],
      { windowsHide: true, timeout: 3000, maxBuffer: 4096 },
      (error, stdout) => resolve(error ? unavailable() : parseRemoteSnapshot(stdout.trim())));
  });
}

export function remoteNames(snapshot: RemoteSnapshot): string[] {
  return [...snapshot.processes, ...(snapshot.remoteSession ? ["RDP"] : [])];
}
