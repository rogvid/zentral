import { useState, type FormEvent } from "react";
import { useApp, useLoad } from "../context";
import { titleCase } from "../lib";
import type { ConnectorInfo, Rule, Source, SystemInfo, Team, User } from "../types";
import { Chip, Dialog, Empty, ErrorState, Field, PageHead, Spinner } from "../ui";

function useAction() {
  const { notify } = useApp();
  return async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) notify(done);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    }
  };
}

function Teams() {
  const { api } = useApp();
  const run = useAction();
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const users = useLoad(() => api.get<User[]>("/users"), []);
  const names = new Map((users.data ?? []).map((u) => [u.id, u.display_name || u.email || u.id]));

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await run(async () => {
      await api.post("/teams", {
        name: String(data.get("name")),
        entra_group_id: String(data.get("group") ?? "").trim() || null,
      });
      form.reset();
      teams.reload();
    }, "Team created");
  }

  if (teams.error) return <ErrorState message={teams.error} retry={teams.reload} />;
  if (!teams.data) return <Spinner />;
  return (
    <section className="stack">
      <form className="card panel form-inline" onSubmit={(e) => void create(e)}>
        <Field label="New team">
          <input name="name" required maxLength={120} placeholder="e.g. Network" />
        </Field>
        <Field label="Entra group ID (optional)" hint="Members of this group join the team automatically.">
          <input name="group" pattern="[0-9a-fA-F\-]{36}" placeholder="00000000-0000-0000-0000-000000000000" />
        </Field>
        <button className="btn btn-primary">Add team</button>
      </form>
      {teams.data.length === 0 && <Empty title="No teams yet" hint="Without teams, every ticket stays in triage." />}
      {teams.data.map((team) => (
        <div key={team.id} className="card panel">
          <header className="panel-head">
            <h2>{team.name}</h2>
            {team.entra_group_id && <Chip>Entra group linked</Chip>}
            <button
              className="btn btn-quiet"
              onClick={() => void run(async () => { await api.delete(`/teams/${team.id}`); teams.reload(); }, "Team deleted")}
            >
              Delete
            </button>
          </header>
          <ul className="members">
            {team.member_ids.map((id) => (
              <li key={id}>
                {names.get(id) ?? id}
                <button
                  className="btn btn-quiet"
                  aria-label={`Remove ${names.get(id) ?? id}`}
                  onClick={() => void run(async () => { await api.delete(`/teams/${team.id}/members/${encodeURIComponent(id)}`); teams.reload(); })}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <select
            aria-label={`Add member to ${team.name}`}
            value=""
            onChange={(e) => {
              const userId = e.target.value;
              if (userId) void run(async () => { await api.post(`/teams/${team.id}/members`, { user_id: userId }); teams.reload(); });
            }}
          >
            <option value="">Add member…</option>
            {(users.data ?? [])
              .filter((u) => !team.member_ids.includes(u.id))
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.email} ({u.kind})
                </option>
              ))}
          </select>
          <p className="muted small">People appear here after their first sign-in.</p>
        </div>
      ))}
    </section>
  );
}

function Systems() {
  const { api } = useApp();
  const run = useAction();
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems?include_inactive=true"), []);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const teamName = (id: string | null) => teams.data?.find((t) => t.id === id)?.name;

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = (name: string) => String(data.get(name) ?? "").trim();
    await run(async () => {
      await api.post("/systems", {
        key: value("key"),
        name: value("label"),
        description: value("description"),
        keywords: value("keywords").split(",").map((k) => k.trim()).filter(Boolean),
        default_team_id: value("team") || null,
      });
      form.reset();
      systems.reload();
    }, "System added");
  }

  const save = (s: SystemInfo, patch: Partial<SystemInfo>) =>
    run(async () => {
      await api.put(`/systems/${s.id}`, {
        key: s.key,
        name: s.name,
        description: s.description,
        keywords: s.keywords,
        default_team_id: s.default_team_id,
        active: s.active,
        ...patch,
      });
      systems.reload();
    });

  if (systems.error) return <ErrorState message={systems.error} retry={systems.reload} />;
  if (!systems.data) return <Spinner />;
  return (
    <section className="stack">
      <form className="card panel form" onSubmit={(e) => void create(e)}>
        <h2>New system</h2>
        <div className="form-row">
          <Field label="Name">
            <input name="label" required maxLength={120} placeholder="e.g. Payment platform" />
          </Field>
          <Field label="Key" hint="Lowercase identifier used by integrations.">
            <input name="key" required pattern="[a-z0-9][a-z0-9_\-]{0,63}" placeholder="payments" />
          </Field>
          <Field label="Owning team" hint="Receives tickets about this system.">
            <select name="team" defaultValue="">
              <option value="">None</option>
              {(teams.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Description" hint="Helps AI recognise tickets about this system.">
          <input name="description" maxLength={500} />
        </Field>
        <Field label="Keywords" hint="Comma-separated words that identify the system in a message.">
          <input name="keywords" placeholder="invoice, checkout, card" />
        </Field>
        <div>
          <button className="btn btn-primary">Add system</button>
        </div>
      </form>
      {systems.data.length === 0 && (
        <Empty title="No systems yet" hint="Add the systems your organisation supports so tickets can be linked to them." />
      )}
      {systems.data.map((s) => (
        <div key={s.id} className="card panel">
          <header className="panel-head">
            <h2>{s.name}</h2>
            <Chip>
              <code>{s.key}</code>
            </Chip>
            {teamName(s.default_team_id) && <Chip>→ {teamName(s.default_team_id)}</Chip>}
            <Chip tone={s.active ? "ok" : "danger"}>{s.active ? "Active" : "Retired"}</Chip>
            <button className="btn btn-quiet" onClick={() => void save(s, { active: !s.active })}>
              {s.active ? "Retire" : "Reactivate"}
            </button>
            <button
              className="btn btn-quiet"
              onClick={() => void run(async () => { await api.delete(`/systems/${s.id}`); systems.reload(); }, "System deleted")}
            >
              Delete
            </button>
          </header>
          {s.description && <p>{s.description}</p>}
          {s.keywords.length > 0 && <p className="muted small">Keywords: {s.keywords.join(", ")}</p>}
        </div>
      ))}
    </section>
  );
}

function Sources() {
  const { api } = useApp();
  const run = useAction();
  const sources = useLoad(() => api.get<Source[]>("/sources"), []);
  const connectors = useLoad(() => api.get<ConnectorInfo[]>("/connectors"), []);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems"), []);
  const [adding, setAdding] = useState(false);
  const [connector, setConnector] = useState("webhook");
  const [issued, setIssued] = useState<Source | null>(null);
  const selected = connectors.data?.find((c) => c.name === connector);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await run(async () => {
      const configText = String(data.get("config") ?? "").trim();
      const secrets: Record<string, string> = {};
      for (const field of selected?.secret_fields ?? []) {
        const value = String(data.get(`secret:${field}`) ?? "");
        if (value) secrets[field] = value;
      }
      const created = await api.post<Source>("/sources", {
        name: String(data.get("name")),
        connector,
        config: configText ? (JSON.parse(configText) as unknown) : {},
        secrets,
        default_team_id: String(data.get("team") ?? "") || null,
        default_system_id: String(data.get("system") ?? "") || null,
      });
      setAdding(false);
      setIssued(created);
      sources.reload();
    });
  }

  if (sources.error) return <ErrorState message={sources.error} retry={sources.reload} />;
  if (!sources.data) return <Spinner />;
  return (
    <section className="stack">
      <div>
        <button className="btn btn-primary" onClick={() => setAdding(true)}>
          Add channel
        </button>
      </div>
      {sources.data.length === 0 && <Empty title="No channels connected" hint="Add e-mail, Teams or a webhook to receive tickets." />}
      {sources.data.map((s) => (
        <div key={s.id} className="card panel">
          <header className="panel-head">
            <h2>{s.name}</h2>
            <Chip>{titleCase(s.connector)}</Chip>
            <Chip tone={s.enabled ? "ok" : "danger"}>{s.enabled ? "Enabled" : "Disabled"}</Chip>
            <button className="btn btn-quiet" onClick={() => void run(async () => { await api.patch(`/sources/${s.id}`, { enabled: !s.enabled }); sources.reload(); })}>
              {s.enabled ? "Disable" : "Enable"}
            </button>
            {s.has_token && (
              <button className="btn btn-quiet" onClick={() => void run(async () => setIssued(await api.post<Source>(`/sources/${s.id}/rotate-token`)))}>
                Rotate token
              </button>
            )}
            <button className="btn btn-quiet" onClick={() => void run(async () => { await api.delete(`/sources/${s.id}`); sources.reload(); }, "Channel removed")}>
              Delete
            </button>
          </header>
          {s.hook_url && (
            <p>
              Webhook URL: <code>{s.hook_url}</code>
            </p>
          )}
          <p className="muted small">
            Secrets: {s.has_secrets ? "stored (encrypted, never shown)" : "none"} · Config: <code>{JSON.stringify(s.config)}</code>
          </p>
        </div>
      ))}

      {adding && (
        <Dialog title="Add channel" onClose={() => setAdding(false)}>
          <form className="form" onSubmit={(e) => void create(e)}>
            <Field label="Name">
              <input name="name" required maxLength={120} />
            </Field>
            <Field label="Connector">
              <select value={connector} onChange={(e) => setConnector(e.target.value)}>
                {(connectors.data ?? []).map((c) => (
                  <option key={c.name} value={c.name}>
                    {titleCase(c.name)} ({c.capabilities.join(", ")})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Configuration (JSON)" hint="Non-secret settings, e.g. the mailbox address.">
              <textarea name="config" rows={3} placeholder='{"mailbox": "servicedesk@example.com"}' spellCheck={false} />
            </Field>
            {(selected?.secret_fields ?? []).map((field) => (
              <Field key={field} label={`${titleCase(field)}${selected?.required_secrets.includes(field) ? "" : " (optional)"}`} hint="Encrypted at rest. Cannot be read back.">
                <input name={`secret:${field}`} type="password" autoComplete="off" required={selected?.required_secrets.includes(field)} />
              </Field>
            ))}
            <Field label="Default team">
              <select name="team" defaultValue="">
                <option value="">Triage</option>
                {(teams.data ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Default system" hint="Everything arriving on this channel concerns this system unless the sender says otherwise.">
              <select name="system" defaultValue="">
                <option value="">None — let AI and rules decide</option>
                {(systems.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
            <footer className="form-actions">
              <button type="button" className="btn" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button className="btn btn-primary">Add channel</button>
            </footer>
          </form>
        </Dialog>
      )}

      {issued?.ingest_token && (
        <Dialog title="Copy the ingest token now" onClose={() => setIssued(null)}>
          <div className="form">
            <p className="warning-banner">This token is shown once. Zentral stores only a hash of it.</p>
            <Field label="Ingest token">
              <input readOnly value={issued.ingest_token} onFocus={(e) => e.target.select()} />
            </Field>
            <Field label="Webhook URL">
              <input readOnly value={issued.hook_url ?? ""} onFocus={(e) => e.target.select()} />
            </Field>
            <footer className="form-actions">
              <button className="btn btn-primary" onClick={() => setIssued(null)}>
                I have stored it safely
              </button>
            </footer>
          </div>
        </Dialog>
      )}
    </section>
  );
}

function Rules() {
  const { api, config } = useApp();
  const run = useAction();
  const rules = useLoad(() => api.get<Rule[]>("/rules"), []);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems"), []);
  const teamName = (id: unknown) => teams.data?.find((t) => t.id === id)?.name ?? "unknown team";
  const systemName = (id: unknown) => systems.data?.find((s) => s.id === id)?.name ?? "unknown system";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = (name: string) => String(data.get(name) ?? "").trim();
    await run(async () => {
      await api.post("/rules", {
        name: value("name"),
        position: Number(value("position") || 100),
        conditions: {
          ...(value("category") && { category: value("category") }),
          ...(value("priority") && { priority: value("priority") }),
          ...(value("system") && { system_id: value("system") }),
          ...(value("subject_text") && { subject_contains: [value("subject_text")] }),
        },
        actions: {
          ...(value("team") && { set_team_id: value("team") }),
          ...(value("set_priority") && { set_priority: value("set_priority") }),
          ...(value("set_system") && { set_system_id: value("set_system") }),
          ...(value("tag") && { add_tags: [value("tag")] }),
        },
      });
      form.reset();
      rules.reload();
    }, "Rule added");
  }

  function describe(rule: Rule): string {
    const c = rule.conditions as { category?: string; priority?: string; subject_contains?: string[]; channel?: string; system_id?: string };
    const a = rule.actions as { set_team_id?: string; set_priority?: string; add_tags?: string[]; set_assignee_id?: string; set_system_id?: string };
    const when = [
      c.category && `category is ${c.category}`,
      c.priority && `priority is ${c.priority}`,
      c.channel && `channel is ${c.channel}`,
      c.system_id && `system is ${systemName(c.system_id)}`,
      c.subject_contains?.length && `subject contains “${c.subject_contains.join("” or “")}”`,
    ].filter(Boolean);
    const then = [
      a.set_team_id && `route to ${teamName(a.set_team_id)}`,
      a.set_priority && `set priority ${a.set_priority}`,
      a.set_system_id && `set system ${systemName(a.set_system_id)}`,
      a.add_tags?.length && `tag ${a.add_tags.join(", ")}`,
      a.set_assignee_id && "assign",
    ].filter(Boolean);
    return `When ${when.join(" and ") || "any ticket arrives"} → ${then.join(", ") || "do nothing"}`;
  }

  if (rules.error) return <ErrorState message={rules.error} retry={rules.reload} />;
  if (!rules.data) return <Spinner />;
  const priorities = ["low", "normal", "high", "urgent"];
  return (
    <section className="stack">
      <form className="card panel form" onSubmit={(e) => void create(e)}>
        <h2>New rule</h2>
        <div className="form-row">
          <Field label="Name">
            <input name="name" required maxLength={120} />
          </Field>
          <Field label="Order" hint="Lower runs first.">
            <input name="position" type="number" min={0} max={100000} defaultValue={100} />
          </Field>
        </div>
        <div className="form-row">
          <Field label="When category is">
            <select name="category" defaultValue="">
              <option value="">Any</option>
              {config.categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
          <Field label="and priority is">
            <select name="priority" defaultValue="">
              <option value="">Any</option>
              {priorities.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </Field>
          <Field label="and system is">
            <select name="system" defaultValue="">
              <option value="">Any</option>
              {(systems.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="and subject contains">
            <input name="subject_text" maxLength={200} />
          </Field>
        </div>
        <div className="form-row">
          <Field label="Route to team">
            <select name="team" defaultValue="">
              <option value="">No change</option>
              {(teams.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Set priority">
            <select name="set_priority" defaultValue="">
              <option value="">No change</option>
              {priorities.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </Field>
          <Field label="Set system">
            <select name="set_system" defaultValue="">
              <option value="">No change</option>
              {(systems.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Add tag">
            <input name="tag" maxLength={40} pattern="[\w.\-]+" />
          </Field>
        </div>
        <div>
          <button className="btn btn-primary">Add rule</button>
        </div>
      </form>
      {rules.data.length === 0 && <Empty title="No automation rules" hint="Rules run after AI classification of each new ticket." />}
      {rules.data.map((rule) => (
        <div key={rule.id} className="card panel">
          <header className="panel-head">
            <h2>{rule.name}</h2>
            <Chip>#{rule.position}</Chip>
            <button className="btn btn-quiet" onClick={() => void run(async () => { await api.delete(`/rules/${rule.id}`); rules.reload(); }, "Rule deleted")}>
              Delete
            </button>
          </header>
          <p>{describe(rule)}</p>
        </div>
      ))}
    </section>
  );
}

export function AdminPage() {
  const { can } = useApp();
  const tabs = [
    { id: "teams", label: "Teams", allowed: can("team:manage"), view: <Teams /> },
    { id: "systems", label: "Systems", allowed: can("system:manage"), view: <Systems /> },
    { id: "sources", label: "Channels", allowed: can("source:manage"), view: <Sources /> },
    { id: "rules", label: "Automation", allowed: can("rule:manage"), view: <Rules /> },
  ].filter((t) => t.allowed);
  const [active, setActive] = useState(tabs[0]?.id);
  return (
    <>
      <PageHead title="Administration" sub="Roles are assigned in Microsoft Entra ID, not here." />
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.id} role="tab" aria-selected={active === t.id} className={`tab${active === t.id ? " tab-active" : ""}`} onClick={() => setActive(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tabs.find((t) => t.id === active)?.view}
    </>
  );
}
