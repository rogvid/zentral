// Small pure helpers (unit-tested in lib.test.ts).

export type Route =
  | { page: "inbox" }
  | { page: "ticket"; id: number }
  | { page: "audit" }
  | { page: "privacy" }
  | { page: "admin" };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const ticket = /^tickets\/(\d{1,12})$/.exec(path);
  if (ticket) return { page: "ticket", id: Number(ticket[1]) };
  if (path === "audit" || path === "privacy" || path === "admin") return { page: path };
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
