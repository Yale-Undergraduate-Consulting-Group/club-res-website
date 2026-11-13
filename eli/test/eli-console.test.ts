import fs from "node:fs/promises";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { consoleReplyFor, helpText, isConsoleRequest } from "../src/eli/commands.js";
import { loadEliConfig } from "../src/eli/config.js";
import { COOKIE_NAME, LINK_TTL_MS, UsedLinks, makeToken, readToken } from "../src/eli/console/auth.js";
import { NOT_RECORDED, buildDashboard } from "../src/eli/console/data.js";
import { dashboardPage } from "../src/eli/console/page.js";
import { type ConsoleDeps, parseDays, signInLink, startConsole } from "../src/eli/console/server.js";
import type { UsageRecord } from "../src/eli/limits.js";
import { classifyTopic } from "../src/eli/topics.js";

const SECRET = "s".repeat(40);
const NOW = Date.parse("2026-11-12T20:00:00Z");
const usage = { input_tokens: 1000, output_tokens: 100, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 };
const rec = (over: Partial<UsageRecord>): UsageRecord => ({
  at: new Date(NOW - 60_000).toISOString(),
  user: "USAM0001",
  channel: "D1",
  model: "claude-haiku-5-5",
  usage,
  estimatedUsd: 0.001,
  tools: [],
  ok: true,
  ...over,
});

describe("topics", () => {
  it("labels requests from the tools used and the wording, without keeping the text", () => {
    expect(classifyTopic("what's in this folder?", ["sharepoint_open_link"])).toBe("Past projects & files");
    expect(classifyTopic("see https://acme.sharepoint.com/x", [])).toBe("Past projects & files");
    expect(classifyTopic("remind me Friday", [])).toBe("Reminders");
    expect(classifyTopic("latest news on Acme", ["web_search"])).toBe("Web research");
    expect(classifyTopic("summarize this thread", [])).toBe("Summaries");
    expect(classifyTopic("draft a cold email to a fintech", [])).toBe("Drafting");
    expect(classifyTopic("should we raise prices?", [])).toBe("Analysis & advice");
    expect(classifyTopic("hi", [])).toBe("Other questions");
  });
});

describe("console sign-in tokens", () => {
  it("round-trips, and rejects tampering, the wrong kind and expiry", () => {
    const t = makeToken(SECRET, "link", "UBOARD01", NOW, LINK_TTL_MS);
    expect(readToken(SECRET, "link", t, NOW)?.user).toBe("UBOARD01");
    expect(readToken(SECRET, "session", t, NOW)).toBeUndefined();
    expect(readToken("x".repeat(40), "link", t, NOW)).toBeUndefined();
    expect(readToken(SECRET, "link", t.replace("UBOARD01", "UADMIN01"), NOW)).toBeUndefined();
    expect(readToken(SECRET, "link", t, NOW + LINK_TTL_MS + 1)).toBeUndefined();
    expect(readToken(SECRET, "link", "garbage", NOW)).toBeUndefined();
  });

  it("lets each link be used once", () => {
    const used = new UsedLinks();
    expect(used.use("n1", NOW + 1000, NOW)).toBe(true);
    expect(used.use("n1", NOW + 1000, NOW)).toBe(false);
  });
});

describe("dashboard data", () => {
  it("totals the range, compares with the previous one, and buckets by day", () => {
    const day = 24 * 60 * 60 * 1000;
    const records = [
      rec({ topic: "Drafting" }),
      rec({ user: "UOTHER01", topic: "Web research", tools: ["web_search", "web_search"], webSearches: 2 }),
      rec({ ok: false }), // older line without a topic
      rec({ at: new Date(NOW - 10 * day).toISOString(), topic: "Drafting" }), // previous 7 days
      rec({ at: new Date(NOW + day).toISOString() }), // in the future: ignored
    ];
    const d = buildDashboard(records, { now: new Date(NOW), days: 7, timeZone: "America/New_York" });
    expect(d.totals).toMatchObject({ requests: 3, people: 2, failures: 1, webSearches: 2 });
    expect(d.totals.costUsd).toBeCloseTo(0.003 + 0.02);
    expect(d.previous).toMatchObject({ requests: 1 });
    expect(d.daily).toHaveLength(7);
    expect(d.daily.at(-1)).toMatchObject({ date: "2026-11-12", requests: 3 });
    expect(d.topics).toEqual(
      expect.arrayContaining([{ name: "Drafting", count: 1 }, { name: "Web research", count: 1 }, { name: NOT_RECORDED, count: 1 }]),
    );
    expect(d.features).toEqual([{ name: "Web search", count: 1 }]);
    expect(d.people.map((p) => p.user)).toEqual(["USAM0001", "UOTHER01"]);
    const all = buildDashboard(records, { now: new Date(NOW), days: 0, timeZone: "America/New_York" });
    expect(all.totals.requests).toBe(4);
    expect(all.previous).toBeUndefined();
  });

  it("escapes names and shows a notice that question text isn't stored", () => {
    const d = buildDashboard([rec({ topic: "Drafting" })], { now: new Date(NOW), days: 30, timeZone: "America/New_York" });
    const html = dashboardPage(d, {
      viewerName: "<b>Board</b>",
      names: new Map([["USAM0001", "<script>x</script>"]]),
      timeZone: "America/New_York",
      limits: { userPerHour: 20, workspacePerDay: 300 },
    });
    expect(html).not.toContain("<script>x");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("never stored");
    expect(html).toContain("Show as a table");
  });

  it("only accepts the offered ranges", () => {
    expect(parseDays("7")).toBe(7);
    expect(parseDays("all")).toBe(0);
    expect(parseDays("5")).toBe(30);
    expect(parseDays(null)).toBe(30);
  });
});

describe("console server", () => {
  let close: (() => Promise<void>) | undefined;
  afterEach(async () => {
    await close?.();
    close = undefined;
  });

  async function start(viewers = new Set(["UBOARD01"])) {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "eli-console-"));
    const log = path.join(dir, "usage.log");
    await fs.writeFile(log, [rec({ topic: "Drafting" }), rec({ user: "UOTHER01", topic: "Summaries" })].map((r) => JSON.stringify(r)).join("\n"));
    const deps: ConsoleDeps = {
      secret: SECRET,
      publicUrl: "http://localhost:8787",
      viewers,
      usageLogPath: log,
      timeZone: "America/New_York",
      limits: { userPerHour: 20, workspacePerDay: 300 },
      names: async (ids) => new Map(ids.filter((id) => id === "UBOARD01" || id === "USAM0001").map((id) => [id, id === "UBOARD01" ? "Bo Ard" : "Sam"])),
      log: () => undefined,
      now: () => NOW,
    };
    const server = await startConsole(deps, { port: 0, host: "127.0.0.1" });
    close = () => new Promise((r) => server.close(() => r()));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const get = (p: string, cookie?: string) => fetch(base + p, { redirect: "manual", headers: cookie ? { cookie } : {} });
    return { deps, base, get };
  }

  it("signs a board member in with a one-time link and shows the dashboard", async () => {
    const { deps, base, get } = await start();
    expect(await (await get("/")).text()).toContain("send Eli the word");
    const link = signInLink(deps, "UBOARD01")!;
    const loginPath = new URL(link).pathname + new URL(link).search;
    const login = await get(loginPath);
    expect(login.status).toBe(303);
    const setCookie = login.headers.get("set-cookie")!;
    expect(setCookie).toMatch(new RegExp(`^${COOKIE_NAME}=.+HttpOnly; SameSite=Lax`));
    expect(login.headers.get("referrer-policy")).toBe("no-referrer");
    expect((await get(loginPath)).status).toBe(401); // used already

    const cookie = setCookie.split(";")[0]!;
    const page = await get("/?days=7", cookie);
    const html = await page.text();
    expect(page.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(page.headers.get("cache-control")).toBe("no-store");
    expect(html).toContain("Signed in as Bo Ard");
    expect(html).toContain("Questions answered");
    expect(html).toContain("Sam");
    expect(html).toContain("UOTHER01"); // unresolved names fall back to the id
    expect(html).toContain("What people ask about");

    const out = await fetch(new URL("/logout", base), { method: "POST", redirect: "manual" });
    expect(out.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("refuses people who aren't viewers, and drops sessions once someone is removed", async () => {
    const viewers = new Set(["UBOARD01"]);
    const { deps, get } = await start(viewers);
    expect(signInLink(deps, "USAM0001")).toBeUndefined();
    const forged = makeToken(SECRET, "link", "USAM0001", NOW, LINK_TTL_MS);
    expect((await get(`/login?t=${encodeURIComponent(forged)}`)).status).toBe(401);
    const session = makeToken(SECRET, "session", "UBOARD01", NOW, 60_000);
    expect(await (await get("/", `${COOKIE_NAME}=${session}`)).text()).toContain("Signed in as");
    viewers.delete("UBOARD01");
    expect(await (await get("/", `${COOKIE_NAME}=${session}`)).text()).not.toContain("Signed in as");
    expect((await get("/healthz")).status).toBe(200);
    expect((await get("/nope")).status).toBe(404);
  });
});

describe("console access from Slack", () => {
  it("gives viewers a link, everyone else a refusal, and lists /eli-console only for viewers", () => {
    const deps = { consoleLink: (u: string) => (u === "UBOARD01" ? "http://localhost:8787/login?t=abc" : undefined) };
    expect(consoleReplyFor("UBOARD01", deps)).toContain("(http://localhost:8787/login?t=abc)");
    expect(consoleReplyFor("USAM0001", deps)).toContain("only for board members");
    expect(consoleReplyFor("UBOARD01", {})).toContain("isn't turned on");
    const base = { webSearch: false, reminders: false, links: false, requests: false, isAdmin: false };
    expect(helpText({ ...base, console: true })).toContain("/eli-console");
    expect(helpText(base)).not.toContain("/eli-console");
    for (const t of ["console", "Dashboard", "/eli-console"]) expect(isConsoleRequest(t), t).toBe(true);
    expect(isConsoleRequest("console me about the deck")).toBe(false);
  });

  it("validates the console settings", () => {
    const base = { SLACK_BOT_TOKEN: "xoxb-x", SLACK_APP_TOKEN: "xapp-x", ANTHROPIC_API_KEY: "k", ELI_SHAREPOINT: "off" };
    expect(loadEliConfig(base).console).toBeUndefined();
    expect(() => loadEliConfig({ ...base, ELI_CONSOLE: "on" })).toThrow(/ELI_CONSOLE_SECRET is required/);
    expect(() => loadEliConfig({ ...base, ELI_CONSOLE: "on", ELI_CONSOLE_SECRET: SECRET, ELI_CONSOLE_URL: "http://eli.example.org" })).toThrow(/https/);
    const c = loadEliConfig({ ...base, ELI_CONSOLE: "on", ELI_CONSOLE_SECRET: SECRET, ELI_BOARD_USER_IDS: "UBOARD01" });
    expect(c.console).toMatchObject({ publicUrl: "http://localhost:8787", port: 8787, host: "127.0.0.1" });
    expect([...c.boardUserIds]).toEqual(["UBOARD01"]);
    expect(() => loadEliConfig({ ...base, ELI_BOARD_USER_IDS: "bob" })).toThrow(/ELI_BOARD_USER_IDS/);
  });
});
