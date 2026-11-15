// Shared presentational components. No inline styles (strict CSP) and no raw
// HTML: everything is rendered through React text nodes.

import { useEffect, useRef, type ReactNode } from "react";
import { initials, titleCase } from "./lib";
import type { Priority, Status } from "./types";

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge status-${status}`}>{titleCase(status)}</span>;
}

export function PriorityMark({ priority }: { priority: Priority }) {
  return (
    <span className={`priority priority-${priority}`} title={`${titleCase(priority)} priority`}>
      <span className="priority-dot" aria-hidden="true" />
      {titleCase(priority)}
    </span>
  );
}

export function Chip({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}

export function Avatar({ name, tone = "staff" }: { name: string; tone?: string }) {
  return (
    <span className={`avatar avatar-${tone}`} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}…
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Something went wrong.</strong> {message}
      {retry && (
        <button className="btn btn-quiet" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="state state-empty">
      <strong>{title}</strong>
      {hint && <span>{hint}</span>}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-label={title}>
      <header className="dialog-head">
        <h2>{title}</h2>
        <button className="btn btn-quiet" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </header>
      {children}
    </dialog>
  );
}

export function PageHead({ title, sub, actions }: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
