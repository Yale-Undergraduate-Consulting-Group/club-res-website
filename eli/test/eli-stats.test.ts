import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { emptyUsage, type UsageRecord } from "../src/eli/limits.js";
import { formatStats, parseUsageLog, readUsageLog, summarizeUsage, WEB_SEARCH_USD } from "../src/eli/stats.js";
import { handleStatsCommand, parseAdminIds, type StatsRequest } from "../src/eli/statsCommand.js";

// Local time, so "today" means the same calendar day wherever the tests run.
const NOW = new Date(2026, 10, 12, 10, 0, 0);
const DAY = 24 * 60 * 60 * 1000;

function rec(at: Date, over: Partial<UsageRecord> = {}): UsageRecord {
  return {
    at: at.toISOString(),
    user: "USAM0001",
    channel: "C1",
    model: "claude-haiku-5-5",
    servedModel: "claude-haiku-5-5",
    usage: { ...emptyUsage(), input_tokens: 1000, output_tokens: 200 },
    estimatedUsd: 0.0002,
    tools: [],
    ok: true,
    ...over,
  };
}

const win = (s: ReturnType<typeof summarizeUsage>, key: string) => s.windows.find((w) => w.key === key)!;

describe("parseUsageLog / readUsageLog", () => {
  it("keeps valid lines, counts malformed ones and ignores blank lines", () => {
    const good = JSON.stringify(rec(NOW));
    const old = JSON.stringify({ ...rec(NOW), webSearches: undefined }); // older lines have no webSearches
    const text = [good, "", "{not json", JSON.stringify({ at: "yesterday", user: "U1" }), old, "[]", "  "].join("\n");
    const { records, skipped } = parseUsageLog(text);
    expect(records).toHaveLength(2);
    expect(records[0]).toMatchObject({ user: "USAM0001", ok: true, tools: [] });
    expect(records[1]!.webSearches).toBeUndefined();
    expect(skipped).toBe(3);
  });

  it("treats a missing file as no records and reads a real one", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "eli-stats-"));
    try {
      expect(await readUsageLog(path.join(dir, "missing.log"))).toEqual({ records: [], skipped: 0 });
      const file = path.join(dir, "usage.log");
      await fs.writeFile(file, JSON.stringify(rec(NOW)) + "\ngarbage\n");
      expect(await readUsageLog(file)).toMatchObject({ records: [{ user: "USAM0001" }], skipped: 1 });
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });
});

describe("summarizeUsage", () => {
  const midnight = new Date(2026, 10, 12, 0, 0, 0);

  it("splits today (local day), the last 7 days and all time at their exact boundaries", () => {
    const s = summarizeUsage(
      [
        rec(midnight), // today
        rec(new Date(midnight.getTime() - 1)), // yesterday, in the last 7 days
        rec(new Date(NOW.getTime() - 7 * DAY)), // exactly 7 days ago: still in
        rec(new Date(NOW.getTime() - 7 * DAY - 1)), // just outside the 7 days
        rec(new Date(2025, 0, 1)),
        rec(new Date(NOW.getTime() + 1000)), // after now: ignored
      ],
      { now: NOW },
    );
    expect(win(s, "today").requests).toBe(1);
    expect(win(s, "week").requests).toBe(3);
    expect(win(s, "all").requests).toBe(5);
    expect(win(s, "all").label).toBe("All time");
    expect(s.firstAt).toBe(new Date(2025, 0, 1).toISOString());
  });

  it("limits the widest window to `since`", () => {
    const s = summarizeUsage([rec(NOW), rec(new Date(NOW.getTime() - 40 * DAY))], {
      now: NOW,
      since: new Date(NOW.getTime() - 30 * DAY),
      sinceLabel: "Last 30 days",
    });
    expect(win(s, "all")).toMatchObject({ requests: 1, label: "Last 30 days" });
  });

  it("sums tokens, failures, web searches and cost, and ranks users", () => {
    const s = summarizeUsage(
      [
        rec(NOW, { user: "UA", estimatedUsd: 0.5, tools: ["search_sharepoint", "read_file"], webSearches: 3 }),
        rec(NOW, { user: "UA", estimatedUsd: 0.25, tools: ["search_sharepoint"] }),
        rec(NOW, { user: "UB", estimatedUsd: 2, usage: { ...emptyUsage(), input_tokens: 10, cache_read_input_tokens: 90, output_tokens: 5 } }),
        rec(NOW, { user: "UC", ok: false, servedModel: undefined, estimatedUsd: 0, usage: emptyUsage() }),
        rec(NOW, { user: "UD", model: "mystery-model", servedModel: "mystery-model", estimatedUsd: undefined }),
      ],
      { now: NOW },
    );
    const all = win(s, "all");
    expect(all).toMatchObject({ requests: 5, failures: 1, users: 4, webSearches: 3, unpriced: 1 });
    expect(all.estimatedUsd).toBeCloseTo(0.5 + 0.25 + 2 + 3 * WEB_SEARCH_USD, 6);
    expect(all.webSearchUsd).toBeCloseTo(0.03, 6);
    expect(all.inputTokens).toBe(1000 + 1000 + 100 + 0 + 1000);
    expect(all.outputTokens).toBe(200 + 200 + 5 + 0 + 200);
    expect(all.tools).toEqual([{ name: "search_sharepoint", count: 2 }, { name: "read_file", count: 1 }]);
    expect(all.models).toEqual([{ model: "claude-haiku-5-5", requests: 3 }, { model: "mystery-model", requests: 1 }]);
    expect(all.topByRequests[0]).toMatchObject({ user: "UA", requests: 2 });
    expect(all.topByCost.map((u) => u.user)).toEqual(["UB", "UA"]); // users with no cost are left out
    expect(all.topByCost[1]!.estimatedUsd).toBeCloseTo(0.78, 6);
  });
});

describe("formatStats", () => {
  it("uses names when given and mentions otherwise", () => {
    const s = summarizeUsage([rec(NOW, { user: "UA" }), rec(NOW, { user: "UB" })], { now: NOW });
    const out = formatStats(s, { names: new Map([["UA", "Jordan Lee"]]), skipped: 2 });
    expect(out).toContain("**Today:** 2 requests · 2 people");
    expect(out).toContain("Jordan Lee 1");
    expect(out).toContain("<@UB> 1");
    expect(out).toContain("Skipped 2 unreadable lines");
  });

  it("says so when there is nothing logged", () => {
    expect(formatStats(summarizeUsage([], { now: NOW }))).toContain("No requests logged yet.");
  });

  it("stays under 3,500 characters with many users, tools and models", () => {
    const records = Array.from({ length: 2000 }, (_, i) =>
      rec(new Date(NOW.getTime() - (i % 30) * DAY), {
        user: `U${String(i % 400).padStart(10, "0")}`,
        model: `claude-model-with-a-rather-long-name-${i % 7}`,
        servedModel: `claude-model-with-a-rather-long-name-${i % 7}`,
        tools: [`a_tool_with_a_long_descriptive_name_${i % 50}`, `another_tool_${i % 13}`],
        webSearches: i % 4,
        ok: i % 9 !== 0,
        estimatedUsd: (i % 17) / 10,
      }),
    );
    const names = new Map(records.map((r) => [r.user, `Someone With An Extremely Long Display Name ${r.user}`]));
    const out = formatStats(summarizeUsage(records, { now: NOW }), { names, skipped: 12345 });
    expect(out.length).toBeLessThan(3500);
    expect(out).toContain("and 55 more");
  });
});

describe("parseAdminIds", () => {
  it("reads comma-separated U/W ids and allows none", () => {
    expect([...parseAdminIds({ ELI_ADMIN_USER_IDS: " U0123ABCD, W0456EFGH ,," })]).toEqual(["U0123ABCD", "W0456EFGH"]);
    expect(parseAdminIds({}).size).toBe(0);
  });

  it("names every bad id", () => {
    expect(() => parseAdminIds({ ELI_ADMIN_USER_IDS: "U0123ABCD,@alex,c0123" })).toThrow(/"@alex", "c0123" are not Slack user ids/);
  });
});

describe("handleStatsCommand", () => {
  async function run(user: string, opts: { team?: string; text?: string; failNames?: boolean; logPath?: string } = {}) {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "eli-stats-"));
    const usageLogPath = opts.logPath ?? path.join(dir, "usage.log");
    if (!opts.logPath) await fs.writeFile(usageLogPath, [rec(NOW, { user: "UADMIN01" }), rec(NOW, { user: "USAM0001" })].map((r) => JSON.stringify(r)).join("\n"));
    const ack = vi.fn(async () => undefined);
    const respond = vi.fn(async (_m: unknown) => undefined);
    const info = vi.fn(async ({ user }: { user: string }) => {
      if (opts.failNames) throw new Error("users_not_found");
      return { ok: true, user: { profile: { display_name: user === "USAM0001" ? "Sam" : "" }, real_name: "Alex Park" } };
    });
    const req = {
      command: { user_id: user, team_id: opts.team ?? "T1", text: opts.text ?? "" },
      ack,
      respond,
      client: { users: { info } },
    } as unknown as StatsRequest;
    const log = vi.fn();
    try {
      await handleStatsCommand(req, { usageLogPath, adminUserIds: new Set(["UADMIN01"]), allowedTeamId: "T1", log, now: () => NOW });
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
    return { ack, respond, info, log, sent: respond.mock.calls.map((c) => c[0] as { response_type: string; text: string; blocks?: unknown[] }) };
  }

  it("shows admins the stats ephemerally with names", async () => {
    const { ack, sent, info } = await run("UADMIN01");
    expect(ack).toHaveBeenCalledTimes(1);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ response_type: "ephemeral", blocks: [{ type: "markdown" }] });
    expect(sent[0]!.text).toContain("Sam 1");
    expect(sent[0]!.text).toContain("Alex Park 1");
    expect(info).toHaveBeenCalledTimes(2);
  });

  it("refuses non-admins without reading the log", async () => {
    const { ack, sent, info } = await run("USAM0001");
    expect(ack).toHaveBeenCalledTimes(1);
    expect(sent).toEqual([expect.objectContaining({ response_type: "ephemeral", text: "Only Eli's admins can see usage stats." })]);
    expect(info).not.toHaveBeenCalled();
  });

  it("ignores other workspaces after acknowledging", async () => {
    const { ack, sent } = await run("UADMIN01", { team: "T999" });
    expect(ack).toHaveBeenCalledTimes(1);
    expect(sent).toHaveLength(0);
  });

  it("falls back to mentions when names can't be looked up, and honours a day count", async () => {
    const { sent } = await run("UADMIN01", { failNames: true, text: "30" });
    expect(sent[0]!.text).toContain("<@USAM0001> 1");
    expect(sent[0]!.text).toContain("**Last 30 days:**");
  });

  it("never throws, even when the log can't be read", async () => {
    const { sent, log } = await run("UADMIN01", { logPath: os.tmpdir() }); // a directory, not a file
    expect(sent[0]!.text).toMatch(/couldn't read the usage log/);
    expect(log).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------------------------- personal stats

import { formatPersonalStats, isStatsRequest, summarizePersonal } from "../src/eli/stats.js";
import { RateLimiter } from "../src/eli/limits.js";

describe("personal stats", () => {
  const records = [
    rec(NOW, { user: "USAM0001", estimatedUsd: 0.01, tools: ["web_search"], webSearches: 1 }),
    rec(new Date(NOW.getTime() - 3 * DAY), { user: "USAM0001", estimatedUsd: 0.002 }),
    rec(NOW, { user: "UOTHER01", estimatedUsd: 5 }),
  ];

  it("counts only the asker's own lines and never mentions anyone else", () => {
    const s = summarizePersonal(records, "USAM0001", NOW);
    expect(win(s, "today").requests).toBe(1);
    expect(win(s, "week").requests).toBe(2);
    expect(win(s, "all").estimatedUsd).toBeCloseTo(0.012 + WEB_SEARCH_USD, 6);
    const text = formatPersonalStats(s, { requestsLeftThisHour: 18, requestsPerHour: 20, searchesLeftToday: 14, searchesPerDay: 15 });
    expect(text).toContain("**Today:** 1 request · 1 web search");
    expect(text).toContain("18 of 20 requests left this hour; 14 of 15 web searches left today");
    expect(text).toContain("Most used: web search (1)");
    expect(text).not.toContain("UOTHER01");
    expect(text).not.toContain("<@");
    expect(text).not.toContain("$5");
  });

  it("handles someone with no history, and leaves out searches when web search is off", () => {
    const text = formatPersonalStats(summarizePersonal(records, "UNEW0001", NOW), { requestsLeftThisHour: 20, requestsPerHour: 20 });
    expect(text).toContain("haven't asked me anything yet");
    expect(text).toContain("20 of 20 requests left this hour.");
    expect(text).not.toContain("web search");
  });

  it("recognises only short stats requests", () => {
    for (const t of ["stats", "Stats!", "<@UBOT1> stats", "my usage", "show me my stats", "usage stats please", "/stats", "eli stats?", "stats\n*Sent using* <@U0C67DKFT25|Claude>", "stats *Sent using* <@U0C67DKFT25>"]) {
      expect(isStatsRequest(t), t).toBe(true);
    }
    for (const t of ["stats on grocery pricing", "what are the usage terms?", "summarize the stats in this deck", "", "usage of AI in consulting", "stats\nand also draft an email"]) {
      expect(isStatsRequest(t), t).toBe(false);
    }
  });

  it("RateLimiter.remaining peeks without using a request", () => {
    const limiter = new RateLimiter(3, 100, () => NOW.getTime());
    expect(limiter.remaining("U1")).toBe(3);
    limiter.take("U1");
    expect(limiter.remaining("U1")).toBe(2);
    expect(limiter.remaining("U1")).toBe(2);
    expect(new RateLimiter(3, 1, () => 0).take("U2").ok && 0).toBe(0);
  });
});

describe("handleStatsCommand with personal stats", () => {
  async function run(user: string, text = "") {
    const respond = vi.fn(async (_m: unknown) => undefined);
    const personalStats = vi.fn(async (u: string) => `own stats for ${u}`);
    const req = {
      command: { user_id: user, team_id: "T1", text },
      ack: async () => undefined,
      respond,
      client: { users: { info: async () => ({ ok: true, user: {} }) } },
    } as unknown as StatsRequest;
    await handleStatsCommand(req, {
      usageLogPath: "/nonexistent/usage.log",
      adminUserIds: new Set(["UADMIN01"]),
      allowedTeamId: "T1",
      personalStats,
      log: () => undefined,
      now: () => NOW,
    });
    return { personalStats, sent: respond.mock.calls.map((c) => c[0] as { response_type: string; text: string }) };
  }

  it("gives non-admins their own stats instead of a refusal", async () => {
    const { personalStats, sent } = await run("USAM0001");
    expect(personalStats).toHaveBeenCalledWith("USAM0001");
    expect(sent).toEqual([expect.objectContaining({ response_type: "ephemeral", text: "own stats for USAM0001" })]);
  });

  it("gives admins the workspace view, or their own with `me`", async () => {
    expect((await run("UADMIN01")).sent[0]!.text).toContain("Eli usage");
    const own = await run("UADMIN01", "me");
    expect(own.sent[0]!.text).toBe("own stats for UADMIN01");
  });
});
