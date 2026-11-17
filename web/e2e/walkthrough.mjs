// End-to-end walkthrough of the web client against a running development
// instance (see scripts/e2e.sh). It signs in as each persona through the real
// OpenID Connect flow and exercises the main journeys.
//
// It fails on: any uncaught page error, any console error (which includes
// Content-Security-Policy violations), any unexpected HTTP error, and any
// journey step that does not reach its expected state.
//
//   BASE_URL=http://127.0.0.1:8000 node web/e2e/walkthrough.mjs [screenshot-dir]

import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8000";
const SHOTS = process.argv[2];
const problems = [];
const expectedErrors = []; // {status, urlPart} that a step deliberately provokes
const tolerated = new Set(); // statuses whose console echo is therefore expected

function expectError(status, ...urlParts) {
  tolerated.add(status);
  for (const urlPart of urlParts) expectedErrors.push({ status, urlPart });
}

function check(condition, message) {
  if (!condition) problems.push(message);
}

async function shot(page, name) {
  if (!SHOTS) return;
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

async function as(browser, persona, steps, viewport = { width: 1380, height: 900 }) {
  const context = await browser.newContext({ viewport, colorScheme: "light" });
  const page = await context.newPage();
  page.on("pageerror", (e) => problems.push(`[${persona}] page error: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    // The browser also logs a console line for every failed request we provoke on purpose.
    if ([...tolerated].some((status) => text.includes(`status of ${status}`))) return;
    problems.push(`[${persona}] console: ${text}`);
  });
  page.on("response", (r) => {
    if (r.status() < 400 || r.url().includes("favicon")) return;
    const index = expectedErrors.findIndex((e) => e.status === r.status() && r.url().endsWith(e.urlPart));
    if (index >= 0) expectedErrors.splice(index, 1);
    else problems.push(`[${persona}] HTTP ${r.status()} ${r.url()}`);
  });
  await page.goto(`${BASE}/`);
  await page.getByRole("link", { name: new RegExp(`\\(${persona}\\)`) }).click();
  await page.waitForSelector(".sidebar");
  try {
    await steps(page);
  } catch (e) {
    problems.push(`[${persona}] step failed: ${String(e.message).split("\n")[0]}`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/FAILED-${persona}.png` }).catch(() => {});
  }
  await context.close();
}

const go = (page, hash) => page.goto(`${BASE}/#/${hash}`);
const themeButton = (page) => page.getByRole("button", { name: /Switch theme/ });

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
);

// --- agent: the daily work ------------------------------------------------------------------
await as(browser, "agent", async (page) => {
  await page.waitForSelector(".table tbody tr");
  await page.waitForFunction(() => document.querySelector(".stat-value")?.textContent !== "–");
  check((await page.title()) === "Inbox · Zentral", "inbox page title");

  await page.keyboard.press("/");
  check((await page.evaluate(() => document.activeElement?.getAttribute("type"))) === "search", '"/" focuses search');

  await page.getByRole("link", { name: /Cannot sign in/ }).click();
  await page.waitForSelector(".thread .msg");
  check(/ZEN-\d+ · Zentral/.test(await page.title()), "ticket page title");

  // Hostile text must be displayed, never executed or interpreted.
  const hostile = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>';
  await page.getByRole("tab", { name: "Internal note" }).click();
  await page.getByLabel("Internal note").fill(hostile);
  await page.keyboard.press("Control+Enter"); // keyboard shortcut sends
  await page.locator(".thread .msg-body", { hasText: hostile }).waitFor();
  check((await page.evaluate(() => window.__xss)) === undefined, "ticket content was executed as script");

  // A draft survives navigating away and back (kept in memory only).
  await page.getByLabel("Internal note").fill("half-written thought");
  await page.getByRole("link", { name: "← Inbox" }).click();
  await page.waitForSelector(".table tbody tr");
  await page.getByRole("link", { name: /Cannot sign in/ }).click();
  await page.waitForSelector(".thread .msg");
  await page.getByRole("tab", { name: "Internal note" }).click();
  const draft = await page.getByLabel("Internal note").inputValue();
  check(draft === "half-written thought", `draft kept across navigation (found ${JSON.stringify(draft)})`);
  check((await page.evaluate(() => JSON.stringify(sessionStorage) + JSON.stringify(localStorage))).includes("half-written") === false, "draft leaked into browser storage");
  await page.getByLabel("Internal note").fill("");

  // A saved reply fills in the ticket's details and stays editable before sending.
  const signInKey = (await page.locator(".ticket-key").innerText()).trim();
  await page.getByRole("tab", { name: "Reply to requester" }).click();
  await page.getByLabel("Insert a saved reply").selectOption({ label: "Account unlocked" });
  const filled = await page.getByLabel("Reply", { exact: true }).inputValue();
  check(filled.includes(signInKey) && filled.startsWith("Hi Jane,") && !filled.includes("{"), `saved reply placeholders filled (got ${JSON.stringify(filled.slice(0, 60))})`);
  await page.getByLabel("Reply", { exact: true }).fill("");

  await page.getByLabel("Add a tag").fill("vip-customer");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.locator(".tags .chip", { hasText: "vip-customer" }).waitFor();
  await page.getByLabel("Status").selectOption("pending");
  await page.locator(".ticket-meta .status-pending").waitFor();
  await page.locator(".timeline li", { hasText: "changed status → pending" }).waitFor();
  await page.locator(".timeline li", { hasText: "added an internal note" }).first().waitFor();
  await shot(page, "ticket");

  await go(page, "");
  await page.getByRole("button", { name: "New ticket" }).click();
  await page.getByLabel("Subject").fill("Printer on 3rd floor is jammed");
  await page.getByLabel("Description").fill("Paper jam in tray 2, printer shows error E-204.");
  await page.getByRole("button", { name: "Create ticket" }).click();
  await page.getByRole("heading", { name: "Printer on 3rd floor is jammed" }).waitFor();

  // Merge it into another ticket: the duplicate closes and points to where it went.
  await page.getByText("Merge a duplicate").click();
  await page.getByLabel("Ticket to merge into").fill(signInKey);
  await page.locator(".merge").getByRole("button", { name: "Merge" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Merge" }).click();
  await page.locator(".ticket-key", { hasText: signInKey }).waitFor();
  await page.locator(".thread .msg-body", { hasText: "was merged into this ticket" }).waitFor();

  // Role boundaries are visible in the UI…
  await go(page, "admin");
  await page.getByText("does not include access").waitFor();
  check((await page.getByRole("link", { name: "Administration" }).count()) === 0, "agent sees admin navigation");
  // …and a ticket outside the agent's teams looks like it does not exist.
  expectError(404, "/tickets/3", "/tickets/3/messages");
  await go(page, "tickets/3");
  await page.getByText("ticket not found").waitFor();
});

// --- admin: configuration ----------------------------------------------------------------------
await as(browser, "admin", async (page) => {
  await page.waitForSelector(".table tbody tr");
  await page.waitForFunction(() => document.querySelector(".stat-value")?.textContent !== "–");
  await shot(page, "inbox");

  await themeButton(page).click();
  await themeButton(page).click();
  check((await page.evaluate(() => document.documentElement.dataset.theme)) === "dark", "theme switch reaches dark");
  await shot(page, "inbox-dark");
  await page.getByRole("button", { name: /Overdue/ }).click();
  await page
    .waitForFunction(
      () => {
        const cells = [...document.querySelectorAll(".table tbody tr td:last-child .due")];
        return cells.length > 0 && cells.every((cell) => /overdue/i.test(cell.textContent ?? ""));
      },
      undefined,
      { timeout: 10000 },
    )
    .catch(() => undefined);
  const dues = await page.locator(".table tbody tr td:last-child .due, .table tbody tr td:last-child > .muted").allInnerTexts();
  check(dues.every((text) => /overdue/i.test(text)), `overdue filter shows only overdue tickets (saw ${JSON.stringify([...new Set(dues)])})`);
  await page.getByRole("button", { name: /Active/ }).click();
  await go(page, "tickets/2");
  await page.waitForSelector(".thread .msg");
  await shot(page, "ticket-dark");
  await page.reload();
  await page.waitForSelector(".thread .msg");
  check((await page.evaluate(() => document.documentElement.dataset.theme)) === "dark", "theme persists across reload");
  await themeButton(page).click();
  await themeButton(page).click();

  await go(page, "admin");
  await page.getByRole("tab", { name: "Systems" }).click();
  await page.getByText("Payment platform").first().waitFor();
  const payments = page.locator(".card", { hasText: "Payment platform" });
  await payments.getByRole("button", { name: "Change" }).click();
  await payments.getByLabel("Resolution (hours)").fill("urgent=4, high=12");
  await payments.getByRole("button", { name: "Save targets" }).click();
  await payments.getByText(/Own targets .* resolution: urgent=4, high=12/).waitFor();
  await shot(page, "admin-systems");

  // A mail channel through the generated form.
  await page.getByRole("tab", { name: "Channels" }).click();
  await page.getByRole("button", { name: "Add channel" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Service desk mailbox");
  await page.getByLabel("Type").selectOption({ label: "Microsoft 365 mail" });
  await page.getByLabel("Mailbox", { exact: true }).fill("servicedesk@example.com");
  await page.getByLabel(/Allowed sender domains/).fill("example.com, partner.example.org");
  await page.getByLabel("Tenant id").fill("00000000-0000-0000-0000-000000000000");
  await page.getByLabel("Client id").fill("11111111-1111-1111-1111-111111111111");
  await page.getByLabel("Client secret").fill("not-a-real-secret");
  await shot(page, "admin-channel-form");
  await page.getByRole("dialog").getByRole("button", { name: "Add channel" }).click();
  const card = page.locator(".card", { hasText: "Service desk mailbox" });
  await card.waitFor();

  // The self-test tells the truth: these credentials are not real.
  await card.getByRole("button", { name: "Test" }).click();
  await card.locator(".checks li.check-fail", { hasText: "Sign in to Microsoft" }).waitFor({ timeout: 40000 });
  await shot(page, "admin-channel-test");

  // Editing shows stored settings and never the secrets.
  await card.getByRole("button", { name: "Edit" }).click();
  check((await page.getByLabel("Mailbox", { exact: true }).inputValue()) === "servicedesk@example.com", "edit form shows stored config");
  check((await page.getByLabel("Client secret").inputValue()) === "", "edit form leaked a stored secret");
  check((await page.content()).includes("not-a-real-secret") === false, "secret present in page");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();

  // Destructive actions ask first.
  await card.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  check((await card.count()) === 1, "cancelled delete still deleted");
  await card.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await card.waitFor({ state: "detached" });

  await page.getByRole("tab", { name: "Saved replies" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Thanks for waiting");
  await page.getByLabel("Text").fill("Hi {requester_first_name}, thanks for your patience with {ticket_key}.");
  await page.getByRole("button", { name: "Add saved reply" }).click();
  await page.locator(".card", { hasText: "Thanks for waiting" }).getByRole("button", { name: "Edit" }).waitFor();

  await page.getByRole("tab", { name: "Automation" }).click();
  const rule = page.locator(".card", { hasText: "Infrastructure alerts" });
  await rule.getByRole("button", { name: "Turn off" }).click();
  await rule.getByRole("button", { name: "Turn on" }).waitFor();
  await rule.getByRole("button", { name: "Turn on" }).click();
  await rule.getByRole("button", { name: "Turn off" }).waitFor();

  // Insights: a chart with a keyboard-reachable crosshair, and the same data as a table.
  await go(page, "insights");
  await page.locator(".chart-svg path.series-line").first().waitFor();
  check((await page.locator(".tile").count()) === 8, "insights shows its eight figures");
  await page.locator(".chart-svg[role=img]").first().focus();
  await page.keyboard.press("ArrowLeft");
  await page.locator(".tooltip").waitFor();
  await page.locator(".bar-mark").first().waitFor();
  await shot(page, "insights");
  await page.getByRole("button", { name: "Show as table" }).click();
  check((await page.locator(".table-scroll tbody tr").count()) >= 30, "insights table view lists every day");
  await page.getByRole("button", { name: "Show chart" }).click();

  await go(page, "audit");
  await page.waitForSelector(".table tbody tr");
  await page.getByRole("button", { name: "Verify integrity" }).click();
  await page.locator(".success-banner").waitFor();
  await shot(page, "audit");
});

// --- AI agent role: masked view ---------------------------------------------------------------
await as(browser, "automation", async (page) => {
  await page.waitForSelector(".table tbody tr");
  await page.locator(".table tbody tr", { hasText: "Invoice" }).click();
  await page.waitForSelector(".thread .msg");
  const text = await page.locator("body").innerText();
  check(!text.includes("billing@customer.example") && !text.includes("Accounts Payable"), "masked role saw requester identity");
  await page.getByText("Personal data is masked").waitFor();
  check((await page.getByRole("tab", { name: "Reply to requester" }).count()) === 0, "masked role offered a reply box");
  await shot(page, "agent-view-masked");
});

// --- privacy officer: access and erasure ---------------------------------------------------------
await as(browser, "privacy", async (page) => {
  await go(page, "privacy");
  await page.getByLabel(/e-mail address/).fill("lena.berg@example.com");
  await page.getByRole("button", { name: "Find person" }).click();
  await page.getByRole("button", { name: /Export data/ }).waitFor();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /Export data/ }).click()]);
  check(download.suggestedFilename().startsWith("data-subject-export-"), "export file name");

  await page.getByRole("button", { name: "Record objection to AI processing" }).click();
  await page.getByText(/excluded from AI triage/).waitFor();
  await page.getByRole("button", { name: "Withdraw AI objection" }).click();
  await page.getByText(/Allowed \(personal data is masked first\)/).waitFor();

  await page.getByRole("button", { name: /Erase personal data/ }).click();
  const confirm = page.getByRole("dialog").getByRole("button", { name: "Erase permanently" });
  check(await confirm.isDisabled(), "erase enabled before typing ERASE");
  await page.getByLabel("Type ERASE to confirm").fill("ERASE");
  await confirm.click();
  await page.getByText(/Erased: 1 tickets/).waitFor();
  expectError(404, "/privacy/contacts/lookup");
  await page.getByLabel(/e-mail address/).fill("lena.berg@example.com");
  await page.getByRole("button", { name: "Find person" }).click();
  await page.getByText("No data held").waitFor();
});

// --- auditor: trail only -------------------------------------------------------------------------
await as(browser, "auditor", async (page) => {
  await page.waitForSelector(".table tbody tr"); // lands on the audit trail
  check((await page.getByRole("link", { name: "Inbox" }).count()) === 0, "auditor sees the inbox");
  await page.getByLabel("Action").fill("privacy.erased");
  await page.waitForFunction(() => document.querySelectorAll(".table tbody tr").length === 1);
});

// --- small screen, dark ---------------------------------------------------------------------------
await as(
  browser,
  "agent",
  async (page) => {
    await page.waitForSelector(".table tbody tr");
    await themeButton(page).click();
    await themeButton(page).click();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "page scrolls sideways on a phone");
    await shot(page, "mobile-dark");
  },
  { width: 390, height: 844 },
);

await browser.close();
if (problems.length) {
  console.error(`${problems.length} problem(s):\n` + problems.map((p) => ` - ${p}`).join("\n"));
  process.exit(1);
}
console.log("walkthrough passed: 6 personas, no console errors, no CSP violations, no unexpected HTTP errors");
