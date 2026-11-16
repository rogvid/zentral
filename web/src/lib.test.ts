import { describe, expect, it } from "vitest";
import { initials, parseRoute, percent, query, relativeTime, titleCase } from "./lib";

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
