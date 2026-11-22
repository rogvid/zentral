import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useApp, useLoad } from "../context";
import { channelName, query, relativeTime, titleCase, until } from "../lib";
import type { Priority, SystemInfo, Team, Ticket, TicketPage } from "../types";
import { BoxIcon, ChannelIcon, ClockIcon, InboxIcon, PlusIcon, SparkIcon, UserIcon } from "../icons";
import { Compass, firstName, greeting, TeaCup } from "../brand";
import { Avatar, Chip, Dialog, Empty, ErrorState, Field, PriorityMark, SkeletonRows, Stat, StatusBadge } from "../ui";

type View = "active" | "mine" | "unassigned" | "all";
type Stats = { active: number; new: number; unassigned: number; mine: number; urgent: number; no_system: number; overdue: number };
const VIEWS: { id: View; label: string }[] = [
  { id: "active", label: "Active" },
  { id: "mine", label: "Assigned to me" },
  { id: "unassigned", label: "Unassigned" },
  { id: "all", label: "All" },
];
const PAGE_SIZE = 25;
const ACTIVE = "&status=new&status=open&status=pending";

function NewTicket({
  teams,
  systems,
  onClose,
  onCreated,
}: {
  teams: Team[];
  systems: SystemInfo[];
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const { api, config, can, notify } = useApp();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const text = (name: string) => String(form.get(name) ?? "").trim();
    setBusy(true);
    try {
      const ticket = await api.post<{ id: number }>("/tickets", {
        subject: text("subject"),
        body: text("body"),
        priority: text("priority"),
        category: text("category") || null,
        team_id: text("team_id") || null,
        system_id: text("system_id") || null,
        requester_email: text("requester_email") || null,
        requester_name: text("requester_name") || null,
      });
      onCreated(ticket.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not create the ticket", "error");
      setBusy(false);
    }
  }

  return (
    <Dialog title="New ticket" onClose={onClose}>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <Field label="Subject">
          <input name="subject" required maxLength={200} autoFocus />
        </Field>
        <Field label="Description">
          <textarea name="body" required rows={5} maxLength={100000} />
        </Field>
        {can("ticket:read_pii") && (
          <div className="form-row">
            <Field label="Requester e-mail" hint="Optional. Leave empty for internal tickets.">
              <input name="requester_email" type="email" maxLength={320} />
            </Field>
            <Field label="Requester name">
              <input name="requester_name" maxLength={200} />
            </Field>
          </div>
        )}
        <Field label="System" hint="Which system is this about? Leave empty to let AI suggest one.">
          <select name="system_id" defaultValue="">
            <option value="">Not specified</option>
            {systems.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <div className="form-row">
          <Field label="Priority">
            <select name="priority" defaultValue="normal">
              {(["low", "normal", "high", "urgent"] satisfies Priority[]).map((p) => (
                <option key={p} value={p}>
                  {titleCase(p)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Category" hint="Leave empty to let AI suggest one.">
            <select name="category" defaultValue="">
              <option value="">Auto</option>
              {config.categories.map((c) => (
                <option key={c} value={c}>
                  {titleCase(c)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Team">
            <select name="team_id" defaultValue="">
              <option value="">Triage</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <footer className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            Create ticket
          </button>
        </footer>
      </form>
    </Dialog>
  );
}

/** The next promise on a ticket: a first reply, then a resolution. */
function Due({ ticket: t }: { ticket: Ticket }) {
  if (t.response_state === "breached" && t.response_due_at)
    return <span className="due due-late">Reply overdue · {until(t.response_due_at).text}</span>;
  if (t.response_state === "due" && t.response_due_at) {
    const next = until(t.response_due_at);
    return <span className={`due${dueSoon(t.response_due_at) ? " due-soon" : ""}`}>Reply {next.text}</span>;
  }
  if (t.resolve_state === "breached" && t.resolve_due_at)
    return <span className="due due-late">Overdue · {until(t.resolve_due_at).text}</span>;
  if (t.resolve_state === "paused") return <span className="due due-paused">Waiting on requester</span>;
  if (t.resolve_state === "due" && t.resolve_due_at)
    return <span className={`due${dueSoon(t.resolve_due_at) ? " due-soon" : ""}`}>Resolve {until(t.resolve_due_at).text}</span>;
  if (t.resolve_state === "met") return <span className="due due-met">Resolved on time</span>;
  if (t.resolve_state === "late") return <span className="due">Resolved late</span>;
  return <span className="muted">–</span>;
}

function dueSoon(iso: string): boolean {
  return new Date(iso).getTime() - Date.now() < 60 * 60 * 1000;
}

/** One plain sentence about the queue, so the numbers below read as a story. */
function QueueSummary({ stats }: { stats: Stats }) {
  if (stats.active === 0) return <>Your queues are clear. Nothing is waiting.</>;
  const parts: ReactNode[] = [];
  if (stats.overdue) parts.push(<strong key="o">{stats.overdue} overdue</strong>);
  if (stats.urgent) parts.push(<strong key="u">{stats.urgent} urgent</strong>);
  if (stats.mine) parts.push(<span key="m">{stats.mine} with you</span>);
  if (stats.unassigned) parts.push(<span key="w">{stats.unassigned} waiting for someone to pick {stats.unassigned === 1 ? "it" : "them"} up</span>);
  const total = `${stats.active} open ticket${stats.active === 1 ? "" : "s"}`;
  if (parts.length === 0) return <>{total}, all in hand.</>;
  return (
    <>
      {total} —{" "}
      {parts.map((part, i) => (
        <span key={i}>
          {i > 0 && (i === parts.length - 1 ? " and " : ", ")}
          {part}
        </span>
      ))}
      .
    </>
  );
}

export function InboxPage() {
  const { api, me, can } = useApp();
  const name = firstName(me.display_name);
  const [view, setView] = useState<View>("active");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [system, setSystem] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" jumps to search, like most tools people already use.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setTerm(search.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems"), []);
  const page = useLoad(() => {
    const base = query({
      q: term,
      limit: PAGE_SIZE,
      offset,
      assignee_id: view === "mine" ? me.subject : undefined,
      unassigned: view === "unassigned",
      overdue: overdueOnly,
      system_id: system && system !== "none" ? system : undefined,
      no_system: system === "none",
    });
    return api.get<TicketPage>(`/tickets${base}${view === "all" ? "" : ACTIVE}`);
  }, [view, term, offset, system, overdueOnly]);
  const stats = useLoad(() => api.get<Stats>("/tickets/stats"), [page.data]);
  const [fresh, setFresh] = useState(false);
  const shownActive = stats.data?.active;

  // Poll the (unaudited, content-free) counters; offer a refresh when they move.
  useEffect(() => {
    setFresh(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible" || shownActive === undefined) return;
      api
        .get<Stats>("/tickets/stats")
        .then((latest) => setFresh(latest.active !== shownActive))
        .catch(() => undefined);
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [api, shownActive]);

  function show(next: View, opts: { overdue?: boolean; system?: string } = {}) {
    setView(next);
    setOverdueOnly(opts.overdue ?? false);
    setSystem(opts.system ?? "");
    setOffset(0);
  }

  const data = page.data;
  return (
    <>
      <header className="page-head hello">
        <div>
          <h1>
            {greeting()}
            {name ? (
              <>
                , <em>{name}</em>.
              </>
            ) : (
              "."
            )}
          </h1>
          <p>{stats.data ? <QueueSummary stats={stats.data} /> : "\u00a0"}</p>
        </div>
        {can("ticket:create") && (
          <div className="page-actions">
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              <PlusIcon />
              New ticket
            </button>
          </div>
        )}
      </header>
      <div className="stats">
        <Stat label="Active" value={stats.data?.active} icon={<InboxIcon size={18} />} tone="accent"
          active={view === "active" && !overdueOnly && !system} onClick={() => show("active")} />
        <Stat label="Overdue" value={stats.data?.overdue} icon={<ClockIcon size={18} />} tone="danger"
          active={overdueOnly} onClick={() => show("active", { overdue: true })} />
        <Stat label="Unassigned" value={stats.data?.unassigned} icon={<UserIcon size={18} />} tone="warn"
          active={view === "unassigned"} onClick={() => show("unassigned")} />
        <Stat label="Assigned to me" value={stats.data?.mine} icon={<SparkIcon size={18} />} tone="ok"
          active={view === "mine"} onClick={() => show("mine")} />
        <Stat label="No system yet" value={stats.data?.no_system} icon={<BoxIcon size={18} />} tone="neutral"
          active={system === "none"} onClick={() => show("active", { system: "none" })} />
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              role="tab"
              aria-selected={view === v.id}
              className={`tab${view === v.id ? " tab-active" : ""}`}
              onClick={() => show(v.id, { system })}
            >
              {v.label}
            </button>
          ))}
        </div>
        <select
          aria-label="Filter by system"
          value={system}
          onChange={(e) => {
            setSystem(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All systems</option>
          {(systems.data ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
          <option value="none">No system yet</option>
        </select>
        <input
          ref={searchRef}
          className="search"
          type="search"
          placeholder="Search subject or ticket key   /"
          aria-label="Search tickets"
          value={search}
          maxLength={200}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {fresh && (
        <button className="banner-new" onClick={page.reload}>
          The queue has changed — show the latest
        </button>
      )}
      {page.error ? (
        <ErrorState message={page.error} retry={page.reload} />
      ) : !data ? (
        <SkeletonRows />
      ) : data.items.length === 0 ? (
        term || system || overdueOnly ? (
          <Empty
            art={<Compass />}
            title={term ? `Nothing matches “${term}”` : "Nothing matches this filter"}
            hint={term ? "Try a ticket key such as ZEN-12, or fewer words." : "Try another view, or clear the filter."}
          />
        ) : view === "mine" ? (
          <Empty art={<TeaCup />} title="Nothing on your plate" hint="Nothing is assigned to you right now. The unassigned queue may need a hand." />
        ) : (
          <Empty art={<TeaCup />} title="All caught up" hint="Nothing is waiting. A good moment for a cup of tea." />
        )
      ) : (
        <div className={`card table-card ticket-table${page.loading ? " is-loading" : ""}`}>
          <table className="table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>State</th>
                <th>System</th>
                <th>Category</th>
                <th>With</th>
                <th>Due</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((t) => (
                <tr key={t.id} className={`row-${t.priority}`} onClick={() => (window.location.hash = `#/tickets/${t.id}`)}>
                  <td className="cell-main">
                    <a href={`#/tickets/${t.id}`} onClick={(e) => e.stopPropagation()}>
                      <span className="ticket-key">{t.key}</span>
                      <span className="ticket-subject">{t.subject}</span>
                    </a>
                    <span className="cell-sub">
                      <ChannelIcon channel={t.channel} />
                      {t.requester ? t.requester.display_name : "Internal"} · {channelName(t.channel)}
                    </span>
                  </td>
                  <td>
                    <div className="state-cell">
                      <StatusBadge status={t.status} />
                      <PriorityMark priority={t.priority} />
                    </div>
                  </td>
                  <td>
                    {t.system_name ? (
                      <Chip tone="system">{t.system_name}</Chip>
                    ) : t.ai_system_key ? (
                      <Chip tone="ai">{t.ai_system_key}?</Chip>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                  <td>
                    {t.category ? (
                      <Chip>{titleCase(t.category)}</Chip>
                    ) : t.ai_category ? (
                      <Chip tone="ai">{titleCase(t.ai_category)}?</Chip>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                  <td>
                    <span className="owner">
                      {t.assignee_name ? <Avatar name={t.assignee_name} size="sm" /> : <span className="owner-empty" aria-hidden="true" />}
                      <span className="owner-text">
                        <span className={t.assignee_name ? "" : "muted"}>{t.assignee_name ?? "Nobody yet"}</span>
                        <span className="cell-sub">{t.team_name ?? "Triage"}</span>
                      </span>
                    </span>
                  </td>
                  <td className="nowrap">
                    <Due ticket={t} />
                    <span className="cell-sub">updated {relativeTime(t.updated_at)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <footer className="pager">
            <span className="muted">
              {offset + 1}–{offset + data.items.length} of {data.total}
            </span>
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button className="btn" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </footer>
        </div>
      )}
      {creating && (
        <NewTicket
          teams={teams.data ?? []}
          systems={systems.data ?? []}
          onClose={() => setCreating(false)}
          onCreated={(id) => (window.location.hash = `#/tickets/${id}`)}
        />
      )}
    </>
  );
}
