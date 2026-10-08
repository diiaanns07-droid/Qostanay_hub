import { execFile } from "node:child_process";
import type { EnvironmentCapabilities } from "@contracts/qorgau-v1.generated";
import { resolveHelperCommand, type HelperCommand } from "./native";

export const VM_PLATFORMS = ["VMware", "VirtualBox", "Hyper-V", "QEMU/KVM", "Parallels", "Xen"] as const;
export interface VmSnapshot {
  state: "detected" | "not_detected" | "unknown";
  platform: typeof VM_PLATFORMS[number] | null;
}
export const unknownVm = (): VmSnapshot => ({ state: "unknown", platform: null });

export function parseVmSnapshot(text: string): VmSnapshot {
  try {
    if (text.length > 4096) return unknownVm();
    const v = JSON.parse(text);
    if (v.type !== "vm") return unknownVm();
    if (v.state === "detected" && VM_PLATFORMS.includes(v.platform)) return { state: v.state, platform: v.platform };
    if ((v.state === "not_detected" || v.state === "unknown") && v.platform === null) return { state: v.state, platform: null };
  } catch { /* unavailable, never clean */ }
  return unknownVm();
}

export function checkVm(cmd: HelperCommand, platform = process.platform): Promise<VmSnapshot> {
  if (platform !== "win32" && !cmd.prefixArgs) return Promise.resolve(unknownVm());
  const resolved = resolveHelperCommand(cmd, platform);
  return new Promise(resolve => {
    execFile(resolved.command, [...(resolved.prefixArgs ?? []), "--vm-check"],
      { windowsHide: true, timeout: 10_000, maxBuffer: 4096 },
      (error, stdout) => resolve(error ? unknownVm() : parseVmSnapshot(stdout.trim())));
  });
}

export function withVmCheck(caps: EnvironmentCapabilities, vm: VmSnapshot): EnvironmentCapabilities {
  return { ...caps, items: [...caps.items.filter(i => !i.mechanism.startsWith("native.vm_check.")), {
    action: "exam_mode_engaged", status: vm.state === "unknown" ? "unverified" : "detected_only",
    mechanism: `native.vm_check.${vm.state}`, verified_on: null,
    note_ru: vm.state === "detected" ? `Экзамен запущен в виртуальной машине (${vm.platform})` :
      vm.state === "unknown" ? "Не удалось определить, запущен ли экзамен в виртуальной машине (WMI: unknown)" :
        "Признаки виртуальной машины не обнаружены",
  }] };
}
