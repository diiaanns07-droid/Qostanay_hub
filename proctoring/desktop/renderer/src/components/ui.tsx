import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { ApiErrorBody, SourceMode } from "@contracts/qorgau-v1.generated";
import { ERROR_HINT, SOURCE_MODE, SOURCE_MODE_RU } from "../lib/labels";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "info";

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "warn";
  size?: "md" | "lg" | "sm";
  busy?: boolean;
};

export function Button({ variant = "secondary", size = "md", busy = false, className, children, disabled, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`btn btn-${variant} btn-${size} ${className ?? ""}`}
    >
      {busy && <span className="spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: Tone }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

export function SourceModeBadge({ mode, fixture }: { mode: SourceMode | null; fixture: boolean }) {
  if (!mode && !fixture) return null;
  const tone: Tone = mode === "live" ? "accent" : "warn";
  return (
    <span className="mode-badges">
      {mode && (
        <Badge tone={tone} title={`Источник данных: ${SOURCE_MODE_RU[mode]}`}>
          {SOURCE_MODE[mode]}
        </Badge>
      )}
      {fixture && (
        <Badge tone="danger" title="Данные FixtureBridge: тестовые, не backend и не камера">
          FIXTURE
        </Badge>
      )}
    </span>
  );
}

export function Banner({
  tone,
  title,
  children,
  actions,
  role,
}: {
  tone: Tone;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: "alert" | "status";
}) {
  return (
    <div className={`banner banner-${tone}`} role={role ?? (tone === "danger" ? "alert" : "status")}>
      <div className="banner-body">
        {title && <div className="banner-title">{title}</div>}
        {children && <div className="banner-text">{children}</div>}
      </div>
      {actions && <div className="banner-actions">{actions}</div>}
    </div>
  );
}

export function ErrorBanner({
  error,
  onRetry,
  onDismiss,
  context,
}: {
  error: ApiErrorBody;
  onRetry?: () => void;
  onDismiss?: () => void;
  context?: string;
}) {
  const hint = ERROR_HINT[error.code];
  return (
    <Banner
      tone="danger"
      title={context ? `${context}: ${hint ?? "ошибка"}` : (hint ?? "Ошибка")}
      actions={
        <>
          {onRetry && error.retryable && (
            <Button size="sm" onClick={onRetry}>
              Повторить
            </Button>
          )}
          {onDismiss && (
            <Button size="sm" variant="ghost" onClick={onDismiss}>
              Скрыть
            </Button>
          )}
        </>
      }
    >
      <span>{error.message}</span> <code className="err-code">{error.code}</code>
    </Banner>
  );
}

export function Progress({ value, max, tone = "accent", label }: { value: number; max: number; tone?: Tone; label: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={`progress progress-${tone}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <div className="progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner spinner-lg" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function Card({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h2 className="card-title">{title}</h2>}
          {aside && <div className="card-aside">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** Modal dialog with focus trap, Escape to close and focus restore. */
export function Dialog({
  title,
  children,
  actions,
  onClose,
  tone = "neutral",
}: {
  title: string;
  children: ReactNode;
  actions: ReactNode;
  onClose: () => void;
  tone?: Tone;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () =>
      el ? [...el.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), textarea, select, [tabindex='0']")] : [];
    (focusables()[0] ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      }
      if (e.key === "Tab") {
        const f = focusables();
        if (f.length === 0) return;
        const first = f[0]!;
        const last = f[f.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
  }, []);
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog dialog-${tone}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}>
        <h2 id={titleId} className="dialog-title">
          {title}
        </h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">{actions}</div>
      </div>
    </div>
  );
}

export function KV({ items }: { items: Array<[ReactNode, ReactNode]> }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div className="kv-row" key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
