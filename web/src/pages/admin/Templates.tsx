import { useState, type FormEvent } from "react";
import { useApp, useLoad } from "../../context";
import type { Team, Template } from "../../types";
import { Chip, Empty, ErrorState, Field, Spinner } from "../../ui";
import { useAction, useDelete } from "./shared";

const PLACEHOLDERS = "{requester_first_name}, {ticket_key}, {agent_name}";

function TemplateForm({
  initial,
  teams,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: Template;
  teams: Team[];
  submitLabel: string;
  onSubmit: (data: { name: string; body: string; team_id: string | null }) => Promise<boolean>;
  onCancel?: () => void;
}) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const ok = await onSubmit({
      name: String(data.get("name") ?? "").trim(),
      body: String(data.get("body") ?? ""),
      team_id: String(data.get("team") ?? "") || null,
    });
    if (ok && !initial) form.reset();
  }
  return (
    <form className="form" onSubmit={(e) => void submit(e)}>
      <div className="form-row">
        <Field label="Name">
          <input name="name" required maxLength={120} defaultValue={initial?.name} placeholder="e.g. Password reset done" />
        </Field>
        <Field label="Available to" hint="A team's replies are offered only to its members.">
          <select name="team" defaultValue={initial?.team_id ?? ""}>
            <option value="">Everyone</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Text" hint={`Placeholders: ${PLACEHOLDERS}. Agents always see and can edit the text before sending.`}>
        <textarea name="body" required rows={5} maxLength={20000} defaultValue={initial?.body} />
      </Field>
      <div className="form-actions-left">
        <button className="btn btn-primary">{submitLabel}</button>
        {onCancel && (
          <button type="button" className="btn btn-quiet" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

export function Templates() {
  const { api } = useApp();
  const run = useAction();
  const remove = useDelete();
  const templates = useLoad(() => api.get<Template[]>("/templates"), []);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const [editing, setEditing] = useState<string | null>(null);

  if (templates.error) return <ErrorState message={templates.error} retry={templates.reload} />;
  if (!templates.data) return <Spinner />;
  return (
    <section className="stack">
      <div className="card panel">
        <h2>New saved reply</h2>
        <TemplateForm
          teams={teams.data ?? []}
          submitLabel="Add saved reply"
          onSubmit={(data) =>
            run(async () => {
              await api.post("/templates", data);
              templates.reload();
            }, "Saved reply added")
          }
        />
      </div>
      {templates.data.length === 0 && (
        <Empty title="No saved replies yet" hint="Write the answers your desk gives every week once, so everyone gives them the same way." />
      )}
      {templates.data.map((t) => (
        <div key={t.id} className="card panel">
          {editing === t.id ? (
            <TemplateForm
              initial={t}
              teams={teams.data ?? []}
              submitLabel="Save"
              onCancel={() => setEditing(null)}
              onSubmit={(data) =>
                run(async () => {
                  await api.patch(`/templates/${t.id}`, data);
                  setEditing(null);
                  templates.reload();
                }, "Saved")
              }
            />
          ) : (
            <>
              <header className="panel-head">
                <h2>{t.name}</h2>
                <Chip>{t.team_name ?? "Everyone"}</Chip>
                <button className="btn btn-quiet" onClick={() => setEditing(t.id)}>
                  Edit
                </button>
                <button
                  className="btn btn-quiet"
                  onClick={() =>
                    void remove(`saved reply “${t.name}”`, "Agents will no longer be offered it.", async () => {
                      await api.delete(`/templates/${t.id}`);
                      templates.reload();
                    })
                  }
                >
                  Delete
                </button>
              </header>
              <p className="template-body">{t.body}</p>
            </>
          )}
        </div>
      ))}
    </section>
  );
}
