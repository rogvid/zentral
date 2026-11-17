import { type FormEvent } from "react";
import { useApp, useLoad } from "../../context";
import type { Team, User } from "../../types";
import { Chip, Empty, ErrorState, Field, Spinner } from "../../ui";
import { useAction, useDelete } from "./shared";

export function Teams() {
  const { api } = useApp();
  const run = useAction();
  const remove = useDelete();
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
              onClick={() =>
                void remove(`team “${team.name}”`, "Its tickets return to triage. Members lose access to them.", async () => {
                  await api.delete(`/teams/${team.id}`);
                  teams.reload();
                })
              }
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
