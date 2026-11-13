import fs from "node:fs/promises";
import { z } from "zod";
import type { UsageRecord } from "./limits.js";

const DAY_MS = 24 * 60 * 60 * 1000;
/** Anthropic bills web searches separately from tokens; `estimatedUsd` in the log covers tokens only. */
export const WEB_SEARCH_USD = 0.01;
const TOP_USERS = 5;
const TOP_TOOLS = 8;
const TOP_MODELS = 3;

const count = z.number().nonnegative().default(0);
const recordSchema = z.object({
  at: z.string().refine((s) => !Number.isNaN(Date.parse(s))),
  user: z.string().min(1),
  channel: z.string().default(""),
  model: z.string(),
  servedModel: z.string().optional(),
  usage: z.object({
    input_tokens: count,
    output_tokens: count,
    cache_creation_input_tokens: count,
    cache_read_input_tokens: count,
  }),
  estimatedUsd: z.number().nonnegative().optional(),
  tools: z.array(z.string()).default([]),
  webSearches: z.number().int().nonnegative().optional(),
  topic: z.string().max(60).optional(),
  ok: z.boolean(),
});

export interface UsageLog {
  records: UsageRecord[];
  /** Lines that were not valid usage records. Blank lines are not counted. */
  skipped: number;
}

export function parseUsageLog(text: string): UsageLog {
  const records: UsageRecord[] = [];
  let skipped = 0;
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let json: unknown;
    try {
      json = JSON.parse(line);
    } catch {
      skipped++;
      continue;
    }
    const parsed = recordSchema.safeParse(json);
    if (parsed.success) records.push(parsed.data);
    else skipped++;
  }
  return { records, skipped };
}

/** Read the JSONL usage log. A missing file means no requests yet. */
export async function readUsageLog(path: string): Promise<UsageLog> {
  try {
    return parseUsageLog(await fs.readFile(path, "utf8"));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return { records: [], skipped: 0 };
    throw err;
  }
}

// ---------------------------------------------------------------------------------------------- summary

export interface UserStats {
  user: string;
  requests: number;
  estimatedUsd: number;
}

export interface WindowStats {
  key: "today" | "week" | "all";
  label: string;
  /** ISO start of the window; absent for all time. */
  from?: string;
  requests: number;
  failures: number;
  /** Tokens plus web searches. */
  estimatedUsd: number;
  webSearchUsd: number;
  /** Requests whose model had no known price, so they add nothing to `estimatedUsd`. */
  unpriced: number;
  /** Including cache reads and writes. */
  inputTokens: number;
  outputTokens: number;
  webSearches: number;
  tools: Array<{ name: string; count: number }>;
  users: number;
  topByRequests: UserStats[];
  topByCost: UserStats[];
  /** Models that answered (successful requests only). */
  models: Array<{ model: string; requests: number }>;
}

export interface UsageSummary {
  now: string;
  /** The oldest record counted in the widest window. */
  firstAt?: string;
  windows: WindowStats[];
}

const recordCost = (r: UsageRecord) => (r.estimatedUsd ?? 0) + (r.webSearches ?? 0) * WEB_SEARCH_USD;
const round6 = (n: number) => Math.round(n * 1_000_000) / 1_000_000;

function windowStats(key: WindowStats["key"], label: string, from: Date | undefined, records: UsageRecord[]): WindowStats {
  const users = new Map<string, UserStats>();
  const tools = new Map<string, number>();
  const models = new Map<string, number>();
  const w = {
    requests: 0, failures: 0, estimatedUsd: 0, webSearchUsd: 0, unpriced: 0, inputTokens: 0, outputTokens: 0, webSearches: 0,
  };
  for (const r of records) {
    const cost = recordCost(r);
    w.requests++;
    if (!r.ok) w.failures++;
    if (r.estimatedUsd === undefined && r.ok) w.unpriced++;
    w.estimatedUsd += cost;
    w.webSearches += r.webSearches ?? 0;
    w.webSearchUsd += (r.webSearches ?? 0) * WEB_SEARCH_USD;
    w.inputTokens += r.usage.input_tokens + r.usage.cache_read_input_tokens + r.usage.cache_creation_input_tokens;
    w.outputTokens += r.usage.output_tokens;
    for (const t of r.tools) tools.set(t, (tools.get(t) ?? 0) + 1);
    if (r.ok) {
      const m = r.servedModel ?? r.model;
      models.set(m, (models.get(m) ?? 0) + 1);
    }
    const u = users.get(r.user) ?? { user: r.user, requests: 0, estimatedUsd: 0 };
    u.requests++;
    u.estimatedUsd += cost;
    users.set(r.user, u);
  }
  const all = [...users.values()].map((u) => ({ ...u, estimatedUsd: round6(u.estimatedUsd) }));
  return {
    key,
    label,
    from: from?.toISOString(),
    ...w,
    estimatedUsd: round6(w.estimatedUsd),
    webSearchUsd: round6(w.webSearchUsd),
    tools: [...tools].map(([name, n]) => ({ name, count: n })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    users: users.size,
    topByRequests: [...all].sort((a, b) => b.requests - a.requests || b.estimatedUsd - a.estimatedUsd).slice(0, TOP_USERS),
    topByCost: all
      .filter((u) => u.estimatedUsd > 0)
      .sort((a, b) => b.estimatedUsd - a.estimatedUsd || b.requests - a.requests)
      .slice(0, TOP_USERS),
    models: [...models].map(([model, n]) => ({ model, requests: n })).sort((a, b) => b.requests - a.requests),
  };
}

/**
 * Today (local calendar day), the last 7 days (rolling) and all time, or everything since `since` when given.
 * Records after `now` are ignored.
 */
export function summarizeUsage(
  records: readonly UsageRecord[],
  { now, since, sinceLabel }: { now: Date; since?: Date; sinceLabel?: string },
): UsageSummary {
  const end = now.getTime();
  const timed = records
    .map((r) => ({ r, t: Date.parse(r.at) }))
    .filter((x) => x.t <= end)
    .sort((a, b) => a.t - b.t);
  const inWindow = (from: Date | undefined) => timed.filter((x) => !from || x.t >= from.getTime()).map((x) => x.r);

  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const weekAgo = new Date(end - 7 * DAY_MS);
  const widest = inWindow(since);
  return {
    now: now.toISOString(),
    firstAt: widest[0]?.at,
    windows: [
      windowStats("today", "Today", midnight, inWindow(midnight)),
      windowStats("week", "Last 7 days", weekAgo, inWindow(weekAgo)),
      windowStats("all", since ? (sinceLabel ?? `Since ${shortDate(since)}`) : "All time", since, widest),
    ],
  };
}

// ---------------------------------------------------------------------------------------------- formatting

const shortDate = (d: Date) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });

export function formatUsd(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `$${usd.toFixed(usd < 0.1 ? 4 : 2)}`;
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 2 : 1)}M`;
}

const plural = (n: number, one: string, many = one + "s") => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const clip = (s: string, max = 40) => (s.length > max ? s.slice(0, max - 1) + "…" : s);

function windowLine(w: WindowStats): string {
  if (w.requests === 0) return `**${w.label}:** no requests`;
  const parts = [
    plural(w.requests, "request") + (w.failures ? ` (${w.failures} failed)` : ""),
    plural(w.users, "person", "people"),
    `${formatTokens(w.inputTokens)} in / ${formatTokens(w.outputTokens)} out tokens`,
  ];
  if (w.webSearches) parts.push(plural(w.webSearches, "web search", "web searches"));
  parts.push(`~${formatUsd(w.estimatedUsd)}`);
  return `**${w.label}:** ${parts.join(" · ")}`;
}

/**
 * Compact Markdown for one Slack message (well under 3,500 characters whatever the log holds).
 * `names` maps user ids to display names; without one, users appear as Slack mentions.
 */
export function formatStats(
  summary: UsageSummary,
  { names, skipped = 0 }: { names?: ReadonlyMap<string, string>; skipped?: number } = {},
): string {
  const who = (id: string) => {
    const name = names?.get(id);
    return name ? clip(name, 30) : `<@${clip(id, 20)}>`;
  };
  const asOf = new Date(summary.now).toLocaleString("en-US", {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  const lines = [`**Eli usage** (as of ${asOf})`, ""];
  const widest = summary.windows.at(-1)!;

  if (widest.requests === 0) {
    lines.push(widest.from ? `No requests logged ${widest.label.toLowerCase()}.` : "No requests logged yet.");
  } else {
    for (const w of summary.windows) lines.push(windowLine(w));
    const since = widest.from || !summary.firstAt ? "" : ` (since ${shortDate(new Date(summary.firstAt))})`;
    lines.push("", `**${widest.label}${since}, in detail**`);
    if (widest.webSearches) {
      lines.push(`- Web searches: ${widest.webSearches.toLocaleString("en-US")} (~${formatUsd(widest.webSearchUsd)} of the total)`);
    }
    if (widest.tools.length) {
      const shown = widest.tools.slice(0, TOP_TOOLS).map((t) => `${clip(t.name, 32)} ${t.count}`);
      const more = widest.tools.length - shown.length;
      lines.push(`- Tools: ${shown.join(", ")}${more > 0 ? `, and ${more} more` : ""}`);
    } else {
      lines.push("- Tools: none used");
    }
    if (widest.models.length) {
      lines.push(`- Answered by: ${widest.models.slice(0, TOP_MODELS).map((m) => `${clip(m.model, 40)} (${m.requests})`).join(", ")}`);
    }
    lines.push(`- Most requests: ${widest.topByRequests.map((u) => `${who(u.user)} ${u.requests}`).join(", ")}`);
    if (widest.topByCost.length) {
      lines.push(`- Highest cost: ${widest.topByCost.map((u) => `${who(u.user)} ${formatUsd(u.estimatedUsd)}`).join(", ")}`);
    }
    if (widest.unpriced) lines.push(`- ${plural(widest.unpriced, "request")} used a model with no known price and count as $0.`);
  }
  if (skipped) lines.push("", `_Skipped ${plural(skipped, "unreadable line")} in the usage log._`);
  lines.push("", "_Costs are estimates; the Anthropic Console has the real bill._");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------------------------- personal stats

/** What a person has left right now; the counters live in memory and reset when Eli restarts. */
export interface PersonalLimits {
  requestsLeftThisHour: number;
  requestsPerHour: number;
  /** Omitted when web search is off. */
  searchesLeftToday?: number;
  searchesPerDay?: number;
}

/** One person's own usage: today, the last 7 days and all time, from their lines in the log only. */
export function summarizePersonal(records: readonly UsageRecord[], user: string, now: Date): UsageSummary {
  return summarizeUsage(
    records.filter((r) => r.user === user),
    { now },
  );
}

/**
 * Markdown for "my stats": safe to show the person themselves, so it never mentions anyone else.
 * Shorter than the admin view: totals per window, their most-used tools, and what they have left.
 */
export function formatPersonalStats(summary: UsageSummary, limits?: PersonalLimits): string {
  const lines = ["**Your Eli usage**", ""];
  const all = summary.windows.at(-1)!;
  if (all.requests === 0) {
    lines.push("You haven't asked me anything yet (or not since my usage log started).");
  } else {
    for (const w of summary.windows) {
      if (w.requests === 0) {
        lines.push(`**${w.label}:** none`);
        continue;
      }
      const parts = [plural(w.requests, "request") + (w.failures ? ` (${w.failures} failed)` : "")];
      if (w.webSearches) parts.push(plural(w.webSearches, "web search", "web searches"));
      parts.push(`~${formatUsd(w.estimatedUsd)}`);
      lines.push(`**${w.label}:** ${parts.join(" · ")}`);
    }
    const used = new Map<string, number>();
    for (const t of all.tools) used.set(toolLabel(t.name), (used.get(toolLabel(t.name)) ?? 0) + t.count);
    if (used.size) {
      const top = [...used].sort((a, b) => b[1] - a[1]).slice(0, 4);
      lines.push("", `Most used: ${top.map(([label, n]) => `${label} (${n})`).join(", ")}`);
    }
  }
  if (limits) {
    const left = [`${limits.requestsLeftThisHour} of ${limits.requestsPerHour} requests left this hour`];
    if (limits.searchesLeftToday !== undefined && limits.searchesPerDay !== undefined) {
      left.push(`${limits.searchesLeftToday} of ${limits.searchesPerDay} web searches left today`);
    }
    lines.push("", `**Right now:** ${left.join("; ")}.`);
  }
  lines.push("", "_Costs are estimates of what Eli's Anthropic account pays. Checking your stats is free._");
  return lines.join("\n");
}

/** Plain names for members; the admin view keeps the raw tool names. */
export function toolLabel(name: string): string {
  if (name.startsWith("sharepoint_")) return "SharePoint/OneDrive";
  if (name.startsWith("gdrive_")) return "Google Drive";
  if (name === "web_search") return "web search";
  if (name === "slack_read_file") return "attachments";
  if (name.includes("reminder")) return "reminders";
  return clip(name, 32);
}

/** True for a message that only asks for the sender's own stats ("stats", "my usage", "/stats"…). */
/** Lowercased text of a short DM command, without @mentions, Slack's "Sent using @App" footer or end punctuation. */
export function normalizeDmCommand(text: string): string {
  return text
    // Slack's "Sent using @App" footer, added when someone sends through an AI app or connector.
    .replace(/\s*[*_]*Sent using[*_]*\s+<@[UW][A-Z0-9]+(?:\|[^>]*)?>\s*$/i, "")
    .replace(/<@[UW][A-Z0-9]+(?:\|[^>]*)?>/g, "")
    .trim()
    .toLowerCase()
    .replace(/[.!?]+$/, "")
    .trim();
}

export function isStatsRequest(text: string): boolean {
  const t = normalizeDmCommand(text);
  return /^\/?(?:(?:show\s+)?(?:me\s+)?my\s+)?(?:stats|usage|usage stats|eli stats|eli usage)(?:\s+please)?$/.test(t);
}
