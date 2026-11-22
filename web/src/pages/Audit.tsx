import { useState } from "react";
import { Compass } from "../brand";
import { useApp, useLoad } from "../context";
import { query } from "../lib";
import type { AuditEvent } from "../types";
import { Chip, Empty, ErrorState, PageHead, Spinner } from "../ui";

interface Page {
  items: AuditEvent[];
  next_before_seq: number | null;
}
interface Verify {
  ok: boolean;
  checked: number;
  first_bad_seq: number | null;
  archived_through: number;
}

export function AuditPage() {
  const { api, notify } = useApp();
  const [filters, setFilters] = useState({ actor_id: "", action: "", resource_id: "", outcome: "" });
  const [before, setBefore] = useState<number[]>([]);
  const [verify, setVerify] = useState<Verify | null>(null);
  const cursor = before[before.length - 1];
  const page = useLoad(
    () => api.get<Page>(`/audit/events${query({ ...filters, before_seq: cursor, limit: 50 })}`),
    [filters, cursor],
  );

  function setFilter(name: keyof typeof filters, value: string) {
    setFilters((f) => ({ ...f, [name]: value.trim() }));
    setBefore([]);
  }

  async function runVerify() {
    try {
      setVerify(await api.post<Verify>("/audit/verify"));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Verification failed", "error");
    }
  }

  return (
    <>
      <PageHead
        title="Audit trail"
        sub="Append-only, hash-chained record of every access and change."
        actions={
          <button className="btn" onClick={() => void runVerify()}>
            Verify integrity
          </button>
        }
      />
      {verify && (
        <p className={verify.ok ? "success-banner" : "danger-banner"} role="status">
          {verify.ok
            ? `Chain intact: ${verify.checked} events verified.${
                verify.archived_through ? ` Events 1–${verify.archived_through} are archived, vouched for by signed checkpoints.` : ""
              }`
            : `Tampering detected at event #${verify.first_bad_seq}. Follow the incident runbook.`}
        </p>
      )}
      <div className="toolbar">
        <input className="search" placeholder="Actor ID" aria-label="Actor ID" onChange={(e) => setFilter("actor_id", e.target.value)} />
        <input className="search" placeholder="Action, e.g. ticket.read" aria-label="Action" onChange={(e) => setFilter("action", e.target.value)} />
        <input className="search" placeholder="Resource ID" aria-label="Resource ID" onChange={(e) => setFilter("resource_id", e.target.value)} />
        <select aria-label="Outcome" onChange={(e) => setFilter("outcome", e.target.value)}>
          <option value="">Any outcome</option>
          <option value="success">Success</option>
          <option value="denied">Denied</option>
          <option value="failure">Failure</option>
        </select>
      </div>
      {page.error ? (
        <ErrorState message={page.error} retry={page.reload} />
      ) : !page.data ? (
        <Spinner />
      ) : page.data.items.length === 0 ? (
        <Empty art={<Compass />} title="No events match" hint="Widen the filters, or check the actor and resource IDs." />
      ) : (
        <div className="card table-card audit-table">
          <table className="table table-dense">
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Resource</th>
                <th>Outcome</th>
                <th>Via</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((e) => (
                <tr key={e.seq} className="row-static">
                  <td className="muted">{e.seq}</td>
                  <td className="nowrap">{new Date(e.occurred_at).toLocaleString()}</td>
                  <td>
                    <code>{e.actor_id}</code>
                  </td>
                  <td>
                    <code>{e.action}</code>
                  </td>
                  <td>
                    {e.resource_type}
                    {e.resource_id && <code> {e.resource_id}</code>}
                  </td>
                  <td>
                    <Chip tone={e.outcome === "success" ? "ok" : "danger"}>{e.outcome}</Chip>
                  </td>
                  <td>{e.channel}</td>
                  <td>
                    <code className="details">{JSON.stringify(e.details)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <footer className="pager">
            <button className="btn" disabled={before.length === 0} onClick={() => setBefore((b) => b.slice(0, -1))}>
              Newer
            </button>
            <button
              className="btn"
              disabled={page.data.next_before_seq === null}
              onClick={() => {
                const next = page.data?.next_before_seq;
                if (next) setBefore((b) => [...b, next]);
              }}
            >
              Older
            </button>
          </footer>
        </div>
      )}
    </>
  );
}
