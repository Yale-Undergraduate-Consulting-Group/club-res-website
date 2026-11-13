import fs from "node:fs/promises";

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export type LimitDecision = { ok: true } | { ok: false; scope: "user" | "workspace"; retryAfterMs: number };

/**
 * In-memory sliding windows: requests per person per hour and per workspace per day. Resets when Eli restarts;
 * the Anthropic Console spend limit is the hard cap. Every accepted request counts, even if it later fails.
 */
export class RateLimiter {
  private readonly perUser = new Map<string, number[]>();
  private workspace: number[] = [];

  constructor(
    private readonly userPerHour: number,
    private readonly workspacePerDay: number,
    private readonly now: () => number = Date.now,
  ) {}

  /** Requests this person could still make this hour, without using one (also capped by the workspace's day). */
  remaining(userId: string): number {
    const t = this.now();
    const mine = (this.perUser.get(userId) ?? []).filter((x) => t - x < HOUR_MS).length;
    const workspace = this.workspace.filter((x) => t - x < DAY_MS).length;
    return Math.max(0, Math.min(this.userPerHour - mine, this.workspacePerDay - workspace));
  }

  take(userId: string): LimitDecision {
    const t = this.now();
    this.workspace = this.workspace.filter((x) => t - x < DAY_MS);
    const mine = (this.perUser.get(userId) ?? []).filter((x) => t - x < HOUR_MS);

    if (this.workspace.length >= this.workspacePerDay) {
      this.perUser.set(userId, mine);
      return { ok: false, scope: "workspace", retryAfterMs: this.workspace[0]! + DAY_MS - t };
    }
    if (mine.length >= this.userPerHour) {
      this.perUser.set(userId, mine);
      return { ok: false, scope: "user", retryAfterMs: mine[0]! + HOUR_MS - t };
    }
    mine.push(t);
    this.perUser.set(userId, mine);
    this.workspace.push(t);
    return { ok: true };
  }
}

export function limitMessage(d: Extract<LimitDecision, { ok: false }>, userPerHour: number, workspacePerDay: number): string {
  const minutes = Math.max(1, Math.ceil(d.retryAfterMs / 60_000));
  const wait = minutes >= 90 ? `about ${Math.round(minutes / 60)} hours` : `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const requests = (n: number) => `${n} request${n === 1 ? "" : "s"}`;
  return d.scope === "user"
    ? `You've reached Eli's limit of ${requests(userPerHour)} per hour. Try again in ${wait}.`
    : `Eli has reached its daily limit of ${requests(workspacePerDay)} for this workspace. Try again in ${wait}.`;
}

// ---------------------------------------------------------------------------------------------- usage + cost

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

export const emptyUsage = (): UsageTotals => ({
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
});

export function addUsage(total: UsageTotals, u: Partial<Record<keyof UsageTotals, number | null>> | undefined): void {
  if (!u) return;
  for (const k of Object.keys(total) as Array<keyof UsageTotals>) total[k] += u[k] ?? 0;
}

/**
 * USD per 1M tokens: [input, output]. List prices as of 2026-10; Haiku 5.5's rate applies to prompts up to 100K
 * tokens. Cache writes are estimated at 1.25x input and cache reads at 0.1x input. This is an estimate for the
 * log only; the Anthropic Console shows the real bill.
 */
const PRICES: Record<string, [number, number]> = {
  "claude-haiku-5-5": [0.1, 0.5],
  "claude-sonnet-5-5": [2, 10],
  "claude-opus-5-5": [4, 20],
};

export function estimateCostUsd(model: string, u: UsageTotals): number | undefined {
  const p = PRICES[model];
  if (!p) return undefined;
  const [input, output] = p;
  const usd =
    (u.input_tokens * input + u.cache_creation_input_tokens * input * 1.25 + u.cache_read_input_tokens * input * 0.1 +
      u.output_tokens * output) /
    1_000_000;
  return Math.round(usd * 1_000_000) / 1_000_000;
}

export interface UsageRecord {
  at: string;
  user: string;
  channel: string;
  model: string;
  /** What the API reported serving; differs from `model` only if something changed it. */
  servedModel?: string;
  usage: UsageTotals;
  /** Tokens only; web searches are counted in `webSearches` and priced separately (see stats.ts). */
  estimatedUsd?: number;
  tools: string[];
  /** Web searches Anthropic ran for this request (billed separately, about $0.01 each). Absent in older lines. */
  webSearches?: number;
  /** A coarse topic label (topics.ts). The question text is never stored. Absent in older lines. */
  topic?: string;
  ok: boolean;
}

/** Append one JSON line. Never throws: a logging failure must not break a reply. */
export async function logUsage(path: string, record: UsageRecord): Promise<void> {
  try {
    await fs.appendFile(path, JSON.stringify(record) + "\n", { encoding: "utf8", mode: 0o600 });
  } catch (err) {
    console.error(`[eli] could not write usage log: ${(err as Error).message}`);
  }
}
