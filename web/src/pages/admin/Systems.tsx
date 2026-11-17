import { type FormEvent } from "react";
import { useApp, useLoad } from "../../context";
import type { SystemInfo, Team } from "../../types";
import { Chip, Empty, ErrorState, Field, Spinner } from "../../ui";
import { useAction, useDelete } from "./shared";

export function Systems() {
  const { api } = useApp();
  const run = useAction();
  const remove = useDelete();
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
              onClick={() =>
                void remove(`system “${s.name}”`, "Tickets about it lose the link. To keep history, retire it instead.", async () => {
                  await api.delete(`/systems/${s.id}`);
                  systems.reload();
                })
              }
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
