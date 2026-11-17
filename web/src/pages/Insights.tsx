import { useState } from "react";
import { Compass } from "../brand";
import { BarList, LineChart } from "../components/charts";
import { useApp, useLoad } from "../context";
import { channelName, duration, titleCase } from "../lib";
import type { Insights } from "../types";
import { Empty, ErrorState, PageHead, Spinner } from "../ui";

const PERIODS = [7, 30, 90, 365];

function Tile({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: string }) {
  return (
    <div className={`tile${tone ? ` tile-${tone}` : ""}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}

function rate(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
}

function Attainment({ label, target }: { label: string; target: Insights["first_response"] }) {
  const measured = target.met + target.missed;
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{rate(target.rate)}</span>
      <meter min={0} max={1} low={0.8} high={0.95} optimum={1} value={target.rate ?? 0} aria-label={label} />
      <span className="tile-note">{measured ? `${target.met} of ${measured} on time` : "Nothing measured yet"}</span>
    </div>
  );
}

export function InsightsPage() {
  const { api } = useApp();
  const [days, setDays] = useState(30);
  const [table, setTable] = useState(false);
  const data = useLoad(() => api.get<Insights>(`/tickets/insights?days=${days}`), [days]);

  const d = data.data;
  const shortDate = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short" });

  return (
    <>
      <PageHead title="Insights" sub="What came in, where from, and whether we kept our promises. Your teams’ tickets only." />
      <div className="toolbar">
        <div className="tabs" role="tablist" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p} role="tab" aria-selected={days === p} className={`tab${days === p ? " tab-active" : ""}`} onClick={() => setDays(p)}>
              {p === 365 ? "Year" : `${p} days`}
            </button>
          ))}
        </div>
      </div>

      {data.error ? (
        <ErrorState message={data.error} retry={data.reload} />
      ) : !d ? (
        <Spinner />
      ) : d.created === 0 && d.open_now === 0 ? (
        <Empty art={<Compass />} title="Nothing to report yet" hint="Once tickets arrive, this page shows where they come from and how quickly they are handled." />
      ) : (
        <div className={`insights${data.loading ? " is-loading" : ""}`}>
          <section className="tiles" aria-label="Summary">
            <Tile label="Received" value={d.created.toLocaleString()} note={`in the last ${d.days} days`} />
            <Tile label="Resolved" value={d.resolved.toLocaleString()} note={`in the last ${d.days} days`} />
            <Tile
              label="Open now"
              value={d.open_now.toLocaleString()}
              note={d.overdue_now ? `${d.overdue_now} overdue` : d.oldest_open_days !== null ? `oldest ${d.oldest_open_days} days` : undefined}
              tone={d.overdue_now ? "danger" : undefined}
            />
            <Tile label="Waiting on requester" value={d.waiting_on_requester.toLocaleString()} note="clock paused" />
            <Tile label="Median first reply" value={duration(d.median_first_response_minutes)} note="from arrival" />
            <Tile label="Median time to resolve" value={d.median_resolution_hours === null ? "–" : duration(d.median_resolution_hours * 60)} note="waiting time excluded" />
            <Attainment label="Replied on time" target={d.first_response} />
            <Attainment label="Resolved on time" target={d.resolution} />
          </section>

          <section className="card panel chart-card">
            <header className="panel-head">
              <h2>Received and resolved per day</h2>
              <button className="btn btn-quiet" onClick={() => setTable(!table)} aria-pressed={table}>
                {table ? "Show chart" : "Show as table"}
              </button>
            </header>
            {table ? (
              <div className="table-scroll">
                <table className="table table-dense">
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th>Received</th>
                      <th>Resolved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...d.timeline].reverse().map((row) => (
                      <tr key={row.day} className="row-static">
                        <td>{shortDate(row.day)}</td>
                        <td>{row.created}</td>
                        <td>{row.resolved}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <LineChart
                describe={`Tickets received and resolved per day over the last ${d.days} days. ${d.created} received, ${d.resolved} resolved.`}
                labels={d.timeline.map((row) => shortDate(row.day))}
                series={[
                  { key: "in", label: "Received", className: "s-in", values: d.timeline.map((r) => r.created) },
                  { key: "out", label: "Resolved", className: "s-out", values: d.timeline.map((r) => r.resolved) },
                ]}
              />
            )}
          </section>

          <div className="chart-grid">
            <section className="card panel">
              <h2>By system</h2>
              <BarList describe="Tickets received per system" items={d.by_system} />
            </section>
            <section className="card panel">
              <h2>By category</h2>
              <BarList describe="Tickets received per category" items={d.by_category.map((c) => ({ ...c, label: titleCase(c.label) }))} />
            </section>
            <section className="card panel">
              <h2>By channel</h2>
              <BarList describe="Tickets received per channel" items={d.by_channel.map((c) => ({ ...c, label: channelName(c.label) }))} />
            </section>
            <section className="card panel">
              <h2>By priority</h2>
              <BarList describe="Tickets received per priority" items={d.by_priority.map((c) => ({ ...c, label: titleCase(c.label) }))} />
            </section>
          </div>
        </div>
      )}
    </>
  );
}
