// Capability matrix blocked / detected_only / unsupported / unverified (owner: A06). Pure logic.
//
// A status other than "unverified" needs evidence from THIS machine:
//   1. runtime self-test (environment/probe.ts) run by this shell on this OS, this run; or
//   2. a manual controlled-test record in desktop/native/VERIFICATION.json whose platform AND
//      os release match exactly (and whose native mechanism is actually available now).
// Calling an API is never evidence. OS-level items without evidence stay "unverified" on Windows
// and "unsupported" elsewhere (the OS mechanisms are implemented for Windows only).
import type {
  CapabilityStatus,
  EnvironmentAction,
  EnvironmentCapabilities,
  EnvironmentCapability,
} from "@contracts/qorgau-v1.generated";

export type ProbeCheck =
  | "key_ctrl_c"
  | "key_ctrl_v"
  | "key_shift_insert"
  | "key_ctrl_x"
  | "key_ctrl_tab"
  | "key_alt_f4"
  | "key_devtools"
  | "key_reload"
  | "window_close"
  | "window_open"
  | "navigation"
  | "devtools";

export interface ProbeOutcome {
  status: "pass" | "fail" | "inconclusive" | "not_run";
  detail: string;
}

export type ProbeResults = Partial<Record<ProbeCheck, ProbeOutcome>>;

export interface VerificationRecord {
  action: EnvironmentAction;
  /** What the controlled test observed on that machine. */
  status: Exclude<CapabilityStatus, "unverified">;
  mechanism: string;
  platform: NodeJS.Platform;
  /** exact os.release(), e.g. "10.0.22631" */
  os_release: string;
  /** e.g. "Windows 11 Pro 23H2 22631.4317, standard user, helper enforce" */
  verified_on: string;
  date: string;
  note_ru?: string;
}

export interface NativeHelperInfo {
  /** helper binary found and its self-check handshake succeeded */
  available: boolean;
  /** helper will run with --enforce (explicit opt-in, controlled test) */
  enforce: boolean;
  detail: string;
}

export interface PlatformInfo {
  platform: NodeJS.Platform;
  release: string;
  arch: string;
  /** human string for EnvironmentCapabilities.platform (<= 128 chars) */
  label: string;
}

/** Items reported in the matrix (informational actions like exam_mode_engaged are not capabilities). */
export const MATRIX_ACTIONS: readonly EnvironmentAction[] = [
  "shortcut_ctrl_c",
  "shortcut_ctrl_v",
  "shortcut_ctrl_x",
  "shortcut_ctrl_tab",
  "shortcut_alt_f4",
  "shortcut_alt_tab",
  "shortcut_win",
  "shortcut_print_screen",
  "focus_lost",
  "foreign_window_foreground",
  "new_window_blocked",
  "navigation_blocked",
  "devtools_blocked",
  "clipboard_blocked",
  "display_changed",
];

interface InAppRule {
  kind: "in_app";
  mechanism: string;
  checks: ProbeCheck[];
  note_ru: string;
}
interface OsRule {
  kind: "os";
  mechanism: string;
  note_ru: string;
}
interface DetectRule {
  kind: "detect";
  mechanism: string;
  note_ru: string;
}

const RULES: Record<string, InAppRule | OsRule | DetectRule> = {
  shortcut_ctrl_c: {
    kind: "in_app",
    mechanism: "electron.before_input_event",
    checks: ["key_ctrl_c"],
    note_ru: "Блокируется только внутри окна экзамена (включая раскладку ru/kk по физической клавише).",
  },
  shortcut_ctrl_v: {
    kind: "in_app",
    mechanism: "electron.before_input_event",
    checks: ["key_ctrl_v", "key_shift_insert"],
    note_ru: "Ctrl+V и Shift+Insert блокируются внутри окна; буфер обмена очищается при входе и выходе.",
  },
  shortcut_ctrl_x: {
    kind: "in_app",
    mechanism: "electron.before_input_event",
    checks: ["key_ctrl_x"],
    note_ru: "Блокируется только внутри окна экзамена.",
  },
  shortcut_ctrl_tab: {
    kind: "in_app",
    mechanism: "electron.before_input_event",
    checks: ["key_ctrl_tab"],
    note_ru: "В оболочке одно окно без вкладок; сочетание дополнительно подавляется.",
  },
  shortcut_alt_f4: {
    kind: "in_app",
    mechanism: "electron.window_close_event",
    checks: ["window_close", "key_alt_f4"],
    note_ru: "Закрытие окна экзамена отменяется; системное завершение (выход из сеанса ОС) не блокируется.",
  },
  new_window_blocked: {
    kind: "in_app",
    mechanism: "electron.window_open_handler",
    checks: ["window_open"],
    note_ru: "window.open и новые окна запрещены всегда.",
  },
  navigation_blocked: {
    kind: "in_app",
    mechanism: "electron.will_navigate",
    checks: ["navigation", "key_reload"],
    note_ru: "Переход на любые адреса вне приложения запрещён всегда.",
  },
  devtools_blocked: {
    kind: "in_app",
    mechanism: "electron.devtools_disabled",
    checks: ["devtools", "key_devtools"],
    note_ru: "Инструменты разработчика отключены в окне экзамена.",
  },
  clipboard_blocked: {
    kind: "in_app",
    mechanism: "electron.clipboard_policy",
    checks: ["key_ctrl_c", "key_ctrl_v", "key_ctrl_x", "key_shift_insert"],
    note_ru: "Копирование/вставка внутри окна блокируются; другие приложения буфер не теряют до входа в экзамен.",
  },
  shortcut_alt_tab: {
    kind: "os",
    mechanism: "native.ll_keyboard_hook",
    note_ru: "Без проверенного системного помощника Alt+Tab не блокируется; уход фиксируется как потеря фокуса.",
  },
  shortcut_win: {
    kind: "os",
    mechanism: "native.ll_keyboard_hook",
    note_ru: "Клавиша Win обрабатывается ОС; блокировка только через проверенный помощник Windows.",
  },
  shortcut_print_screen: {
    kind: "os",
    mechanism: "native.ll_keyboard_hook",
    note_ru: "Снимок экрана: окно экзамена исключено из захвата (content protection), блокировка клавиши — только помощником.",
  },
  foreign_window_foreground: {
    kind: "os",
    mechanism: "native.foreground_watch",
    note_ru: "Имя процесса (без заголовка окна) фиксируется помощником Windows; переход не предотвращается.",
  },
  focus_lost: {
    kind: "detect",
    mechanism: "electron.browser_window_blur",
    note_ru: "Потеря фокуса только фиксируется и не считается блокировкой.",
  },
  display_changed: {
    kind: "detect",
    mechanism: "electron.screen_events",
    note_ru: "Подключение/отключение мониторов только фиксируется.",
  },
};

export function ruleFor(action: EnvironmentAction): InAppRule | OsRule | DetectRule | undefined {
  return RULES[action];
}

function recordFor(
  action: EnvironmentAction,
  records: readonly VerificationRecord[],
  p: PlatformInfo,
  helper: NativeHelperInfo,
): VerificationRecord | undefined {
  return records.find(
    (r) =>
      r.action === action &&
      r.platform === p.platform &&
      r.os_release === p.release &&
      (!r.mechanism.startsWith("native.") || (helper.available && (r.status !== "blocked" || helper.enforce))),
  );
}

export function buildCapabilities(input: {
  platform: PlatformInfo;
  shellVersion: string;
  probe: ProbeResults;
  probeRanAt: string | null;
  records: readonly VerificationRecord[];
  helper: NativeHelperInfo;
  reportedAt?: Date;
}): EnvironmentCapabilities {
  const { platform: p, probe, records, helper } = input;
  const items: EnvironmentCapability[] = [];
  for (const action of MATRIX_ACTIONS) {
    const rule = RULES[action]!;
    let status: CapabilityStatus = "unverified";
    let verifiedOn: string | null = null;
    let note = rule.note_ru;
    let mechanism = rule.mechanism;

    if (rule.kind === "in_app") {
      const outcomes = rule.checks.map((c) => probe[c] ?? { status: "not_run" as const, detail: "" });
      if (outcomes.every((o) => o.status === "pass")) {
        status = "blocked";
        verifiedOn = `${p.label}; runtime self-test ${input.probeRanAt ?? ""}`.slice(0, 128);
      } else if (outcomes.some((o) => o.status === "fail")) {
        status = "unsupported";
        const failed = rule.checks.filter((_, i) => outcomes[i]!.status === "fail");
        note = `Самопроверка не пройдена (${failed.join(", ")}): блокировка не подтверждена.`;
        verifiedOn = `${p.label}; runtime self-test ${input.probeRanAt ?? ""}`.slice(0, 128);
      }
    }
    const rec = recordFor(action, records, p, helper);
    if (status === "unverified" && rec) {
      status = rec.status;
      verifiedOn = rec.verified_on.slice(0, 128);
      mechanism = rec.mechanism;
      if (rec.note_ru) note = rec.note_ru;
    }
    if (status === "unverified" && rule.kind === "os" && p.platform !== "win32") {
      status = "unsupported";
      note = "Системный механизм реализован только для Windows.";
    }
    if (status === "unverified" && rule.kind === "os" && p.platform === "win32" && !helper.available) {
      note = `${rule.note_ru} Помощник: ${helper.detail}`.slice(0, 300);
    }
    items.push({ action, status, mechanism: mechanism.slice(0, 64), verified_on: verifiedOn, note_ru: note.slice(0, 300) });
  }
  return {
    reported_at: (input.reportedAt ?? new Date()).toISOString(),
    platform: p.label.slice(0, 128),
    shell_version: input.shellVersion.slice(0, 32),
    exam_mode_supported: true,
    items,
  };
}

export function summarize(caps: EnvironmentCapabilities): Record<CapabilityStatus, number> {
  const out: Record<CapabilityStatus, number> = { blocked: 0, detected_only: 0, unsupported: 0, unverified: 0 };
  for (const i of caps.items) out[i.status] += 1;
  return out;
}

/** Parse VERIFICATION.json defensively: invalid entries are ignored (and counted). */
export function parseVerificationRecords(text: string): { records: VerificationRecord[]; invalid: number } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { records: [], invalid: 1 };
  }
  const list = (raw as { records?: unknown })?.records;
  if (!Array.isArray(list)) return { records: [], invalid: 1 };
  const records: VerificationRecord[] = [];
  let invalid = 0;
  for (const r of list as Record<string, unknown>[]) {
    const okStatus = r?.status === "blocked" || r?.status === "detected_only" || r?.status === "unsupported";
    if (
      okStatus &&
      typeof r.action === "string" &&
      (MATRIX_ACTIONS as readonly string[]).includes(r.action) &&
      typeof r.mechanism === "string" &&
      typeof r.platform === "string" &&
      typeof r.os_release === "string" &&
      r.os_release.length > 0 &&
      typeof r.verified_on === "string" &&
      r.verified_on.length > 0 &&
      typeof r.date === "string"
    ) {
      records.push(r as unknown as VerificationRecord);
    } else {
      invalid += 1;
    }
  }
  return { records, invalid };
}
