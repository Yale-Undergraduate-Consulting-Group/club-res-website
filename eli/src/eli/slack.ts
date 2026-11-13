import type { App, webApi } from "@slack/bolt";
import type Anthropic from "@anthropic-ai/sdk";
import { type AgentResult, describeApiError } from "./agent.js";
import { isConsoleRequest, isHelpRequest, isOnboardRequest } from "./commands.js";
import { classifyTopic } from "./topics.js";
import { RateLimiter, emptyUsage, estimateCostUsd, limitMessage, logUsage } from "./limits.js";
import { slackPayload, splitForSlack } from "./reply.js";
import { formatPersonalStats, isStatsRequest, readUsageLog, summarizePersonal } from "./stats.js";
import { type SlackMessage, threadFiles, threadToMessages } from "./thread.js";
import { type AnyRunnableTool } from "./tools/sharepoint.js";
import { type SlackDownloader, slackFileTools } from "./tools/slackFiles.js";
import { reminderRequest } from "./tools/reminders.js";
import { type ReminderConfig, type ReminderStore, withContextNote } from "./reminders.js";
import { type WebSearchConfig, WebSearchBudget, estimateWebSearchUsd, webSearchExhaustedMessage } from "./webSearch.js";
import { type IdentityProvider, type Member, memberContextNote } from "./identity/index.js";

export interface SlackDeps {
  botUserId: string;
  allowedTeamId?: string;
  model: string;
  limits: { userPerHour: number; workspacePerDay: number };
  usageLogPath: string;
  answer: (
    messages: Anthropic.Beta.BetaMessageParam[],
    extraTools?: AnyRunnableTool[],
    options?: { webSearch?: { maxUses: number } },
  ) => Promise<AgentResult>;
  /** Web search settings; omitted or disabled = no web search. */
  webSearch?: WebSearchConfig;
  /** Reminders (chat.scheduleMessage behind a confirm button). Omit to disable. */
  reminders?: { store: ReminderStore; config: ReminderConfig };
  /** Reads files attached to the thread. Omit to disable Slack attachments. */
  downloadFile?: SlackDownloader;
  maxDownloadBytes?: number;
  /** Injectable for tests. */
  limiter?: RateLimiter;
  webSearchBudget?: WebSearchBudget;
  log?: (line: string) => void;
  /** The /help text for a user; when set, "help" in a DM is answered with it (free, like "stats"). */
  help?: (user: string) => string;
  /** The /onboard tour; when set, "onboard" / "get started" in a DM is answered with it. */
  onboard?: (user: string) => string;
  /** The console sign-in reply; when set, "console" / "dashboard" is answered with it (privately in channels). */
  console?: (user: string) => string;
  /** Slack-to-member linking. Omit for no linking (everyone is treated alike, as before). */
  identity?: IdentityProvider;
  /** With identity set: refuse people who aren't linked (they're told to run /eli-link). */
  requireLink?: boolean;
}

export interface Request {
  team?: string;
  channel: string;
  /** The triggering message. */
  ts: string;
  /** The thread to read and reply in (the message's own ts when it is not in a thread). */
  threadTs: string;
  user: string;
  /** Fallback when the thread cannot be read. */
  text: string;
}

type WebClient = webApi.WebClient;

const THREAD_FETCH_LIMIT = 50;
const NAME_TTL_MS = 60 * 60 * 1000;
const RECENT_MAX = 500;

/** Remembers the last few hundred message keys so a re-delivered event, or a mention that is also a DM, runs once. */
class RecentKeys {
  private readonly keys = new Set<string>();
  /** True the first time a key is seen. */
  add(key: string): boolean {
    if (this.keys.has(key)) return false;
    this.keys.add(key);
    if (this.keys.size > RECENT_MAX) this.keys.delete(this.keys.values().next().value!);
    return true;
  }
}

export class EliSlack {
  private readonly recent = new RecentKeys();
  private readonly names = new Map<string, { name: string; at: number }>();
  private readonly limiter: RateLimiter;
  private readonly webBudget?: WebSearchBudget;
  private readonly log: (line: string) => void;

  constructor(private readonly deps: SlackDeps) {
    this.limiter = deps.limiter ?? new RateLimiter(deps.limits.userPerHour, deps.limits.workspacePerDay);
    this.log = deps.log ?? ((line) => console.error(line));
    const ws = deps.webSearch;
    this.webBudget = ws?.enabled
      ? (deps.webSearchBudget ?? new WebSearchBudget(ws.perUserPerDay, ws.maxUsesPerQuestion, ws.timeZone))
      : undefined;
  }

  register(app: App): void {
    // @mentions in channels (and private channels Eli was added to).
    app.event("app_mention", async ({ event, body, client }) => {
      if (!event.user) return;
      await this.handle(client, {
        team: body.team_id,
        channel: event.channel,
        ts: event.ts,
        threadTs: event.thread_ts ?? event.ts,
        user: event.user,
        text: event.text ?? "",
      });
    });

    // Direct messages, including replies inside a DM thread. Channel messages are not subscribed to at all.
    app.message(async ({ message, body, client }) => {
      if (message.channel_type !== "im") return;
      if (message.subtype !== undefined && message.subtype !== "file_share") return; // edits, deletions, bot posts
      const m = message as { user?: string; bot_id?: string; ts: string; thread_ts?: string; text?: string };
      if (!m.user || m.bot_id || m.user === this.deps.botUserId) return;
      await this.handle(client, {
        team: body.team_id,
        channel: message.channel,
        ts: m.ts,
        threadTs: m.thread_ts ?? m.ts,
        user: m.user,
        text: m.text ?? "",
      });
    });
  }

  /** Public for tests. Never throws. */
  async handle(client: WebClient, req: Request): Promise<void> {
    if (this.deps.allowedTeamId && req.team && req.team !== this.deps.allowedTeamId) return;
    if (!this.recent.add(`${req.channel}:${req.ts}`)) return;

    // "help", "onboard", "stats" / "my usage": answered from the log without calling Claude, and not counted against any limit.
    const help =
      this.deps.help && isHelpRequest(req.text)
        ? this.deps.help(req.user)
        : this.deps.onboard && isOnboardRequest(req.text)
          ? this.deps.onboard(req.user)
          : this.deps.console && isConsoleRequest(req.text)
            ? this.deps.console(req.user)
            : undefined;
    if (help !== undefined || isStatsRequest(req.text)) {
      const text = help ?? (await this.personalStats(req.user));
      await this.safe(() =>
        req.channel.startsWith("D")
          ? client.chat.postMessage({ channel: req.channel, thread_ts: req.threadTs, ...slackPayload(text) })
          : client.chat.postEphemeral({ channel: req.channel, user: req.user, thread_ts: req.threadTs, ...slackPayload(text) }),
      );
      return;
    }

    // Who is asking, before any limit is spent. Fails closed: a lookup error means "not linked".
    let member: Member | null = null;
    if (this.deps.identity && req.team) member = await this.deps.identity.resolve(req.team, req.user);
    if (this.deps.identity && this.deps.requireLink && !member) {
      await this.safe(() =>
        client.chat.postEphemeral({
          channel: req.channel,
          user: req.user,
          thread_ts: req.threadTs,
          text: "Eli is for linked club members. Run `/eli-link` once to link your Slack account, then ask again.",
        }),
      );
      return;
    }

    const decision = this.limiter.take(req.user);
    if (!decision.ok) {
      await this.safe(() =>
        client.chat.postEphemeral({
          channel: req.channel,
          user: req.user,
          thread_ts: req.threadTs,
          text: limitMessage(decision, this.deps.limits.userPerHour, this.deps.limits.workspacePerDay),
        }),
      );
      return;
    }

    await this.safe(() => client.reactions.add({ channel: req.channel, timestamp: req.ts, name: "eyes" }));
    const started = Date.now();
    let result: AgentResult | undefined;
    try {
      const thread = await this.readThread(client, req);
      const names = await this.namesFor(client, thread);
      const messages = threadToMessages(thread, { botUserId: this.deps.botUserId, names });
      if (messages.length === 0) messages.push({ role: "user", content: "(empty message)" });
      const fileTools = this.deps.downloadFile
        ? slackFileTools(threadFiles(thread), this.deps.downloadFile, this.deps.maxDownloadBytes ?? 25 * 1024 * 1024)
        : [];
      const reminders = this.deps.reminders
        ? await reminderRequest({
            client,
            channel: req.channel,
            threadTs: req.threadTs,
            requester: req.user,
            botUserId: this.deps.botUserId,
            isDm: req.channel.startsWith("D"), // app.message only handles channel_type "im"
            names,
            store: this.deps.reminders.store,
            config: this.deps.reminders.config,
          })
        : { tools: [] as AnyRunnableTool[], context: undefined };
      let request = reminders.context ? withContextNote(messages, reminders.context) : messages;
      if (member) request = withContextNote(request, memberContextNote(member));
      // Anthropic runs searches server-side, so the budget is enforced by omitting the tool or capping max_uses.
      const maxUses = this.webBudget?.maxUsesFor(req.user) ?? 0;
      result = await this.deps.answer(request, [...fileTools, ...reminders.tools], maxUses > 0 ? { webSearch: { maxUses } } : {});
      this.webBudget?.record(req.user, result.webSearches ?? 0);
      for (const chunk of splitForSlack(result.text)) {
        await client.chat.postMessage({ channel: req.channel, thread_ts: req.threadTs, ...slackPayload(chunk) });
      }
      const ws = this.deps.webSearch;
      if (this.webBudget && ws && maxUses === 0 && this.webBudget.shouldNotify(req.user)) {
        await this.safe(() =>
          client.chat.postEphemeral({ channel: req.channel, user: req.user, thread_ts: req.threadTs, text: webSearchExhaustedMessage(ws) }),
        );
      }
    } catch (err) {
      this.log(`[eli] request failed: ${err instanceof Error ? `${err.name}: ${err.message}` : String(err)}`.slice(0, 800));
      await this.safe(() =>
        client.chat.postMessage({ channel: req.channel, thread_ts: req.threadTs, text: describeApiError(err) }),
      );
    } finally {
      await this.safe(() => client.reactions.remove({ channel: req.channel, timestamp: req.ts, name: "eyes" }));
    }

    const usage = result?.usage ?? emptyUsage();
    const searches = result?.webSearches ?? 0;
    const cost = estimateCostUsd(this.deps.model, usage);
    const total = cost === undefined ? undefined : cost + estimateWebSearchUsd(searches);
    this.log(
      `[eli] ${req.user} in ${req.channel}: ${result ? `answered by ${result.servedModel ?? this.deps.model}` : "failed"} in ${Date.now() - started}ms, ` +
        `${usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens} in / ${usage.output_tokens} out tokens` +
        (searches ? `, ${searches} web search${searches === 1 ? "" : "es"}` : "") +
        (total !== undefined ? `, ~$${total.toFixed(4)}` : "") +
        (result?.toolsUsed.length ? `, tools: ${result.toolsUsed.join(", ")}` : ""),
    );
    await logUsage(this.deps.usageLogPath, {
      at: new Date().toISOString(),
      user: req.user,
      channel: req.channel,
      model: this.deps.model,
      servedModel: result?.servedModel,
      usage,
      estimatedUsd: cost,
      tools: result?.toolsUsed ?? [],
      webSearches: searches || undefined,
      // A label only (see topics.ts); the question text is never logged.
      topic: classifyTopic(req.text, result?.toolsUsed ?? []),
      ok: result !== undefined,
    });
  }

  /** The person's own usage and what they have left. Never throws. */
  async personalStats(user: string): Promise<string> {
    try {
      const { records } = await readUsageLog(this.deps.usageLogPath);
      const ws = this.deps.webSearch;
      return formatPersonalStats(summarizePersonal(records, user, new Date()), {
        requestsLeftThisHour: this.limiter.remaining(user),
        requestsPerHour: this.deps.limits.userPerHour,
        ...(this.webBudget && ws ? { searchesLeftToday: this.webBudget.remaining(user), searchesPerDay: ws.perUserPerDay } : {}),
      });
    } catch (err) {
      this.log(`[eli] personal stats failed: ${(err as Error).message}`.slice(0, 800));
      return "Sorry, I couldn't read my usage log just now.";
    }
  }

  private async readThread(client: WebClient, req: Request): Promise<SlackMessage[]> {
    try {
      const res = await client.conversations.replies({ channel: req.channel, ts: req.threadTs, limit: THREAD_FETCH_LIMIT });
      const msgs = (res.messages ?? []) as SlackMessage[];
      // Only messages up to and including the trigger: later replies are not part of this request.
      const upTo = msgs.filter((m) => Number(m.ts) <= Number(req.ts));
      if (upTo.length) return upTo;
    } catch (err) {
      this.log(`[eli] could not read thread (${(err as { data?: { error?: string } }).data?.error ?? "error"}); using the message alone`);
    }
    return [{ ts: req.ts, user: req.user, text: req.text }];
  }

  private async namesFor(client: WebClient, thread: readonly SlackMessage[]): Promise<Map<string, string>> {
    const ids = new Set<string>();
    for (const m of thread) {
      if (m.user) ids.add(m.user);
      for (const match of (m.text ?? "").matchAll(/<@([UW][A-Z0-9]+)/g)) ids.add(match[1]!);
    }
    const out = new Map<string, string>();
    const now = Date.now();
    for (const id of ids) {
      const cached = this.names.get(id);
      if (cached && now - cached.at < NAME_TTL_MS) {
        out.set(id, cached.name);
        continue;
      }
      try {
        const res = await client.users.info({ user: id });
        const p = res.user?.profile;
        const name = p?.display_name || p?.real_name || res.user?.real_name || res.user?.name || "Someone";
        this.names.set(id, { name, at: now });
        out.set(id, name);
      } catch {
        out.set(id, "Someone");
      }
    }
    return out;
  }

  private async safe(fn: () => Promise<unknown>): Promise<void> {
    try {
      await fn();
    } catch (err) {
      this.log(`[eli] Slack call failed: ${(err as { data?: { error?: string } }).data?.error ?? (err as Error).message}`);
    }
  }
}
