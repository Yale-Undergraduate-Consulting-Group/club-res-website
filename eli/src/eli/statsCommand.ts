import type { App, RespondArguments, SlashCommand, webApi } from "@slack/bolt";
import { slackPayload } from "./reply.js";
import { formatStats, readUsageLog, summarizeUsage } from "./stats.js";

export const STATS_COMMAND = "/eli-stats";
const DAY_MS = 24 * 60 * 60 * 1000;
const NOT_ADMIN = "Only Eli's admins can see usage stats.";

/** `ELI_ADMIN_USER_IDS`: comma-separated Slack user ids (U… or W…). Empty or unset means no admins. */
export function parseAdminIds(env: NodeJS.ProcessEnv = process.env): ReadonlySet<string> {
  return parseUserIds("ELI_ADMIN_USER_IDS", env.ELI_ADMIN_USER_IDS);
}

/** A comma-separated list of Slack user ids from setting `name`. Throws a readable error for anything else. */
export function parseUserIds(name: string, raw: string | undefined): ReadonlySet<string> {
  const ids = (raw ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const bad = ids.filter((id) => !/^[UW][A-Z0-9]{2,}$/.test(id));
  if (bad.length) {
    throw new Error(
      `${name}: ${bad.map((b) => JSON.stringify(b)).join(", ")} ${bad.length === 1 ? "is not a Slack user id" : "are not Slack user ids"}. ` +
        "Use member ids such as U0123ABCD (Slack profile → ⋮ → Copy member ID), separated by commas.",
    );
  }
  return new Set(ids);
}

export interface StatsDeps {
  usageLogPath: string;
  adminUserIds: ReadonlySet<string>;
  allowedTeamId?: string;
  log?: (line: string) => void;
  /** The asker's own stats. When set, non-admins (and admins typing `/eli-stats me`) get these instead of a refusal. */
  personalStats?: (user: string) => Promise<string>;
  /** Injectable for tests. */
  now?: () => Date;
}

export interface StatsRequest {
  command: Pick<SlashCommand, "user_id" | "team_id" | "text">;
  ack: () => Promise<void>;
  respond: (message: RespondArguments) => Promise<unknown>;
  client: { users: Pick<webApi.WebClient["users"], "info"> };
}

/** `/eli-stats [days]`. Public for tests. Never throws. */
export async function handleStatsCommand({ command, ack, respond, client }: StatsRequest, deps: StatsDeps): Promise<void> {
  const log = deps.log ?? ((line) => console.error(line));
  const reply = async (markdown: string) => {
    try {
      await respond({ response_type: "ephemeral", ...slackPayload(markdown) });
    } catch {
      // If Slack rejects the markdown block, plain text still gets the numbers across.
      try {
        await respond({ response_type: "ephemeral", text: markdown });
      } catch (err) {
        log(`[eli] could not send stats: ${(err as Error).message}`);
      }
    }
  };
  try {
    await ack();
  } catch (err) {
    log(`[eli] could not acknowledge ${STATS_COMMAND}: ${(err as Error).message}`);
  }
  if (deps.allowedTeamId && command.team_id && command.team_id !== deps.allowedTeamId) return;
  const wantsOwn = /^(me|mine|my)$/i.test(command.text?.trim() ?? "");
  if (!deps.adminUserIds.has(command.user_id) || wantsOwn) {
    if (!deps.personalStats) return reply(NOT_ADMIN);
    return reply(await deps.personalStats(command.user_id));
  }

  try {
    const days = Number.parseInt(command.text?.trim() ?? "", 10);
    const now = deps.now?.() ?? new Date();
    const range = Number.isFinite(days) && days > 0 ? { since: new Date(now.getTime() - days * DAY_MS), sinceLabel: `Last ${days} days` } : {};
    const { records, skipped } = await readUsageLog(deps.usageLogPath);
    const summary = summarizeUsage(records, { now, ...range });
    const widest = summary.windows.at(-1)!;
    const ids = new Set([...widest.topByRequests, ...widest.topByCost].map((u) => u.user));
    await reply(formatStats(summary, { names: await resolveNames(client, ids), skipped }));
  } catch (err) {
    log(`[eli] ${STATS_COMMAND} failed: ${(err as Error).message}`.slice(0, 800));
    await reply("Sorry, I couldn't read the usage log. Check Eli's terminal for details.");
  }
}

/** Display names for the ids it can look up; the rest fall back to mentions in `formatStats`. */
async function resolveNames(client: StatsRequest["client"], ids: Iterable<string>): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  await Promise.all(
    [...ids].map(async (id) => {
      try {
        const res = await client.users.info({ user: id });
        const p = res.user?.profile;
        const name = p?.display_name || p?.real_name || res.user?.real_name || res.user?.name;
        if (name) out.set(id, name);
      } catch {
        // Unknown or deactivated user: show the mention instead.
      }
    }),
  );
  return out;
}

export function registerStatsCommand(app: App, deps: StatsDeps): void {
  app.command(STATS_COMMAND, async ({ command, ack, respond, client }) => {
    await handleStatsCommand({ command, ack: () => ack(), respond, client }, deps);
  });
}
