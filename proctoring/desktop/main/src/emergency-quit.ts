/** Main-thread emergency exit. A separate process must handle a frozen main event loop. */
export class EmergencyQuit {
  private pending: Promise<void> | null = null;
  private cancelHard: (() => void) | null = null;
  constructor(private readonly deps: {
    release: readonly (() => void)[];
    cleanup: readonly (() => Promise<unknown>)[];
    quit: () => void;
    forceExit: () => void;
    report: (error: unknown) => void;
    schedule?: (callback: () => void, milliseconds: number) => () => void;
  }) {}

  request(): Promise<void> {
    if (this.pending) return this.pending;
    const schedule = this.deps.schedule ?? ((callback, milliseconds) => {
      const timer = setTimeout(callback, milliseconds);
      return () => clearTimeout(timer);
    });
    // Install the final deadline before any cleanup. It also bounds Electron's before-quit path.
    this.cancelHard = schedule(() => {
      this.release();
      this.deps.forceExit();
    }, 6_000);
    this.release(); // synchronous; never wait for a backend or a queued shell transition
    let cancelGrace!: () => void;
    const grace = new Promise<void>(resolve => { cancelGrace = schedule(resolve, 2_000); });
    const cleanup = Promise.allSettled(this.deps.cleanup.map(fn => Promise.resolve().then(fn)));
    this.pending = Promise.race([cleanup, grace]).then(() => {
      cancelGrace();
      try { this.deps.quit(); } catch (error) { this.report(error); }
    });
    return this.pending;
  }

  /** Called only when Electron actually reaches will-quit. */
  complete(): void { this.cancelHard?.(); this.cancelHard = null; }

  private release(): void {
    for (const fn of this.deps.release) {
      try { fn(); } catch (error) { this.report(error); }
    }
  }

  private report(error: unknown): void {
    try { this.deps.report(error); } catch { /* broken diagnostics must not stop the emergency exit */ }
  }
}
