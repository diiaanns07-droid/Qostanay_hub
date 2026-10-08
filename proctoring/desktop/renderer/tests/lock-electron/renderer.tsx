// Safe fixture: real React lock UI; no backend, native guard, camera or microphone.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { LockScreen } from "../../src/components/ClassOverlays";
import { useClassLock } from "../../src/lib/useClassLock";
import type { ClassState } from "../../src/lib/classState";
import "../../src/styles.css";

function Fixture() {
  const [state, update] = useState<ClassState | null>(null);
  (window as unknown as { fixtureUpdate: typeof update }).fixtureUpdate = update;
  const locked = useClassLock(state, "electron");
  return <>{locked && state && <LockScreen state={state} />}<main data-adal-app inert={locked || undefined} aria-hidden={locked || undefined}>
    <h1>FIXTURE — app overlay only</h1><button onClick={e => { e.currentTarget.textContent = "Clicked"; }}>Answer</button>
  </main></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
