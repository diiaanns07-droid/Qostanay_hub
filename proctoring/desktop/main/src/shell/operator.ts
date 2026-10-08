// Operator (teacher) PIN check in main (owner: A06). The renderer only ever sends a candidate PIN.
//
// Configuration (shell-only env, never inherited by the backend):
//   QORGAU_OPERATOR_PIN_HASH = "scrypt:<salt hex>:<hash hex>"   (make it: node main/tools/hash-pin.mjs)
//   QORGAU_SHELL_DEMO_OPERATOR=1   no hash configured -> one-time random PIN printed to the operator's
//                                   terminal (stderr) at startup; labelled DEMO. Off by default.
// Without either, operator unlock is unavailable (pause/resume/review stay locked).
import { randomInt, scryptSync, timingSafeEqual } from "node:crypto";
import { logger } from "../log";

const log = logger("operator");
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 32 } as const;

export type UnlockOutcome = "ok" | "wrong" | "rate_limited" | "not_configured";

export function hashPin(pin: string, salt: Buffer): string {
  const h = scryptSync(pin, salt, SCRYPT.keylen, { N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p });
  return `scrypt:${salt.toString("hex")}:${h.toString("hex")}`;
}

function parseHash(value: string): { salt: Buffer; hash: Buffer } | null {
  const m = /^scrypt:([0-9a-f]{16,128}):([0-9a-f]{64})$/.exec(value.trim());
  if (!m) return null;
  return { salt: Buffer.from(m[1]!, "hex"), hash: Buffer.from(m[2]!, "hex") };
}

export class OperatorAuth {
  private readonly stored: { salt: Buffer; hash: Buffer } | null;
  private failures = 0;
  private lockedUntil = 0;

  constructor(
    env: NodeJS.ProcessEnv,
    private readonly now: () => number = Date.now,
  ) {
    const configured = env.QORGAU_OPERATOR_PIN_HASH ? parseHash(env.QORGAU_OPERATOR_PIN_HASH) : null;
    if (env.QORGAU_OPERATOR_PIN_HASH && !configured) log.error("QORGAU_OPERATOR_PIN_HASH has an invalid format; operator unlock disabled");
    if (configured) {
      this.stored = configured;
    } else if (env.QORGAU_SHELL_DEMO_OPERATOR === "1") {
      const pin = String(randomInt(0, 1_000_000)).padStart(6, "0");
      const salt = Buffer.from(Array.from({ length: 16 }, () => randomInt(0, 256)));
      this.stored = parseHash(hashPin(pin, salt));
      process.stderr.write(`\n[Qorgau DEMO] one-time operator PIN for this run: ${pin}\n\n`);
    } else {
      this.stored = null;
    }
  }

  get configured(): boolean {
    return this.stored !== null;
  }

  /** Constant-time check with exponential lockout after 5 consecutive failures. */
  check(pin: string): UnlockOutcome {
    if (!this.stored) return "not_configured";
    const t = this.now();
    if (t < this.lockedUntil) return "rate_limited";
    const candidate = scryptSync(pin, this.stored.salt, SCRYPT.keylen, { N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p });
    if (timingSafeEqual(candidate, this.stored.hash)) {
      this.failures = 0;
      return "ok";
    }
    this.failures += 1;
    if (this.failures >= 5) {
      const lockMs = Math.min(15 * 60_000, 30_000 * 2 ** (this.failures - 5));
      this.lockedUntil = t + lockMs;
      log.warn(`operator PIN: ${this.failures} failures, locked for ${lockMs} ms`);
    }
    return "wrong";
  }
}
