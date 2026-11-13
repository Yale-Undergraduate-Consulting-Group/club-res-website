import type { UsageRecord } from "../limits.js";
import { WEB_SEARCH_USD, toolLabel } from "../stats.js";

const DAY_MS = 24 * 60 * 60 * 1000;
export const RANGES = [7, 30, 90] as const;
export const NOT_RECORDED = "Earlier (not tracked)";

export interface PersonRow {
  user: string;
  requests: number;
  costUsd: number;
  lastAt: string;
  topTopic: string;
}

export interface Totals {
  requests: number;
  people: number;
  failures: number;
  costUsd: number;
  webSearches: number;
}

export interface Dashboard {
  /** 0 = all time. */
  days: number;
  from?: string;
  to: string;
  totals: Totals;
  /** The same-length period just before this one; absent for all time. */
  previous?: Totals;
  /** One entry per calendar day in `timeZone`, oldest first (capped at the last 90 days for all time). */
  daily: Array<{ date: string; requests: number; costUsd: number }>;
  topics: Array<{ name: string; count: number }>;
  features: Array<{ name: string; count: number }>;
  people: PersonRow[];
  firstAt?: string;
}

const featureName = (tool: string) => {
  const label = toolLabel(tool);
  return label[0]!.toUpperCase() + label.slice(1);
};

const costOf = (r: UsageRecord) => (r.estimatedUsd ?? 0) + (r.webSearches ?? 0) * WEB_SEARCH_USD;

function totalsOf(rs: readonly UsageRecord[]): Totals {
  return {
    requests: rs.length,
    people: new Set(rs.map((r) => r.user)).size,
    failures: rs.filter((r) => !r.ok).length,
    costUsd: rs.reduce((s, r) => s + costOf(r), 0),
    webSearches: rs.reduce((s, r) => s + (r.webSearches ?? 0), 0),
  };
}

function counted(names: Iterable<string>): Array<{ name: string; count: number }> {
  const m = new Map<string, number>();
  for (const n of names) m.set(n, (m.get(n) ?? 0) + 1);
  return [...m].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/** YYYY-MM-DD in `timeZone`. */
export function dayKey(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(ms);
}

export function buildDashboard(
  records: readonly UsageRecord[],
  { now, days, timeZone }: { now: Date; days: number; timeZone: string },
): Dashboard {
  const t = now.getTime();
  const all = records.filter((r) => Date.parse(r.at) <= t).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const since = days > 0 ? t - days * DAY_MS : undefined;
  const inRange = since === undefined ? all : all.filter((r) => Date.parse(r.at) > since);
  const previous =
    since === undefined ? undefined : totalsOf(all.filter((r) => Date.parse(r.at) > since - days * DAY_MS && Date.parse(r.at) <= since));

  const chartDays = days > 0 ? days : Math.min(90, Math.max(1, Math.ceil((t - Date.parse(all[0]?.at ?? now.toISOString())) / DAY_MS) + 1));
  const daily = new Map<string, { requests: number; costUsd: number }>();
  for (let i = chartDays - 1; i >= 0; i--) daily.set(dayKey(t - i * DAY_MS, timeZone), { requests: 0, costUsd: 0 });
  for (const r of inRange) {
    const d = daily.get(dayKey(Date.parse(r.at), timeZone));
    if (d) {
      d.requests++;
      d.costUsd += costOf(r);
    }
  }

  const byUser = new Map<string, UsageRecord[]>();
  for (const r of inRange) byUser.set(r.user, [...(byUser.get(r.user) ?? []), r]);
  const people = [...byUser].map(([user, rs]) => ({
    user,
    requests: rs.length,
    costUsd: rs.reduce((s, r) => s + costOf(r), 0),
    lastAt: rs.at(-1)!.at,
    topTopic: counted(rs.map((r) => r.topic ?? NOT_RECORDED))[0]!.name,
  }));
  people.sort((a, b) => b.requests - a.requests || b.costUsd - a.costUsd);

  return {
    days,
    from: since === undefined ? all[0]?.at : new Date(since).toISOString(),
    to: now.toISOString(),
    totals: totalsOf(inRange),
    previous,
    daily: [...daily].map(([date, v]) => ({ date, ...v })),
    topics: counted(inRange.map((r) => r.topic ?? NOT_RECORDED)),
    // A feature counts once per request, however many times its tools ran.
    features: counted(inRange.flatMap((r) => [...new Set(r.tools.map(featureName))])),
    people,
    firstAt: all[0]?.at,
  };
}
