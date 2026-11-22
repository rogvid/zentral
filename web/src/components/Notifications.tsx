import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../context";
import { BellIcon } from "../icons";
import { relativeTime } from "../lib";
import type { NotificationPage } from "../types";

const POLL_MS = 60_000;
const DESKTOP_KEY = "zentral.desktop-alerts";

export const NOTIFICATION_TEXT: Record<string, string> = {
  assigned: "Assigned to you",
  requester_replied: "The requester replied",
  response_at_risk: "First reply due soon",
  response_breached: "First reply overdue",
  resolve_at_risk: "Resolution due soon",
  resolve_breached: "Resolution overdue",
};

/** Ask the bell to refresh, e.g. after a ticket's notifications were marked read. */
export function refreshNotifications() {
  window.dispatchEvent(new Event("zentral:notifications"));
}

function readPreference(): boolean {
  try {
    return localStorage.getItem(DESKTOP_KEY) === "on";
  } catch {
    return false;
  }
}

function writePreference(on: boolean) {
  try {
    if (on) localStorage.setItem(DESKTOP_KEY, "on");
    else localStorage.removeItem(DESKTOP_KEY);
  } catch {
    /* a private window: the preference simply is not remembered */
  }
}

/**
 * The notification bell. Notifications carry a ticket key and what happened —
 * never ticket content — so a desktop alert cannot leak anything onto a
 * shared screen beyond "ZEN-42: first reply overdue".
 */
export function NotificationBell() {
  const { api } = useApp();
  const [page, setPage] = useState<NotificationPage | null>(null);
  const [open, setOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => readPreference() && "Notification" in window && Notification.permission === "granted");
  const seen = useRef<Set<string> | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.get<NotificationPage>("/notifications?limit=30");
      const known = seen.current;
      if (known && desktop) {
        for (const n of next.items) {
          if (!n.read_at && !known.has(n.id)) {
            new Notification(n.ticket_key, { body: NOTIFICATION_TEXT[n.kind] ?? n.kind, tag: n.id });
          }
        }
      }
      seen.current = new Set(next.items.map((n) => n.id));
      setPage(next);
    } catch {
      /* transient: the next poll tries again */
    }
  }, [api, desktop]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" || desktop) void load();
    }, POLL_MS);
    const refresh = () => void load();
    window.addEventListener("zentral:notifications", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("zentral:notifications", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [load, desktop]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  async function markAll() {
    await api.post("/notifications/read", {}).catch(() => undefined);
    void load();
  }

  async function toggleDesktop() {
    if (desktop) {
      writePreference(false);
      setDesktop(false);
      return;
    }
    if (!("Notification" in window)) return;
    const permission = await Notification.requestPermission();
    const on = permission === "granted";
    writePreference(on);
    setDesktop(on);
  }

  const unread = page?.unread ?? 0;
  return (
    <div className="bell" ref={panel}>
      <button
        className="nav-link bell-button"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        onClick={() => setOpen((o) => !o)}
      >
        <BellIcon />
        <span className="bell-label">Notifications</span>
        {unread > 0 && <span className="bell-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel card" role="dialog" aria-label="Notifications">
          <header className="bell-head">
            <strong>Notifications</strong>
            {unread > 0 && (
              <button className="link-button" onClick={() => void markAll()}>
                Mark all read
              </button>
            )}
          </header>
          {!page || page.items.length === 0 ? (
            <p className="muted small bell-empty">Nothing yet. You will hear about tickets assigned to you, replies, and targets at risk.</p>
          ) : (
            <ol className="bell-list">
              {page.items.map((n) => (
                <li key={n.id} className={n.read_at ? "" : "bell-unread"}>
                  <a
                    href={`#/tickets/${n.ticket_id}`}
                    onClick={() => {
                      setOpen(false);
                      void api.post("/notifications/read", { ids: [n.id] }).then(() => load()).catch(() => undefined);
                    }}
                  >
                    <span className={`bell-dot bell-${n.kind.split("_").pop()}`} aria-hidden="true" />
                    <span className="bell-text">
                      <strong>{n.ticket_key}</strong> {NOTIFICATION_TEXT[n.kind] ?? n.kind}
                    </span>
                    <time dateTime={n.created_at}>{relativeTime(n.created_at)}</time>
                  </a>
                </li>
              ))}
            </ol>
          )}
          {"Notification" in window && (
            <label className="bell-desktop small">
              <input type="checkbox" checked={desktop} onChange={() => void toggleDesktop()} />
              Desktop alerts on this device
            </label>
          )}
        </div>
      )}
    </div>
  );
}
