import { useState } from "react";
import { useApp, useLoad } from "../context";
import { percent, relativeTime, titleCase } from "../lib";
import type { Message, Priority, Status, Team, Ticket, User } from "../types";
import { Avatar, Chip, ErrorState, Field, PriorityMark, Spinner, StatusBadge } from "../ui";

const STATUSES: Status[] = ["new", "open", "pending", "resolved", "closed"];
const PRIORITIES: Priority[] = ["low", "normal", "high", "urgent"];

function Composer({ ticket, onSent }: { ticket: Ticket; onSent: () => void }) {
  const { api, can, notify } = useApp();
  const canReply = can("ticket:reply") && ticket.requester !== null;
  const canNote = can("ticket:note");
  const [kind, setKind] = useState<"outbound" | "note">(canReply ? "outbound" : "note");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  if (!canReply && !canNote) return null;

  async function send() {
    setBusy(true);
    try {
      await api.post(`/tickets/${ticket.id}/messages`, { kind, body });
      setBody("");
      onSent();
    } catch (e) {
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
      />
      <div className="composer-foot">
        <span className="muted">
          {kind === "outbound" ? "Leaves the organisation through the ticket's channel." : "Never leaves the service desk."}
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
            <span className="muted">
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

        {t.ai_category && (
          <div className="card panel panel-ai">
            <h2>AI triage</h2>
            {t.ai_summary && <p>{t.ai_summary}</p>}
            <dl className="facts">
              <dt>Suggested category</dt>
              <dd>{titleCase(t.ai_category)}</dd>
              <dt>Suggested priority</dt>
              <dd>{t.ai_priority ? titleCase(t.ai_priority) : "–"}</dd>
              <dt>Confidence</dt>
              <dd>{percent(t.ai_confidence)}</dd>
            </dl>
            {suggestion && editable && (
              <button className="btn btn-block" onClick={() => void update({ category: suggestion })}>
                Apply “{titleCase(suggestion)}”
              </button>
            )}
            <p className="muted small">A suggestion, not a decision. Personal data was masked before analysis.</p>
          </div>
        )}
      </aside>
    </div>
  );
}
