import { useState } from "react";
import { useApp, useLoad } from "../context";
import { percent, relativeTime, titleCase } from "../lib";
import type { Activity, Message, Priority, Status, SystemInfo, Team, Ticket, User } from "../types";
import { ChannelIcon, SparkIcon } from "../icons";
import { Avatar, Chip, ErrorState, Field, PriorityMark, Spinner, StatusBadge } from "../ui";

const STATUSES: Status[] = ["new", "open", "pending", "resolved", "closed"];
const PRIORITIES: Priority[] = ["low", "normal", "high", "urgent"];

// Unsent text survives moving between tickets, but only in memory: it is never
// written to browser storage, and it is gone when the tab closes.
const drafts = new Map<number, string>();

const ACTIVITY_TEXT: Record<string, string> = {
  "ticket.created": "created the ticket",
  "ticket.ingested": "Ticket received",
  "ticket.message_received": "New message from the requester",
  "ticket.classified": "AI triage suggested labels",
  "automation.rule_applied": "An automation rule was applied",
  "ticket.note_added": "added an internal note",
  "ticket.replied": "replied to the requester",
  "message.delivered": "Reply delivered",
  "message.delivery_failed": "Reply could not be delivered",
};

function describeActivity(entry: Activity): string {
  if (entry.action === "ticket.updated") {
    const changes = (entry.details.changes ?? {}) as Record<string, unknown>;
    const parts = Object.entries(changes).map(([field, value]) => {
      const label = field.replace(/_id$/, "").replace(/_/g, " ");
      const to = Array.isArray(value) && typeof value[1] === "string" && !/^[0-9a-f-]{20,}$/.test(value[1]) ? value[1] : "";
      return to ? `${label} → ${to}` : label;
    });
    return `changed ${parts.join(", ") || "the ticket"}`;
  }
  return ACTIVITY_TEXT[entry.action] ?? entry.action;
}

function ActivityList({ ticketId, version }: { ticketId: number; version: string }) {
  const { api } = useApp();
  const activity = useLoad(() => api.get<Activity[]>(`/tickets/${ticketId}/activity`), [ticketId, version]);
  if (!activity.data || activity.data.length === 0) return null;
  return (
    <div className="card panel">
      <h2>Activity</h2>
      <ol className="timeline">
        {activity.data.slice(0, 12).map((entry, index) => {
          const personal = entry.actor_kind !== "system" && /^(created|changed|added|replied)/.test(describeActivity(entry));
          return (
            <li key={index}>
              <span>
                {personal && <strong>{entry.actor_name} </strong>}
                {describeActivity(entry)}
                {entry.channel === "mcp" && <Chip tone="ai">agent</Chip>}
              </span>
              <time dateTime={entry.occurred_at} title={new Date(entry.occurred_at).toLocaleString()}>
                {relativeTime(entry.occurred_at)}
              </time>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Tags({ ticket, editable, onChange }: { ticket: Ticket; editable: boolean; onChange: (tags: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const valid = /^[\w.-]{1,40}$/.test(draft);
  return (
    <div className="field">
      <span className="field-label">Tags</span>
      <div className="tags">
        {ticket.tags.map((tag) => (
          <span key={tag} className="chip">
            {tag}
            {editable && (
              <button className="chip-x" aria-label={`Remove tag ${tag}`} onClick={() => onChange(ticket.tags.filter((t) => t !== tag))}>
                ×
              </button>
            )}
          </span>
        ))}
        {ticket.tags.length === 0 && !editable && <span className="muted">None</span>}
      </div>
      {editable && (
        <form
          className="tag-add"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid && !ticket.tags.includes(draft)) onChange([...ticket.tags, draft]);
            setDraft("");
          }}
        >
          <input value={draft} maxLength={40} placeholder="Add a tag" aria-label="Add a tag" onChange={(e) => setDraft(e.target.value.trim())} />
          <button className="btn" disabled={!valid}>
            Add
          </button>
        </form>
      )}
      {editable && <span className="field-hint">Letters, digits, dot, dash. Tags are not encrypted — no names.</span>}
    </div>
  );
}

function Composer({ ticket, onSent }: { ticket: Ticket; onSent: () => void }) {
  const { api, can, notify } = useApp();
  const canReply = can("ticket:reply") && ticket.requester !== null;
  const canNote = can("ticket:note");
  const [kind, setKind] = useState<"outbound" | "note">(canReply ? "outbound" : "note");
  const [body, setBodyState] = useState(() => drafts.get(ticket.id) ?? "");
  const [busy, setBusy] = useState(false);
  if (!canReply && !canNote) return null;

  function setBody(text: string) {
    setBodyState(text);
    if (text) drafts.set(ticket.id, text);
    else drafts.delete(ticket.id);
  }

  async function send() {
    const text = body;
    setBusy(true);
    setBody(""); // clear now, so text typed while this is in flight is never wiped
    try {
      await api.post(`/tickets/${ticket.id}/messages`, { kind, body: text });
      onSent();
    } catch (e) {
      // Give the text back unless the person has already started something new.
      if (!drafts.has(ticket.id)) setBody(text);
      notify(e instanceof Error ? e.message : "Could not send", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`composer composer-${kind}`}>
      <div className="tabs" role="tablist">
        {canReply && (
          <button role="tab" aria-selected={kind === "outbound"} className={`tab${kind === "outbound" ? " tab-active" : ""}`} onClick={() => setKind("outbound")}>
            Reply to requester
          </button>
        )}
        {canNote && (
          <button role="tab" aria-selected={kind === "note"} className={`tab${kind === "note" ? " tab-active" : ""}`} onClick={() => setKind("note")}>
            Internal note
          </button>
        )}
      </div>
      <textarea
        rows={4}
        maxLength={100000}
        value={body}
        aria-label={kind === "outbound" ? "Reply" : "Internal note"}
        placeholder={kind === "outbound" ? "Write a reply. It will be sent to the requester." : "Write a note. Only staff can see it."}
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && body.trim() && !busy) void send();
        }}
      />
      <div className="composer-foot">
        <span className="muted">
          {kind === "outbound" ? "Leaves the organisation through the ticket's channel." : "Never leaves the service desk."} Ctrl+Enter to send.
        </span>
        <button className="btn btn-primary" disabled={busy || !body.trim()} onClick={() => void send()}>
          {kind === "outbound" ? "Send reply" : "Add note"}
        </button>
      </div>
    </div>
  );
}

function Thread({ messages }: { messages: Message[] }) {
  return (
    <ol className="thread">
      {messages.map((m) => (
        <li key={m.id} className={`msg msg-${m.kind}`}>
          <Avatar name={m.author_name} tone={m.author_type} />
          <div className="msg-bubble">
            <header>
              <strong>{m.author_name}</strong>
              {m.kind === "note" && <Chip tone="note">Internal note</Chip>}
              {m.kind === "outbound" && m.delivery_status !== "not_applicable" && (
                <Chip tone={m.delivery_status === "failed" ? "danger" : "neutral"}>{titleCase(m.delivery_status)}</Chip>
              )}
              <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString()}>
                {relativeTime(m.created_at)}
              </time>
            </header>
            {/* Plain text only; React escapes it. */}
            <p className="msg-body">{m.body}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function TicketPage({ id }: { id: number }) {
  const { api, config, can, notify } = useApp();
  const ticket = useLoad(() => api.get<Ticket>(`/tickets/${id}`), [id]);
  const messages = useLoad(() => api.get<Message[]>(`/tickets/${id}/messages`), [id]);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems"), []);
  const users = useLoad(() => (can("ticket:assign") ? api.get<User[]>("/users") : Promise.resolve([])), []);

  if (ticket.error) return <ErrorState message={ticket.error} retry={ticket.reload} />;
  if (!ticket.data) return <Spinner />;
  const t = ticket.data;
  const editable = can("ticket:update");

  async function update(patch: Record<string, unknown>) {
    try {
      ticket.setData(await api.patch<Ticket>(`/tickets/${id}`, patch));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not update the ticket", "error");
    }
  }

  const suggestion = t.ai_category && t.ai_category !== t.category ? t.ai_category : null;
  const suggestedSystem =
    t.ai_system_key && t.ai_system_key !== t.system_key
      ? (systems.data ?? []).find((s) => s.key === t.ai_system_key)
      : undefined;
  const staff = (users.data ?? []).filter((u) => u.kind === "user");

  return (
    <div className="ticket">
      <section className="ticket-main">
        <a className="back" href="#/">
          ← Inbox
        </a>
        <header className="ticket-head">
          <span className="ticket-key">{t.key}</span>
          <h1>{t.subject}</h1>
          <div className="ticket-meta">
            <StatusBadge status={t.status} />
            <PriorityMark priority={t.priority} />
            {t.system_name ? <Chip tone="system">{t.system_name}</Chip> : <Chip tone="note">No system</Chip>}
            <span className="muted meta-inline">
              <ChannelIcon channel={t.channel} />
              opened {relativeTime(t.created_at)} via {titleCase(t.channel)}
            </span>
          </div>
        </header>
        {t.pii_redacted && (
          <p className="info-banner">
            Personal data is masked for your role. Placeholders such as [EMAIL] replace identifiers.
          </p>
        )}
        {messages.error ? (
          <ErrorState message={messages.error} retry={messages.reload} />
        ) : !messages.data ? (
          <Spinner />
        ) : (
          <Thread messages={messages.data} />
        )}
        <Composer
          ticket={t}
          onSent={() => {
            messages.reload();
            ticket.reload();
          }}
        />
      </section>

      <aside className="ticket-side">
        <div className="card panel">
          <h2>Details</h2>
          <Field label="System" hint={t.system_id ? undefined : "Which system is this ticket about?"}>
            <select value={t.system_id ?? ""} disabled={!editable} onChange={(e) => void update({ system_id: e.target.value || null })}>
              <option value="">Not specified</option>
              {(systems.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
              {t.system_id && !(systems.data ?? []).some((s) => s.id === t.system_id) && (
                <option value={t.system_id}>{t.system_name} (retired)</option>
              )}
            </select>
          </Field>
          <Field label="Status">
            <select value={t.status} disabled={!editable} onChange={(e) => void update({ status: e.target.value })}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {titleCase(s)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Priority">
            <select value={t.priority} disabled={!editable} onChange={(e) => void update({ priority: e.target.value })}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {titleCase(p)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Category">
            <select value={t.category ?? ""} disabled={!editable} onChange={(e) => void update({ category: e.target.value || null })}>
              <option value="">Uncategorised</option>
              {config.categories.map((c) => (
                <option key={c} value={c}>
                  {titleCase(c)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Team">
            <select value={t.team_id ?? ""} disabled={!editable} onChange={(e) => void update({ team_id: e.target.value || null })}>
              <option value="">Triage</option>
              {(teams.data ?? []).map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Assignee">
            {can("ticket:assign") ? (
              <select value={t.assignee_id ?? ""} onChange={(e) => void update({ assignee_id: e.target.value || null })}>
                <option value="">Unassigned</option>
                {staff.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name || u.email}
                  </option>
                ))}
              </select>
            ) : (
              <span>{t.assignee_name ?? "Unassigned"}</span>
            )}
          </Field>
          <Tags ticket={t} editable={editable} onChange={(tags) => void update({ tags })} />
        </div>

        <div className="card panel">
          <h2>Requester</h2>
          {t.requester ? (
            <div className="user">
              <Avatar name={t.requester.display_name} tone="requester" />
              <div className="user-text">
                <strong>{t.requester.display_name}</strong>
                <span>{t.requester.email ?? "Contact details hidden"}</span>
              </div>
            </div>
          ) : (
            <span className="muted">Internal ticket</span>
          )}
        </div>

        <ActivityList ticketId={t.id} version={`${t.updated_at}:${messages.data?.length ?? 0}`} />

        {t.ai_category && (
          <div className="card panel panel-ai">
            <h2>
              <SparkIcon /> AI triage
            </h2>
            {t.ai_summary && <p>{t.ai_summary}</p>}
            <dl className="facts">
              <dt>Suggested category</dt>
              <dd>{titleCase(t.ai_category)}</dd>
              <dt>Suggested priority</dt>
              <dd>{t.ai_priority ? titleCase(t.ai_priority) : "–"}</dd>
              <dt>Suggested system</dt>
              <dd>{t.ai_system_key ?? "–"}</dd>
              <dt>Confidence</dt>
              <dd className="confidence">
                <meter min={0} max={1} low={0.4} high={0.75} optimum={1} value={t.ai_confidence ?? 0} />
                {percent(t.ai_confidence)}
              </dd>
            </dl>
            {suggestion && editable && (
              <button className="btn btn-block" onClick={() => void update({ category: suggestion })}>
                Apply “{titleCase(suggestion)}”
              </button>
            )}
            {suggestedSystem && editable && (
              <button className="btn btn-block" onClick={() => void update({ system_id: suggestedSystem.id })}>
                Set system to “{suggestedSystem.name}”
              </button>
            )}
            <p className="muted small">A suggestion, not a decision. Personal data was masked before analysis.</p>
          </div>
        )}
      </aside>
    </div>
  );
}
