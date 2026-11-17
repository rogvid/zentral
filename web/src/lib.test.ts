import { describe, expect, it } from "vitest";
import { channelName, fillTemplate, formatTargets, parseTargets, initials, parseRoute, percent, query, relativeTime, titleCase } from "./lib";

describe("parseRoute", () => {
  it("parses known routes", () => {
    expect(parseRoute("#/tickets/42")).toEqual({ page: "ticket", id: 42 });
    expect(parseRoute("#/audit")).toEqual({ page: "audit" });
    expect(parseRoute("")).toEqual({ page: "inbox" });
  });
  it("falls back to the inbox for anything unexpected", () => {
    expect(parseRoute("#/tickets/abc")).toEqual({ page: "inbox" });
    expect(parseRoute("#/tickets/1/../../admin")).toEqual({ page: "inbox" });
    expect(parseRoute("#javascript:alert(1)")).toEqual({ page: "inbox" });
  });
});

describe("formatting", () => {
  const now = new Date("2026-11-15T12:00:00Z");
  it("formats relative time", () => {
    expect(relativeTime("2026-11-15T11:59:40Z", now)).toBe("just now");
    expect(relativeTime("2026-11-15T11:15:00Z", now)).toBe("45m ago");
    expect(relativeTime("2026-11-15T07:00:00Z", now)).toBe("5h ago");
    expect(relativeTime("2026-11-12T12:00:00Z", now)).toBe("3d ago");
  });
  it("formats labels", () => {
    expect(titleCase("service_request")).toBe("Service request");
    expect(initials("Jane van Doe")).toBe("JD");
    expect(initials("")).toBe("?");
    expect(percent(0.934)).toBe("93%");
    expect(percent(null)).toBe("–");
    expect(channelName("email_graph")).toBe("Microsoft 365 mail");
    expect(channelName("my_plugin")).toBe("My plugin");
  });
  it("builds query strings and encodes values", () => {
    expect(query({ status: "open", q: "a&b=c", empty: "", off: false, n: 0 })).toBe(
      "?status=open&q=a%26b%3Dc&n=0",
    );
    expect(query({})).toBe("");
  });
});

import { base64Url, challengeFor, randomToken } from "./auth";

describe("PKCE helpers", () => {
  it("matches the RFC 7636 appendix B test vector", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });
  it("produces url-safe, unpadded, unpredictable values", () => {
    expect(base64Url(new Uint8Array([251, 255, 254]))).toBe("-__-");
    const a = randomToken(48);
    expect(a).toMatch(/^[A-Za-z0-9_-]{64}$/);
    expect(a).not.toBe(randomToken(48));
  });
});

import { hue } from "./lib";
import { nextTheme } from "./theme";

describe("visual helpers", () => {
  it("gives a name a stable colour slot", () => {
    expect(hue("Jane Doe")).toBe(hue("Jane Doe"));
    expect(hue("Jane Doe")).toBeGreaterThanOrEqual(0);
    expect(hue("Jane Doe")).toBeLessThan(6);
  });
  it("cycles themes system → light → dark → system", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });
});

import { cleanValues, initialValues, parseList } from "./components/SchemaForm";

import { compact, niceMax } from "./components/charts";
import { duration, until } from "./lib";

describe("time and numbers", () => {
  it("expresses durations in human units", () => {
    expect(duration(null)).toBe("–");
    expect(duration(42)).toBe("42 min");
    expect(duration(210)).toBe("3.5 h");
    expect(duration(60 * 60)).toBe("2.5 days");
  });
  it("describes deadlines relative to now", () => {
    const now = new Date("2026-11-17T12:00:00Z");
    expect(until("2026-11-17T12:40:00Z", now)).toEqual({ text: "in 40m", past: false });
    expect(until("2026-11-17T09:00:00Z", now)).toEqual({ text: "3h ago", past: true });
  });
  it("chooses clean axis bounds and compact labels", () => {
    expect([niceMax(0), niceMax(3), niceMax(7), niceMax(12), niceMax(480)]).toEqual([1, 5, 10, 20, 500]);
    expect([compact(950), compact(12_900)]).toEqual(["950", "12.9K"]);
  });
});

describe("schema-driven forms", () => {
  const schema = {
    required: ["mailbox"],
    properties: {
      mailbox: { type: "string" },
      folder: { type: "string", default: "inbox" },
      page_size: { type: "integer", default: 25 },
      send_replies: { type: "boolean", default: true },
      allowed: { type: "array", items: { type: "string" }, default: [] },
      probe: { anyOf: [{ type: "string" }, { type: "null" }], default: null },
    },
  };
  it("starts from stored values over schema defaults", () => {
    expect(initialValues(schema, { folder: "support", send_replies: false })).toEqual({
      mailbox: "", folder: "support", page_size: 25, send_replies: false, allowed: [], probe: "",
    });
  });
  it("omits empty optional values and keeps types", () => {
    const values = { mailbox: "a@b.c", folder: "", page_size: "40", send_replies: false, allowed: ["x.com"], probe: "" };
    expect(cleanValues(schema, values)).toEqual({ mailbox: "a@b.c", page_size: 40, send_replies: false, allowed: ["x.com"] });
  });
  it("parses comma and newline separated lists", () => {
    expect(parseList(" a.com, b.org\n\n c.net ,")).toEqual(["a.com", "b.org", "c.net"]);
  });
});

describe("fillTemplate", () => {
  it("replaces every placeholder, with safe fallbacks", () => {
    const body = "Hi {requester_first_name}, {ticket_key} is fixed. {ticket_key}! — {agent_name} {unknown}";
    expect(fillTemplate(body, { ticketKey: "ZEN-4", requester: "Jane", agent: "Ola" })).toBe(
      "Hi Jane, ZEN-4 is fixed. ZEN-4! — Ola {unknown}",
    );
    expect(fillTemplate("Hi {requester_first_name}, {agent_name}", { ticketKey: "ZEN-4" })).toBe("Hi there, The service desk");
  });
  it("does not interpret replacement patterns", () => {
    expect(fillTemplate("{ticket_key}", { ticketKey: "$&$1" })).toBe("$&$1");
  });
});

describe("targets text", () => {
  it("round-trips", () => {
    const parsed = parseTargets(" urgent = 0.5, HIGH=4 ");
    expect(parsed).toEqual({ urgent: 0.5, high: 4 });
    expect(formatTargets(parsed ?? {})).toBe("urgent=0.5, high=4");
    expect(parseTargets("")).toEqual({});
  });
  it("rejects nonsense", () => {
    for (const bad of ["critical=1", "high=0", "high=-1", "high", "high=1x", "high=9000"]) expect(parseTargets(bad)).toBeNull();
  });
});
