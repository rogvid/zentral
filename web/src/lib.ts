// Small pure helpers (unit-tested in lib.test.ts).

export type Route =
  | { page: "inbox" }
  | { page: "ticket"; id: number }
  | { page: "audit" }
  | { page: "insights" }
  | { page: "privacy" }
  | { page: "admin" };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const ticket = /^tickets\/(\d{1,12})$/.exec(path);
  if (ticket) return { page: "ticket", id: Number(ticket[1]) };
  if (path === "audit" || path === "privacy" || path === "admin" || path === "insights") return { page: path };
  return { page: "inbox" };
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function titleCase(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const CONNECTOR_NAMES: Record<string, string> = {
  email_graph: "Microsoft 365 mail",
  teams: "Microsoft Teams",
  webhook: "Webhook",
  slack: "Slack",
  manual: "Created by hand",
  mcp: "AI agent",
  portal: "Requester portal",
};

/** The human name for a channel type ("email_graph" → "Microsoft 365 mail"). */
export function channelName(name: string): string {
  return CONNECTOR_NAMES[name] ?? titleCase(name);
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Stable colour slot (0–5) for a name, so the same person always gets the same avatar colour. */
export function hue(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return hash % 6;
}

/** "12 min", "3.5 h", "2.4 days" — a duration in the unit a person would use. */
export function duration(minutes: number | null): string {
  if (minutes === null) return "–";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const h = minutes / 60;
  if (h < 48) return `${Math.round(h * 10) / 10} h`;
  return `${Math.round((h / 24) * 10) / 10} days`;
}

/** "in 40 min", "2 h ago" — relative to now, for deadlines. */
export function until(iso: string, now: Date = new Date()): { text: string; past: boolean } {
  const minutes = (new Date(iso).getTime() - now.getTime()) / 60000;
  const span = duration(Math.abs(minutes)).replace(" days", "d").replace(" h", "h").replace(" min", "m");
  return minutes >= 0 ? { text: `in ${span}`, past: false } : { text: `${span} ago`, past: true };
}

export function percent(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
}

/** Build a query string, skipping empty values. */
export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      search.append(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/**
 * Fill a saved reply's placeholders. Plain string replacement only: a template
 * is administrator text and must not be able to reach anything else.
 */
export function fillTemplate(body: string, values: { ticketKey: string; requester?: string | null; agent?: string | null }): string {
  return body
    .split("{ticket_key}")
    .join(values.ticketKey)
    .split("{requester_first_name}")
    .join(values.requester || "there")
    .split("{agent_name}")
    .join(values.agent || "The service desk");
}

/** "urgent=1, high=4" ⇄ {urgent: 1, high: 4} for per-system target overrides. */
export function formatTargets(targets: Record<string, number>): string {
  return Object.entries(targets)
    .map(([priority, hours]) => `${priority}=${hours}`)
    .join(", ");
}

export function parseTargets(text: string): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const part of text.split(",").map((p) => p.trim()).filter(Boolean)) {
    const match = /^(low|normal|high|urgent)\s*=\s*(\d+(?:\.\d+)?)$/i.exec(part);
    if (!match) return null;
    const hours = Number(match[2]);
    if (!(hours > 0 && hours <= 8760)) return null;
    out[(match[1] ?? "").toLowerCase()] = hours;
  }
  return out;
}
