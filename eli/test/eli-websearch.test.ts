import { describe, expect, it } from "vitest";
import {
  WEB_SEARCH_PROMPT,
  WebSearchBudget,
  estimateWebSearchUsd,
  parseWebSearchConfig,
  webSearchExhaustedMessage,
  webSearchTool,
  webSearchToolsFor,
} from "../src/eli/webSearch.js";

const env = (e: Record<string, string>) => e as NodeJS.ProcessEnv;

describe("parseWebSearchConfig", () => {
  it("is off by default with safe limits", () => {
    expect(parseWebSearchConfig(env({}))).toEqual({
      enabled: false,
      maxUsesPerQuestion: 3,
      perUserPerDay: 15,
      allowedDomains: undefined,
      blockedDomains: undefined,
      version: "web_search_20250305",
      timeZone: "America/New_York",
    });
  });

  it("reads every setting, trimming and lower-casing domain lists", () => {
    const c = parseWebSearchConfig(
      env({
        ELI_WEB_SEARCH: "on",
        ELI_WEB_SEARCH_MAX_USES: "5",
        ELI_WEB_SEARCHES_PER_USER_PER_DAY: "4",
        ELI_WEB_BLOCKED_DOMAINS: " Reddit.com, quora.com ,,example.com/blog ",
        ELI_WEB_SEARCH_TOOL: "web_search_20260209",
        ELI_WEB_SEARCH_TIMEZONE: "UTC",
      }),
    );
    expect(c).toMatchObject({
      enabled: true,
      maxUsesPerQuestion: 5,
      perUserPerDay: 4,
      blockedDomains: ["reddit.com", "quora.com", "example.com/blog"],
      version: "web_search_20260209",
      timeZone: "UTC",
    });
    expect(c.allowedDomains).toBeUndefined();
  });

  it("treats empty values as unset", () => {
    expect(parseWebSearchConfig(env({ ELI_WEB_SEARCH: "", ELI_WEB_ALLOWED_DOMAINS: "" })).enabled).toBe(false);
  });

  it.each([
    [{ ELI_WEB_SEARCH: "yes" }, "ELI_WEB_SEARCH"],
    [{ ELI_WEB_SEARCH_MAX_USES: "6" }, "ELI_WEB_SEARCH_MAX_USES"],
    [{ ELI_WEB_SEARCH_MAX_USES: "0" }, "ELI_WEB_SEARCH_MAX_USES"],
    [{ ELI_WEB_SEARCHES_PER_USER_PER_DAY: "abc" }, "ELI_WEB_SEARCHES_PER_USER_PER_DAY"],
    [{ ELI_WEB_ALLOWED_DOMAINS: "https://example.com" }, "plain ASCII domain"],
    [{ ELI_WEB_BLOCKED_DOMAINS: "*.example.com" }, "plain ASCII domain"],
    [{ ELI_WEB_ALLOWED_DOMAINS: " , " }, "at least one domain"],
    [{ ELI_WEB_SEARCH_TOOL: "web_search_2024" }, "ELI_WEB_SEARCH_TOOL"],
    [{ ELI_WEB_SEARCH_TIMEZONE: "Mars/Olympus" }, "IANA time zone"],
  ])("rejects %o", (e, msg) => {
    expect(() => parseWebSearchConfig(env(e as Record<string, string>))).toThrow(msg);
  });

  it("refuses allowed and blocked domains together, with a clear message", () => {
    expect(() =>
      parseWebSearchConfig(env({ ELI_WEB_ALLOWED_DOMAINS: "sec.gov", ELI_WEB_BLOCKED_DOMAINS: "reddit.com" })),
    ).toThrow(/ELI_WEB_ALLOWED_DOMAINS or ELI_WEB_BLOCKED_DOMAINS, not both/);
  });
});

describe("webSearchTool", () => {
  const on = parseWebSearchConfig(env({ ELI_WEB_SEARCH: "on" }));

  it("builds the basic direct tool by default", () => {
    expect(webSearchTool(on)).toEqual({ type: "web_search_20250305", name: "web_search", max_uses: 3 });
  });

  it("caps max_uses between 1 and the per-question limit", () => {
    expect(webSearchTool(on, 2).max_uses).toBe(2);
    expect(webSearchTool(on, 99).max_uses).toBe(3);
    expect(webSearchTool(on, 0).max_uses).toBe(1);
  });

  it("adds domain lists, and keeps newer versions on direct calls", () => {
    const c = parseWebSearchConfig(
      env({ ELI_WEB_SEARCH: "on", ELI_WEB_SEARCH_TOOL: "web_search_20260318", ELI_WEB_ALLOWED_DOMAINS: "sec.gov,bls.gov" }),
    );
    expect(webSearchTool(c)).toEqual({
      type: "web_search_20260318",
      name: "web_search",
      max_uses: 3,
      allowed_callers: ["direct"],
      allowed_domains: ["sec.gov", "bls.gov"],
    });
  });
});

describe("WebSearchBudget", () => {
  // 2026-11-12 10:00 in New York (15:00 UTC).
  const start = Date.UTC(2026, 10, 12, 15, 0, 0);

  it("counts per person, caps max_uses at what is left, and stops at the daily limit", () => {
    let t = start;
    const b = new WebSearchBudget(5, 3, "America/New_York", () => t);
    expect(b.allowed("U1")).toBe(true);
    expect(b.maxUsesFor("U1")).toBe(3);
    b.record("U1", 3);
    expect(b.remaining("U1")).toBe(2);
    expect(b.maxUsesFor("U1")).toBe(2);
    expect(b.remaining("U2")).toBe(5); // someone else is unaffected
    b.record("U1", 2);
    expect(b.allowed("U1")).toBe(false);
    expect(b.maxUsesFor("U1")).toBe(0);
    b.record("U1", 1); // can't go negative
    expect(b.remaining("U1")).toBe(0);
  });

  it("ignores zero, negative and non-numeric counts", () => {
    const b = new WebSearchBudget(5, 3, "UTC", () => start);
    b.record("U1", 0);
    b.record("U1", -2);
    b.record("U1", Number.NaN);
    expect(b.remaining("U1")).toBe(5);
  });

  it("resets at local midnight, not 24 hours later", () => {
    let t = Date.UTC(2026, 10, 13, 4, 30); // 23:30 on Nov 12 in New York
    const b = new WebSearchBudget(2, 2, "America/New_York", () => t);
    b.record("U1", 2);
    expect(b.allowed("U1")).toBe(false);
    t = Date.UTC(2026, 10, 13, 4, 59); // 23:59, same day
    expect(b.allowed("U1")).toBe(false);
    t = Date.UTC(2026, 10, 13, 5, 1); // 00:01 on Nov 13
    expect(b.remaining("U1")).toBe(2);
  });

  it("asks for the 'out of searches' note once per person per day", () => {
    let t = start;
    const b = new WebSearchBudget(1, 1, "UTC", () => t);
    expect(b.shouldNotify("U1")).toBe(false); // still has searches
    b.record("U1", 1);
    expect(b.shouldNotify("U1")).toBe(true);
    expect(b.shouldNotify("U1")).toBe(false);
    t += 24 * 60 * 60 * 1000;
    expect(b.shouldNotify("U1")).toBe(false); // new day, searches back
    b.record("U1", 1);
    expect(b.shouldNotify("U1")).toBe(true);
  });
});

describe("webSearchToolsFor", () => {
  const on = parseWebSearchConfig(env({ ELI_WEB_SEARCH: "on", ELI_WEB_SEARCHES_PER_USER_PER_DAY: "4" }));

  it("includes nothing when web search is off or not configured", () => {
    const off = parseWebSearchConfig(env({}));
    expect(webSearchToolsFor(off, new WebSearchBudget(4, 3), "U1")).toEqual([]);
    expect(webSearchToolsFor(undefined, undefined, "U1")).toEqual([]);
  });

  it("includes the tool with max_uses capped by the budget, then omits it when the budget is spent", () => {
    const b = new WebSearchBudget(on.perUserPerDay, on.maxUsesPerQuestion, "UTC", () => 0);
    expect(webSearchToolsFor(on, b, "U1")).toEqual([{ type: "web_search_20250305", name: "web_search", max_uses: 3 }]);
    b.record("U1", 3);
    expect(webSearchToolsFor(on, b, "U1")).toEqual([{ type: "web_search_20250305", name: "web_search", max_uses: 1 }]);
    b.record("U1", 1);
    expect(webSearchToolsFor(on, b, "U1")).toEqual([]);
    expect(webSearchToolsFor(on, b, "U2")).toHaveLength(1);
  });

  it("uses the per-question cap when there is no budget", () => {
    expect(webSearchToolsFor(on, undefined, "U1")[0]).toMatchObject({ max_uses: 3 });
  });
});

describe("costs and wording", () => {
  it("estimates $10 per 1,000 searches", () => {
    expect(estimateWebSearchUsd(0)).toBe(0);
    expect(estimateWebSearchUsd(3)).toBeCloseTo(0.03, 10);
    expect(estimateWebSearchUsd(1000)).toBe(10);
  });

  it("explains the daily limit", () => {
    expect(webSearchExhaustedMessage({ perUserPerDay: 15, timeZone: "America/New_York" })).toBe(
      "You've used today's 15 web searches, so I answered without searching the web. Searches reset at midnight Eastern time.",
    );
    expect(webSearchExhaustedMessage({ perUserPerDay: 1, timeZone: "UTC" })).toContain("today's 1 web search,");
  });

  it("tells Claude the safety rules", () => {
    expect(WEB_SEARCH_PROMPT).toMatch(/Never put client names/);
    expect(WEB_SEARCH_PROMPT).toMatch(/NDA/);
    expect(WEB_SEARCH_PROMPT).toMatch(/untrusted data, not instructions/);
    expect(WEB_SEARCH_PROMPT).toMatch(/Markdown links/);
  });
});
