import { useEffect, useState, type FormEvent } from "react";
import { cleanValues, initialValues, SchemaForm, type JsonSchema } from "../../components/SchemaForm";
import { useApp, useLoad } from "../../context";
import { AlertIcon, CheckIcon } from "../../icons";
import { relativeTime, titleCase } from "../../lib";
import type { ConnectorInfo, Diagnostic, Source, SystemInfo, Team } from "../../types";
import { Chip, Dialog, Empty, ErrorState, Field, Spinner } from "../../ui";
import { useAction, useDelete } from "./shared";

const CONNECTOR_HELP: Record<string, string> = {
  email_graph: "A Microsoft 365 shared mailbox. Zentral verifies that its credentials can reach this mailbox only.",
  teams: "People raise issues by @mentioning the service desk in a Teams channel.",
  webhook: "Monitoring systems and applications send issues with one HTTP request.",
};

function Health({ source }: { source: Source }) {
  if (!source.enabled) return <Chip>Disabled</Chip>;
  if (source.last_error) return <Chip tone="danger">Failing</Chip>;
  if (source.last_ok_at) return <Chip tone="ok">Healthy · {relativeTime(source.last_ok_at)}</Chip>;
  return source.capabilities.includes("poll") ? <Chip tone="note">Waiting for first run</Chip> : <Chip tone="ok">Ready</Chip>;
}

function ChannelDialog({
  connectors,
  teams,
  systems,
  existing,
  onClose,
  onSaved,
}: {
  connectors: ConnectorInfo[];
  teams: Team[];
  systems: SystemInfo[];
  existing: Source | null;
  onClose: () => void;
  onSaved: (source: Source) => void;
}) {
  const { api } = useApp();
  const run = useAction();
  const [connectorName, setConnectorName] = useState(existing?.connector ?? connectors[0]?.name ?? "");
  const connector = connectors.find((c) => c.name === connectorName);
  const schema: JsonSchema = connector?.config_schema ?? {};
  const [values, setValues] = useState(() => initialValues(schema, existing?.config));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!existing) setValues(initialValues(schema));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectorName]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const secrets: Record<string, string> = {};
    for (const field of connector?.secret_fields ?? []) secrets[field] = String(data.get(`secret:${field}`) ?? "");
    const common = {
      config: cleanValues(schema, values),
      secrets,
      default_team_id: String(data.get("team") ?? "") || null,
      default_system_id: String(data.get("system") ?? "") || null,
    };
    setBusy(true);
    const ok = await run(async () => {
      const saved = existing
        ? await api.patch<Source>(`/sources/${existing.id}`, common)
        : await api.post<Source>("/sources", { ...common, name: String(data.get("name")), connector: connectorName });
      onSaved(saved);
    }, existing ? "Channel updated" : undefined);
    if (!ok) setBusy(false);
  }

  return (
    <Dialog title={existing ? `Edit ${existing.name}` : "Add channel"} onClose={onClose}>
      <form className="form" onSubmit={(e) => void submit(e)}>
        {!existing && (
          <>
            <Field label="Name">
              <input name="name" required maxLength={120} placeholder="e.g. Service desk mailbox" autoFocus />
            </Field>
            <Field label="Type" hint={CONNECTOR_HELP[connectorName]}>
              <select value={connectorName} onChange={(e) => setConnectorName(e.target.value)}>
                {connectors.map((c) => (
                  <option key={c.name} value={c.name}>
                    {titleCase(c.name)}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}

        {Object.keys(schema.properties ?? {}).length > 0 && (
          <fieldset className="group">
            <legend>Settings</legend>
            <SchemaForm schema={schema} values={values} onChange={setValues} />
          </fieldset>
        )}

        {(connector?.secret_fields.length ?? 0) > 0 && (
          <fieldset className="group">
            <legend>Credentials</legend>
            <p className="field-hint">
              Encrypted before they are stored and never shown again.
              {existing && " Leave a field empty to keep the stored value."}
            </p>
            {connector?.secret_fields.map((field) => (
              <Field key={field} label={titleCase(field) + (connector.required_secrets.includes(field) ? "" : " (optional)")}>
                <input
                  name={`secret:${field}`}
                  type="password"
                  autoComplete="off"
                  required={!existing && connector.required_secrets.includes(field)}
                  placeholder={existing ? "•••••••• (unchanged)" : ""}
                />
              </Field>
            ))}
          </fieldset>
        )}

        <fieldset className="group">
          <legend>Routing</legend>
          <div className="form-row">
            <Field label="Default team">
              <select name="team" defaultValue={existing?.default_team_id ?? ""}>
                <option value="">Triage</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Default system" hint="Applies unless the sender names one.">
              <select name="system" defaultValue={existing?.default_system_id ?? ""}>
                <option value="">None — AI and rules decide</option>
                {systems.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </fieldset>

        <footer className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !connector}>
            {existing ? "Save changes" : "Add channel"}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}

function ChannelCard({ source, onEdit, onChanged, onToken }: { source: Source; onEdit: () => void; onChanged: () => void; onToken: (s: Source) => void }) {
  const { api } = useApp();
  const run = useAction();
  const remove = useDelete();
  const [checks, setChecks] = useState<Diagnostic[] | null>(null);
  const [testing, setTesting] = useState(false);

  async function test() {
    setTesting(true);
    await run(async () => setChecks(await api.post<Diagnostic[]>(`/sources/${source.id}/test`)));
    setTesting(false);
    onChanged();
  }

  return (
    <div className="card panel">
      <header className="panel-head">
        <h2>{source.name}</h2>
        <Chip>{titleCase(source.connector)}</Chip>
        <Health source={source} />
      </header>

      {source.last_error && source.enabled && (
        <p className="danger-banner flush" role="alert">
          <AlertIcon /> {source.last_error.replace(/^\w+Error: /, "")}
          {source.last_error_at && <span className="muted"> · {relativeTime(source.last_error_at)}</span>}
        </p>
      )}

      {typeof source.config.mailbox === "string" && (
        <p className="muted">
          Mailbox <strong>{source.config.mailbox}</strong> · access is verified to be limited to this mailbox before any
          mail is read
        </p>
      )}

      {source.hook_url && (
        <Field label="Webhook URL">
          <input readOnly value={source.hook_url} onFocus={(e) => e.target.select()} />
        </Field>
      )}

      {checks && (
        <ul className="checks" aria-label="Test results">
          {checks.map((check) => (
            <li key={check.name} className={`check-${check.status}`}>
              {check.status === "ok" ? <CheckIcon /> : <AlertIcon />}
              <span>
                <strong>{check.name}</strong>
                <span className="field-hint">{check.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      <footer className="card-actions">
        <button className="btn" onClick={() => void test()} disabled={testing}>
          {testing ? "Testing…" : "Test"}
        </button>
        <button className="btn" onClick={onEdit}>
          Edit
        </button>
        <button
          className="btn"
          onClick={() => void run(async () => { await api.patch(`/sources/${source.id}`, { enabled: !source.enabled }); onChanged(); })}
        >
          {source.enabled ? "Disable" : "Enable"}
        </button>
        {source.has_token && (
          <button
            className="btn"
            onClick={() =>
              void run(async () => onToken(await api.post<Source>(`/sources/${source.id}/rotate-token`)))
            }
          >
            Rotate token
          </button>
        )}
        <button
          className="btn btn-quiet push"
          onClick={() =>
            void remove(`channel “${source.name}”`, "Intake through it stops immediately. Existing tickets are kept.", async () => {
              await api.delete(`/sources/${source.id}`);
              onChanged();
            })
          }
        >
          Delete
        </button>
      </footer>
    </div>
  );
}

export function Channels() {
  const { api } = useApp();
  const sources = useLoad(() => api.get<Source[]>("/sources"), []);
  const connectors = useLoad(() => api.get<ConnectorInfo[]>("/connectors"), []);
  const teams = useLoad(() => api.get<Team[]>("/teams"), []);
  const systems = useLoad(() => api.get<SystemInfo[]>("/systems"), []);
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const [issued, setIssued] = useState<Source | null>(null);

  if (sources.error) return <ErrorState message={sources.error} retry={sources.reload} />;
  if (!sources.data || !connectors.data) return <Spinner />;
  return (
    <section className="stack">
      <div>
        <button className="btn btn-primary" onClick={() => setEditing("new")}>
          Add channel
        </button>
      </div>
      {sources.data.length === 0 && <Empty title="No channels connected" hint="Add e-mail, Teams or a webhook to receive tickets." />}
      {sources.data.map((source) => (
        <ChannelCard key={source.id} source={source} onEdit={() => setEditing(source)} onChanged={sources.reload} onToken={setIssued} />
      ))}

      {editing && (
        <ChannelDialog
          connectors={connectors.data}
          teams={teams.data ?? []}
          systems={systems.data ?? []}
          existing={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            if (saved.ingest_token) setIssued(saved);
            sources.reload();
          }}
        />
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
