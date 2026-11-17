import { type FormEvent } from "react";
import { useApp, useLoad } from "../../context";
import type { Rule, SystemInfo, Team } from "../../types";
import { Chip, Empty, ErrorState, Field, Spinner } from "../../ui";
import { useAction, useDelete } from "./shared";

export function Rules() {
  const { api, config } = useApp();
  const run = useAction();
  const remove = useDelete();
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
            <Chip tone={rule.enabled ? "ok" : "danger"}>{rule.enabled ? "On" : "Off"}</Chip>
            <button
              className="btn btn-quiet"
              onClick={() =>
                void run(async () => {
                  await api.put(`/rules/${rule.id}`, {
                    name: rule.name,
                    position: rule.position,
                    enabled: !rule.enabled,
                    conditions: rule.conditions,
                    actions: rule.actions,
                  });
                  rules.reload();
                })
              }
            >
              {rule.enabled ? "Turn off" : "Turn on"}
            </button>
            <button
              className="btn btn-quiet"
              onClick={() =>
                void remove(`rule “${rule.name}”`, "New tickets will no longer be handled by this rule.", async () => {
                  await api.delete(`/rules/${rule.id}`);
                  rules.reload();
                })
              }
            >
              Delete
            </button>
          </header>
          <p>{describe(rule)}</p>
        </div>
      ))}
    </section>
  );
}
