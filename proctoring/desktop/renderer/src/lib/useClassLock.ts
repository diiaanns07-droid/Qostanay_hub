import { useEffect, useState } from "react";
import { receiptFor } from "../../../shared/class-lock";
import type { ClassState } from "./classState";

/** Render requested intent; report success only after a browser paint and DOM checks. */
export function useClassLock(state: ClassState | null, transport: string): boolean {
  const [, refresh] = useState(0);
  const request = state?.lock_request;
  const live = !!request && Date.parse(request.expires_at) > Date.now();
  const locked = live ? request.locked : !!state?.locked || (!!state?.lock_requested && !state.lock_confirmed);
  useEffect(() => {
    if (!request || !live) return;
    let cancelled = false;
    let raf = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const expires = setTimeout(() => refresh(v => v + 1), Math.max(0, Date.parse(request.expires_at) - Date.now()) + 1);
    const report = async () => {
      if (cancelled || transport !== "electron" || !window.qorgauLock || Date.parse(request.expires_at) <= Date.now()) return;
      const app = document.querySelector<HTMLElement>("[data-adal-app]");
      const overlay = document.querySelector<HTMLElement>("[data-adal-lock]");
      const applied = !!app && document.visibilityState === "visible" && (request.locked
        ? !!overlay && app.inert && overlay.dataset.lockToken === request.request_token
          && overlay.querySelector("[data-lock-reason]")?.textContent === request.reason_ru
        : !overlay && !app.inert);
      try {
        const result = await window.qorgauLock.confirmApplied(receiptFor(request, applied));
        if (!result.accepted && !cancelled) retry = setTimeout(() => void report(), 200);
      } catch {
        if (!cancelled) retry = setTimeout(() => void report(), 200);
      }
    };
    // First frame commits layout; the second runs after the preceding paint.
    raf = requestAnimationFrame(() => { raf = requestAnimationFrame(() => void report()); });
    return () => { cancelled = true; cancelAnimationFrame(raf); clearTimeout(expires); clearTimeout(retry); };
  }, [request?.request_token, live, transport]);
  return locked;
}
