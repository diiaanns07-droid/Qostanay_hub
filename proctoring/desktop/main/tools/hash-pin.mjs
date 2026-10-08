// Prints a QORGAU_OPERATOR_PIN_HASH value for a PIN read from stdin (owner: A06).
//   node main/tools/hash-pin.mjs        (type the PIN, press Enter; it is not echoed back)
// Same parameters as main/src/shell/operator.ts (scrypt N=16384 r=8 p=1, 32-byte key, 16-byte salt).
import { randomBytes, scryptSync } from "node:crypto";
import { createInterface } from "node:readline";

const rl = createInterface({ input: process.stdin });
rl.once("line", (line) => {
  rl.close();
  const pin = line.trim();
  if (!/^[0-9A-Za-z]{4,64}$/.test(pin)) {
    console.error("PIN must be 4..64 letters/digits");
    process.exit(2);
  }
  const salt = randomBytes(16);
  const hash = scryptSync(pin, salt, 32, { N: 16384, r: 8, p: 1 });
  console.log(`scrypt:${salt.toString("hex")}:${hash.toString("hex")}`);
});
