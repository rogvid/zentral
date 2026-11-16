import { useCallback, useEffect, useMemo, useState } from "react";
import { Api } from "./api";
import { loadConfig, startSession, type Session } from "./auth";
import { AppContext, type AppState } from "./context";
import { parseRoute, type Route } from "./lib";
import { AdminPage } from "./pages/Admin";
import { AuditPage } from "./pages/Audit";
import { InboxPage } from "./pages/Inbox";
import { PrivacyPage } from "./pages/Privacy";
import { TicketPage } from "./pages/Ticket";
import type { AppConfig, Me } from "./types";
import { Avatar, ErrorState, Spinner } from "./ui";

interface Toast {
  id: number;
  message: string;
  kind: "info" | "error";
}

type Boot =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; config: AppConfig; session: Session; me: Me };

function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

const NAV: { page: Route["page"]; href: string; label: string; needs: string[] }[] = [
  { page: "inbox", href: "#/", label: "Inbox", needs: ["ticket:read"] },
  { page: "audit", href: "#/audit", label: "Audit trail", needs: ["audit:read"] },
  { page: "privacy", href: "#/privacy", label: "Privacy requests", needs: ["privacy:export"] },
  {
    page: "admin",
    href: "#/admin",
    label: "Administration",
    needs: ["team:manage", "system:manage", "source:manage", "rule:manage"],
  },
];

export function App() {
  const [boot, setBoot] = useState<Boot>({ phase: "loading" });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const route = useRoute();

  useEffect(() => {
    (async () => {
      const config = await loadConfig();
      const session = await startSession(config);
      const me = await new Api(session).get<Me>("/me");
      setBoot({ phase: "ready", config, session, me });
    })().catch((e: unknown) =>
      setBoot({ phase: "error", message: e instanceof Error ? e.message : "Could not start" }),
    );
  }, []);

  const notify = useCallback((message: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  const state = useMemo<AppState | null>(() => {
    if (boot.phase !== "ready") return null;
    const permissions = new Set(boot.me.permissions);
    return {
      api: new Api(boot.session),
      config: boot.config,
      me: boot.me,
      can: (permission) => permissions.has(permission),
      notify,
    };
  }, [boot, notify]);

  if (boot.phase === "loading") return <Spinner label="Signing in" />;
  if (boot.phase === "error") return <ErrorState message={boot.message} retry={() => window.location.assign("/")} />;
  if (!state) return null;

  const nav = NAV.filter((item) => item.needs.some((p) => state.can(p)));
  const allowed = nav.some((item) => item.page === route.page) || (route.page === "ticket" && state.can("ticket:read"));
  const active = route.page === "ticket" ? "inbox" : route.page;

  return (
    <AppContext.Provider value={state}>
      <div className="shell">
        <aside className="sidebar">
          <a className="brand" href="#/">
            <span className="brand-mark">Z</span> Zentral
          </a>
          {state.config.auth.mode === "dev" && <p className="warning-banner small">Development sign-in</p>}
          <nav aria-label="Main">
            {nav.map((item) => (
              <a
                key={item.page}
                href={item.href}
                className={`nav-link${active === item.page ? " nav-active" : ""}`}
                aria-current={active === item.page ? "page" : undefined}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="user">
              <Avatar name={state.me.display_name || state.me.email || "User"} />
              <div className="user-text">
                <strong>{state.me.display_name || state.me.email}</strong>
                <span>{state.me.roles.map((r) => r.replace("Zentral.", "")).join(", ") || "No role"}</span>
              </div>
            </div>
            <button className="btn btn-quiet btn-block" onClick={() => void boot.session.signOut()}>
              Sign out
            </button>
          </div>
        </aside>
        <main className="main">
          {!allowed ? (
            <ErrorState message="Your role does not include access to this page." />
          ) : route.page === "inbox" ? (
            <InboxPage />
          ) : route.page === "ticket" ? (
            <TicketPage key={route.id} id={route.id} />
          ) : route.page === "audit" ? (
            <AuditPage />
          ) : route.page === "privacy" ? (
            <PrivacyPage />
          ) : (
            <AdminPage />
          )}
        </main>
        <div className="toasts" aria-live="polite">
          {toasts.map((toast) => (
            <div key={toast.id} className={`toast toast-${toast.kind}`}>
              {toast.message}
            </div>
          ))}
        </div>
      </div>
    </AppContext.Provider>
  );
}
