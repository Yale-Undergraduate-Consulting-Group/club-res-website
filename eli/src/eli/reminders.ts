import { randomBytes } from "node:crypto";
import type Anthropic from "@anthropic-ai/sdk";
import type { App, types as slackTypes, webApi } from "@slack/bolt";
import { z } from "zod";
import { cleanEnv } from "../config.js";
import { ToolInputError } from "../tools/result.js";

/**
 * Reminders that Slack itself posts later (chat.scheduleMessage), so they arrive even while Eli is offline.
 *
 * Nothing is scheduled by the model directly: the tool creates a pending reminder here and posts a preview with
 * Schedule / Cancel buttons, and only the person who asked can press Schedule. Scheduled reminders are found again
 * with chat.scheduledMessages.list and their owner is read back from the text prefix, so no database is needed.
 */

type WebClient = webApi.WebClient;

// ---------------------------------------------------------------------------------------------- limits

/** Slack: "You will only be able to schedule a message up to 120 days into the future" (error time_too_far). */
export const MAX_AHEAD_MS = 120 * 24 * 60 * 60 * 1000;
export const MIN_AHEAD_MS = 2 * 60 * 1000;
/** At confirm time the reminder only has to still be in the future (the preview may have sat for a while). */
export const MIN_AHEAD_AT_CONFIRM_MS = 30 * 1000;
export const PENDING_TTL_MS = 15 * 60 * 1000;
export const MAX_MESSAGE_CHARS = 1000;
export const MAX_MENTIONS = 10;

export const SCHEDULE_ACTION = "eli_reminder_schedule";
export const CANCEL_ACTION = "eli_reminder_cancel";

// ---------------------------------------------------------------------------------------------- config

export interface ReminderConfig {
  enabled: boolean;
  maxPerUser: number;
  /** Used when Slack does not give the person's time zone. */
  defaultTimeZone: string;
}

export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

const reminderEnv = z.object({
  ELI_REMINDERS: z.enum(["on", "off"]).default("on"),
  ELI_MAX_REMINDERS_PER_USER: z.coerce.number().int().min(1).max(50).default(5),
  ELI_DEFAULT_TIME_ZONE: z
    .string()
    .default("America/New_York")
    .refine(isValidTimeZone, "must be an IANA time zone such as America/New_York"),
});

/** Throws one Error listing every problem. */
export function parseReminderConfig(env: NodeJS.ProcessEnv = process.env): ReminderConfig {
  const parsed = reminderEnv.safeParse(cleanEnv(env));
  if (!parsed.success) {
    throw new Error("Reminder settings:\n" + parsed.error.issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`).join("\n"));
  }
  const e = parsed.data;
  return { enabled: e.ELI_REMINDERS === "on", maxPerUser: e.ELI_MAX_REMINDERS_PER_USER, defaultTimeZone: e.ELI_DEFAULT_TIME_ZONE };
}

// ---------------------------------------------------------------------------------------------- time zones

export interface ZoneInfo {
  /** IANA name, e.g. America/New_York. */
  tz: string;
  /** True when Slack did not give a usable zone and the default was used. */
  assumed: boolean;
}

const TZ_TTL_MS = 60 * 60 * 1000;
const tzCache = new Map<string, { zone: ZoneInfo; at: number }>();

/** The person's time zone from users.info (`user.tz`), falling back to `defaultTz`. Never throws. */
export async function requesterTimeZone(client: WebClient, user: string, defaultTz: string, now = Date.now()): Promise<ZoneInfo> {
  const cached = tzCache.get(user);
  if (cached && now - cached.at < TZ_TTL_MS) return cached.zone;
  let zone: ZoneInfo = { tz: defaultTz, assumed: true };
  try {
    const res = await client.users.info({ user });
    const tz = res.user?.tz;
    if (tz && isValidTimeZone(tz)) zone = { tz, assumed: false };
  } catch {
    // users:read missing or Slack down: use the default, and say so in the context line.
  }
  tzCache.set(user, { zone, at: now });
  return zone;
}

interface Wall {
  y: number;
  mo: number;
  d: number;
  h: number;
  mi: number;
  s: number;
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function partsFormatter(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      weekday: "short",
    });
    fmtCache.set(tz, f);
  }
  return f;
}

function wallAt(tz: string, ms: number): Wall & { wd: string } {
  const get = (parts: Intl.DateTimeFormatPart[], type: string) => parts.find((p) => p.type === type)?.value ?? "0";
  const parts = partsFormatter(tz).formatToParts(new Date(ms));
  return {
    y: Number(get(parts, "year")),
    mo: Number(get(parts, "month")),
    d: Number(get(parts, "day")),
    h: Number(get(parts, "hour")) % 24,
    mi: Number(get(parts, "minute")),
    s: Number(get(parts, "second")),
    wd: get(parts, "weekday"),
  };
}

const wallMs = (w: Wall) => Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s);

/** The zone's UTC offset at instant `ms`, in milliseconds (EST is -5h). */
export function offsetAt(tz: string, ms: number): number {
  const whole = Math.floor(ms / 1000) * 1000;
  return wallMs(wallAt(tz, whole)) - whole;
}

/**
 * The instant a wall-clock time in `tz` refers to, resolved like Temporal's "compatible" option: a time that
 * happens twice (clocks go back) is the earlier one; a time that is skipped (clocks go forward) moves forward by
 * the gap, so 2:30 on a spring-forward night becomes 3:30. `shifted` is true in that second case.
 */
export function zonedToUtc(w: Wall, tz: string): { ms: number; shifted: boolean } {
  const local = wallMs(w);
  const day = 24 * 60 * 60 * 1000;
  const before = offsetAt(tz, local - day);
  const candidates = [...new Set([before, offsetAt(tz, local), offsetAt(tz, local + day)])]
    .map((o) => local - o)
    .filter((t) => offsetAt(tz, t) === local - t)
    .sort((a, b) => a - b);
  if (candidates.length) return { ms: candidates[0]!, shifted: false };
  return { ms: local - before, shifted: true };
}

function fmtOffset(ms: number): string {
  const sign = ms < 0 ? "-" : "+";
  const mins = Math.round(Math.abs(ms) / 60000);
  return `${sign}${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
}

const pad = (n: number, len = 2) => String(n).padStart(len, "0");

/** "2026-11-19T09:00:00-05:00" in the given zone. */
export function isoInZone(ms: number, tz: string): string {
  const w = wallAt(tz, ms);
  return `${w.y}-${pad(w.mo)}-${pad(w.d)}T${pad(w.h)}:${pad(w.mi)}:${pad(w.s)}${fmtOffset(offsetAt(tz, ms))}`;
}

/** "Thu, Nov 19, 2026, 9:00 AM EST". */
export function formatLocal(ms: number, tz: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
    .format(new Date(ms))
    .replace(/[  ]/g, " ");
}

/** One line for the request so the model can turn "Thursday morning" into a date. */
export function reminderContext(zone: ZoneInfo, now: number, people?: ReadonlyMap<string, string>): string {
  const w = wallAt(zone.tz, now);
  const lines = [
    `[Context for reminders] Now: ${w.wd} ${w.y}-${pad(w.mo)}-${pad(w.d)} ${pad(w.h)}:${pad(w.mi)} ${zone.tz} ` +
      `(UTC${fmtOffset(offsetAt(zone.tz, now))}), the requester's time zone` +
      (zone.assumed ? " (assumed: Slack did not give theirs; say so when you state a time)" : "") +
      ". Read times they give in this zone unless they name another.",
  ];
  if (people?.size) {
    const list = [...people].slice(0, 25).map(([id, name]) => `${name} = ${id}`);
    lines.push(`People in this thread, for mention_user_ids: ${list.join(", ")}.`);
  }
  return lines.join("\n");
}

/**
 * Add a context note to the request as a separate text block on the last user turn. It goes in the messages, not
 * the system prompt or a tool description, so it never breaks the prompt cache.
 */
export function withContextNote(messages: Anthropic.Beta.BetaMessageParam[], note: string): Anthropic.Beta.BetaMessageParam[] {
  const out = [...messages];
  const last = out.at(-1);
  if (last?.role === "user") {
    const blocks: Anthropic.Beta.BetaContentBlockParam[] =
      typeof last.content === "string" ? [{ type: "text", text: last.content }] : [...last.content];
    out[out.length - 1] = { role: "user", content: [...blocks, { type: "text", text: note }] };
  } else {
    out.push({ role: "user", content: note });
  }
  return out;
}

const WHEN_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$/i;

export interface ResolvedTime {
  /** Unix seconds, for chat.scheduleMessage post_at. */
  postAt: number;
  /** "Thu, Nov 19, 2026, 9:00 AM EST" in the requester's zone. */
  local: string;
  iso: string;
  /** The requested wall time did not exist (clocks went forward) and was moved later. */
  shifted: boolean;
}

/**
 * Parse `when`: ISO 8601 with an offset ("2026-11-19T09:00-05:00", "...Z"), or a local date and time
 * ("2026-11-19T09:00", "2026-11-19 09:00") read in the requester's zone. Then check it is between 2 minutes and
 * 120 days away. Errors are ToolInputErrors written for the model.
 */
export function resolveWhen(when: string, zone: ZoneInfo, now: number): ResolvedTime {
  const m = WHEN_RE.exec(when.trim());
  if (!m) {
    throw new ToolInputError(
      `Could not read the time "${when.slice(0, 60)}". Use a date and time like 2026-11-19T09:00 (in the requester's time zone) or with an offset like 2026-11-19T09:00-05:00.`,
    );
  }
  const [, ys, mos, ds, hs, mis, ss, off] = m;
  const w: Wall = { y: Number(ys), mo: Number(mos), d: Number(ds), h: Number(hs), mi: Number(mis), s: Number(ss ?? 0) };
  const daysInMonth = new Date(Date.UTC(w.y, w.mo, 0)).getUTCDate();
  if (w.mo < 1 || w.mo > 12 || w.d < 1 || w.d > daysInMonth || w.h > 23 || w.mi > 59 || w.s > 59) {
    throw new ToolInputError(`"${when.slice(0, 60)}" is not a real date and time.`);
  }
  let ms: number;
  let shifted = false;
  if (off) {
    let offsetMs = 0;
    if (off.toUpperCase() !== "Z") {
      const digits = off.replace(":", "");
      const mins = Number(digits.slice(1, 3)) * 60 + Number(digits.slice(3, 5));
      if (mins > 14 * 60) throw new ToolInputError(`"${off}" is not a valid UTC offset.`);
      offsetMs = (digits[0] === "-" ? -1 : 1) * mins * 60000;
    }
    ms = wallMs(w) - offsetMs;
  } else {
    ({ ms, shifted } = zonedToUtc(w, zone.tz));
  }
  const nowText = formatLocal(now, zone.tz);
  if (ms < now + MIN_AHEAD_MS) {
    throw new ToolInputError(
      `${formatLocal(ms, zone.tz)} is ${ms <= now ? "in the past" : "less than 2 minutes away"} (it is now ${nowText}). Reminders must be at least 2 minutes ahead.`,
    );
  }
  if (ms > now + MAX_AHEAD_MS) {
    throw new ToolInputError(`${formatLocal(ms, zone.tz)} is more than 120 days away, which is Slack's limit (it is now ${nowText}).`);
  }
  return { postAt: Math.floor(ms / 1000), local: formatLocal(ms, zone.tz), iso: isoInZone(ms, zone.tz), shifted };
}

// ---------------------------------------------------------------------------------------------- text, mentions

const MENTION_RE = /<@([UW][A-Z0-9]{2,})(?:\|[^>]*)?>/g;
export const USER_ID_RE = /^[UW][A-Z0-9]{2,}$/;

/**
 * Remove everything that would notify more than named people: <!channel>, <!here>, <!everyone>, user-group
 * mentions (<!subteam^S123>) and any other <!...> command, plus bare @channel / @here / @everyone.
 */
export function stripBroadcasts(text: string): { text: string; removed: string[] } {
  const removed: string[] = [];
  let out = text.replace(/<!([^>]*)>/g, (token) => {
    removed.push(token);
    return "";
  });
  out = out.replace(/(^|[^\w@])@(channel|here|everyone)\b/gi, (_all, lead: string, word: string) => {
    removed.push(`@${word}`);
    return `${lead}${word}`;
  });
  return { text: out.replace(/[ \t]{2,}/g, " ").trim(), removed };
}

export function mentionsIn(text: string): string[] {
  return [...new Set([...text.matchAll(MENTION_RE)].map((m) => m[1]!))];
}

const OWNER_RE = /^(?::alarm_clock:|⏰) Reminder from <@([UW][A-Z0-9]{2,})(?:\|[^>]*)?>: ([\s\S]*)$/;

/** The text Slack will post. The owner is read back from this prefix (see parseReminderText). */
export function buildReminderText(requester: string, mentions: readonly string[], body: string): string {
  const lead = mentions.filter((id) => id !== requester).map((id) => `<@${id}>`).join(" ");
  return `:alarm_clock: Reminder from <@${requester}>: ${lead ? `${lead} ` : ""}${body}`;
}

/** Undefined for scheduled messages that are not Eli reminders. */
export function parseReminderText(text: string | undefined): { owner: string; body: string } | undefined {
  const m = OWNER_RE.exec(text ?? "");
  return m ? { owner: m[1]!, body: m[2]! } : undefined;
}

// ---------------------------------------------------------------------------------------------- Slack helpers

export function slackError(err: unknown): string | undefined {
  return (err as { data?: { error?: string } } | undefined)?.data?.error;
}

export function describeScheduleError(code: string | undefined): string {
  switch (code) {
    case "time_in_past":
      return "That time has already passed.";
    case "time_too_far":
      return "That time is more than 120 days away, which is Slack's limit.";
    case "restricted_too_many":
      return "Too many messages are already scheduled in this channel around that time. Try a slightly different time.";
    case "not_in_channel":
    case "channel_not_found":
      return "I can't post in this channel any more.";
    case "missing_scope":
      return "My Slack app is missing a permission for this.";
    default:
      return "Slack would not schedule it.";
  }
}

export interface ScheduledReminder {
  id: string;
  channel: string;
  postAt: number;
  owner: string;
  body: string;
}

const LIST_PAGE_LIMIT = 100;
const LIST_MAX_PAGES = 20;

/**
 * Eli's scheduled reminders (only messages this bot token scheduled are ever returned), optionally in one
 * channel. Messages without the reminder prefix are skipped.
 */
export async function listScheduledReminders(client: WebClient, channel?: string): Promise<ScheduledReminder[]> {
  const out: ScheduledReminder[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < LIST_MAX_PAGES; page++) {
    const res = await client.chat.scheduledMessages.list({ ...(channel ? { channel } : {}), limit: LIST_PAGE_LIMIT, ...(cursor ? { cursor } : {}) });
    for (const m of res.scheduled_messages ?? []) {
      const parsed = parseReminderText(m.text);
      if (!parsed || !m.id || !m.channel_id || m.post_at === undefined) continue;
      out.push({ id: m.id, channel: m.channel_id, postAt: m.post_at, ...parsed });
    }
    cursor = res.response_metadata?.next_cursor || undefined;
    if (!cursor) break;
  }
  return out.sort((a, b) => a.postAt - b.postAt);
}

const MEMBERS_PAGE_LIMIT = 200;
const MEMBERS_MAX_PAGES = 25;

/** Everyone in the conversation (paginated; needs channels:read / groups:read / im:read / mpim:read). */
export async function conversationMembers(client: WebClient, channel: string): Promise<Set<string>> {
  const out = new Set<string>();
  let cursor: string | undefined;
  for (let page = 0; page < MEMBERS_MAX_PAGES; page++) {
    const res = await client.conversations.members({ channel, limit: MEMBERS_PAGE_LIMIT, ...(cursor ? { cursor } : {}) });
    for (const id of res.members ?? []) out.add(id);
    cursor = res.response_metadata?.next_cursor || undefined;
    if (!cursor) break;
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- pending store

export type ReminderStatus = "pending" | "scheduling" | "scheduled" | "cancelled" | "expired" | "failed";

export interface PendingReminder {
  id: string;
  requester: string;
  channel: string;
  /** Where the reminder posts: the thread, or undefined for the channel itself. */
  threadTs?: string;
  /** The thread the preview was posted in. */
  previewThreadTs: string;
  previewTs?: string;
  postAt: number;
  text: string;
  /** Preview text with mentions shown as names, so the preview itself pings nobody. */
  displayBody: string;
  local: string;
  timeZone: string;
  createdAt: number;
  status: ReminderStatus;
  scheduledId?: string;
}

export type NewReminder = Omit<PendingReminder, "id" | "createdAt" | "status" | "previewTs" | "scheduledId">;

/** In-memory previews awaiting a click. Finished ones are kept for a day so a late second click is a no-op. */
export class ReminderStore {
  private readonly items = new Map<string, PendingReminder>();
  readonly ttlMs: number;
  private readonly now: () => number;
  private readonly keepMs = 24 * 60 * 60 * 1000;

  constructor(opts: { ttlMs?: number; now?: () => number } = {}) {
    this.ttlMs = opts.ttlMs ?? PENDING_TTL_MS;
    this.now = opts.now ?? Date.now;
  }

  add(input: NewReminder): PendingReminder {
    this.sweep();
    const r: PendingReminder = { ...input, id: `r${randomBytes(9).toString("base64url")}`, createdAt: this.now(), status: "pending" };
    this.items.set(r.id, r);
    return r;
  }

  get(id: string): PendingReminder | undefined {
    return this.items.get(id);
  }

  remove(id: string): void {
    this.items.delete(id);
  }

  isExpired(r: PendingReminder): boolean {
    return this.now() - r.createdAt > this.ttlMs;
  }

  /** Previews still waiting for the requester, optionally in one channel. */
  pendingFor(requester: string, channel?: string): PendingReminder[] {
    return [...this.items.values()].filter(
      (r) => r.status === "pending" && !this.isExpired(r) && r.requester === requester && (!channel || r.channel === channel),
    );
  }

  /** Moves pending -> `to` synchronously; false if it was not pending (second click, or already handled). */
  transition(id: string, to: ReminderStatus, from: ReminderStatus = "pending"): boolean {
    const r = this.items.get(id);
    if (!r || r.status !== from) return false;
    r.status = to;
    return true;
  }

  private sweep(): void {
    const now = this.now();
    for (const [id, r] of this.items) if (now - r.createdAt > this.ttlMs + this.keepMs) this.items.delete(id);
  }
}

// ---------------------------------------------------------------------------------------------- preview blocks

type Block = slackTypes.KnownBlock;

function escapeMrkdwn(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Replace <@U..> with @Name so showing the text pings no one. */
export function displayText(text: string, names: ReadonlyMap<string, string>): string {
  return text.replace(MENTION_RE, (_all, id: string) => `@${escapeMrkdwn(names.get(id) ?? "someone")}`);
}

function quote(text: string): string {
  return text
    .split("\n")
    .map((l) => `> ${l}`)
    .join("\n");
}

function details(r: PendingReminder, requesterName: string): string {
  return `*When:* ${r.local} (${r.timeZone})\n*Where:* ${r.threadTs ? "this thread" : "this channel"}\n*Asked by:* ${escapeMrkdwn(requesterName)}`;
}

export function previewBlocks(r: PendingReminder, requesterName: string, ttlMinutes: number): { text: string; blocks: Block[] } {
  const value = `${r.id}|${r.requester}`;
  return {
    text: `Reminder preview for ${r.local}. Press Schedule to confirm.`,
    blocks: [
      { type: "section", text: { type: "mrkdwn", text: `:alarm_clock: *Reminder preview* (not scheduled yet)\n${details(r, requesterName)}` } },
      { type: "section", text: { type: "mrkdwn", text: quote(r.displayBody) } },
      {
        type: "actions",
        block_id: "eli_reminder_actions",
        elements: [
          { type: "button", action_id: SCHEDULE_ACTION, style: "primary", text: { type: "plain_text", text: "Schedule" }, value },
          { type: "button", action_id: CANCEL_ACTION, text: { type: "plain_text", text: "Cancel" }, value },
        ],
      },
      {
        type: "context",
        elements: [{ type: "mrkdwn", text: `Only ${escapeMrkdwn(requesterName)} can press Schedule. This preview expires in ${ttlMinutes} minutes.` }],
      },
    ],
  };
}

export function outcomeBlocks(r: PendingReminder | undefined, outcome: string): { text: string; blocks: Block[] } {
  const blocks: Block[] = [{ type: "section", text: { type: "mrkdwn", text: outcome } }];
  if (r) blocks.push({ type: "section", text: { type: "mrkdwn", text: quote(r.displayBody) } });
  return { text: outcome.replace(/[*_]/g, ""), blocks };
}

// ---------------------------------------------------------------------------------------------- buttons

export interface ReminderActionDeps {
  maxPerUser: number;
  allowedTeamId?: string;
  now?: () => number;
  log?: (line: string) => void;
}

/** The parts of a block_actions payload the handler reads (Bolt's BlockAction<ButtonAction> fits). */
export interface ReminderClick {
  team?: { id: string } | null;
  user: { id: string };
  channel?: { id: string };
  message?: { ts: string; thread_ts?: string };
  actions: Array<{ action_id: string; value?: string }>;
}

/**
 * One Schedule or Cancel click. Only the requester may act; anyone else gets an ephemeral note. Every second
 * click, and any click on a finished preview, does nothing. Never throws.
 */
export async function handleReminderClick(client: WebClient, body: ReminderClick, store: ReminderStore, deps: ReminderActionDeps): Promise<void> {
  const log = deps.log ?? ((line: string) => console.error(line));
  const now = deps.now ?? Date.now;
  if (deps.allowedTeamId && body.team?.id && body.team.id !== deps.allowedTeamId) return;
  const action = body.actions[0];
  if (!action) return;
  const [id = "", encodedOwner = ""] = (action.value ?? "").split("|");
  const r = store.get(id);
  const owner = r?.requester ?? encodedOwner;
  const channel = body.channel?.id ?? r?.channel;
  const messageTs = body.message?.ts ?? r?.previewTs;
  if (!channel) return;

  const safe = async (what: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (err) {
      log(`[eli] reminder ${what} failed: ${slackError(err) ?? (err as Error).message}`);
    }
  };
  const update = (content: { text: string; blocks: Block[] }) =>
    messageTs ? safe("preview update", () => client.chat.update({ channel, ts: messageTs, ...content })) : Promise.resolve();

  if (body.user.id !== owner) {
    const threadTs = body.message?.thread_ts ?? r?.previewThreadTs;
    await safe("ephemeral", () =>
      client.chat.postEphemeral({
        channel,
        user: body.user.id,
        ...(threadTs ? { thread_ts: threadTs } : {}),
        text: owner ? `Only <@${owner}> can confirm or cancel this reminder.` : "Only the person who asked can confirm this reminder.",
      }),
    );
    return;
  }

  if (!r) {
    // Eli restarted, or the preview is older than a day.
    await update(outcomeBlocks(undefined, ":hourglass: This reminder preview expired, so nothing was scheduled. Ask me again."));
    return;
  }
  if (r.status === "pending" && store.isExpired(r)) {
    if (store.transition(r.id, "expired")) {
      await update(outcomeBlocks(r, ":hourglass: This reminder preview expired, so nothing was scheduled. Ask me again."));
    }
    return;
  }

  if (action.action_id === CANCEL_ACTION) {
    if (store.transition(r.id, "cancelled")) await update(outcomeBlocks(r, ":x: Cancelled. Nothing was scheduled."));
    return;
  }
  if (action.action_id !== SCHEDULE_ACTION) return;
  // Synchronous claim: a double click's second event finds "scheduling" and stops here.
  if (!store.transition(r.id, "scheduling")) return;

  const fail = async (reason: string) => {
    r.status = "failed";
    await update(outcomeBlocks(r, `:warning: Not scheduled: ${reason} Ask me again if you still want it.`));
  };

  if (r.postAt * 1000 < now() + MIN_AHEAD_AT_CONFIRM_MS) return fail("that time has already passed.");
  try {
    const mine = (await listScheduledReminders(client)).filter((s) => s.owner === r.requester).length;
    if (mine >= deps.maxPerUser) {
      return fail(`you already have ${mine} scheduled reminders (the limit is ${deps.maxPerUser}). Cancel one first.`);
    }
  } catch (err) {
    log(`[eli] reminder count failed: ${slackError(err) ?? (err as Error).message}`);
    return fail("I couldn't check how many reminders you already have.");
  }

  try {
    const res = await client.chat.scheduleMessage({
      channel: r.channel,
      text: r.text,
      post_at: r.postAt,
      ...(r.threadTs ? { thread_ts: r.threadTs } : {}),
      // No `metadata`: Slack does not post scheduled messages that carry it.
      parse: "none",
      link_names: false,
      unfurl_links: false,
      unfurl_media: false,
    });
    r.status = "scheduled";
    r.scheduledId = res.scheduled_message_id;
    await update(
      outcomeBlocks(
        r,
        `:white_check_mark: *Scheduled* for ${r.local} (${r.timeZone}) in ${r.threadTs ? "this thread" : "this channel"}. ` +
          "Slack will post it even if I'm offline. Ask me to list or cancel your reminders.",
      ),
    );
  } catch (err) {
    const code = slackError(err);
    log(`[eli] chat.scheduleMessage failed: ${code ?? (err as Error).message}`);
    await fail(describeScheduleError(code));
  }
}

/** Wire the Schedule and Cancel buttons. Socket Mode delivers the clicks; the manifest needs interactivity on. */
export function registerReminderActions(app: Pick<App, "action">, store: ReminderStore, deps: ReminderActionDeps): void {
  for (const actionId of [SCHEDULE_ACTION, CANCEL_ACTION]) {
    app.action(actionId, async ({ ack, body, client }) => {
      await ack();
      if (body.type !== "block_actions") return;
      await handleReminderClick(client, body as unknown as ReminderClick, store, deps);
    });
  }
}

// ---------------------------------------------------------------------------------------------- prompt

export const REMINDERS_PROMPT = `## Reminders
- You can set a reminder that Slack posts later in this thread (or this channel): "remind me Thursday at 9 to send the deck", "ping @Sam tomorrow morning about the call". Use \`slack_schedule_reminder\`; use \`slack_list_reminders\` and \`slack_cancel_reminder\` for "what reminders do I have" and "cancel that".
- Work out the date and time from the "[Context for reminders]" line (the current time in the requester's time zone). Pass \`when\` as a local date and time like 2026-11-19T09:00. If they give no time, pick a sensible one (9:00 AM for "morning", 1:00 PM for "afternoon") and say so.
- Calling the tool only posts a preview. Nothing is scheduled until the person who asked presses **Schedule** on it, so tell them to. Always state the resolved local time from the tool result (\`resolved_local_time\`), for example "Thu, Nov 19 at 9:00 AM EST".
- To ping other people, put their user ids (from the context line) in \`mention_user_ids\`; don't write @names into the message. You can only ping the requester and people in this conversation, and in a DM with you only the requester. Never use @channel, @here or @everyone.
- Reminders post here only. You can't DM people, and you can't create calendar events (yet). If the tool refuses, explain why in one line.`;
