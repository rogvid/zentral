import type { ErrorEvent } from "@sentry/react";
import { describe, expect, it } from "vitest";
import { scrubEvent } from "./sentry";

describe("scrubEvent", () => {
  it("keeps the error type and frames and drops everything personal", () => {
    const frames = [{ filename: "https://desk.example/assets/main.js", function: "render", lineno: 1 }];
    const event = {
      type: undefined,
      message: "failed for repro@example.com",
      transaction: "/tickets/4711",
      server_name: "desk",
      request: { url: "https://desk.example/#/tickets/4711?q=chair", headers: { "X-Forwarded-For": "203.0.113.77" } },
      user: { ip_address: "203.0.113.77", email: "repro@example.com" },
      breadcrumbs: [{ category: "ui.input", message: "Margit" }],
      extra: { body: "hunter2" },
      logentry: { message: "Margit" },
      exception: { values: [{ type: "TypeError", value: "cannot read Margit", stacktrace: { frames } }] },
    } as ErrorEvent;

    const scrubbed = scrubEvent(event);
    const sent = JSON.stringify(scrubbed);

    expect(scrubbed.user).toEqual({ geo: {} });
    expect(sent).toContain("TypeError");
    expect(sent).toContain("render");
    for (const value of ["repro@example.com", "203.0.113.77", "Margit", "hunter2", "chair", "4711"]) {
      expect(sent).not.toContain(value);
    }
  });
});
