import { betaZodTool } from "@anthropic-ai/sdk/helpers/beta/zod";
import type { webApi } from "@slack/bolt";
import { z } from "zod";
import { NotAllowedError, UpstreamError } from "../../sharepoint/errors.js";
import { ToolInputError } from "../../tools/result.js";
import {
  MAX_MENTIONS,
  MAX_MESSAGE_CHARS,
  type ReminderConfig,
  type ReminderStore,
  USER_ID_RE,
  type ZoneInfo,
  buildReminderText,
  conversationMembers,
  displayText,
  formatLocal,
  isoInZone,
  listScheduledReminders,
  mentionsIn,
  outcomeBlocks,
  previewBlocks,
  reminderContext,
  requesterTimeZone,
  resolveWhen,
  slackError,
  stripBroadcasts,
} from "../reminders.js";
import { type AnyRunnableTool, jsonTool } from "./sharepoint.js";

type WebClient = webApi.WebClient;

export const SCHEDULE_REMINDER_TOOL = "slack_schedule_reminder";
export const LIST_REMINDERS_TOOL = "slack_list_reminders";
export const CANCEL_REMINDER_TOOL = "slack_cancel_reminder";

const SLACK_UNAVAILABLE = "Slack is temporarily unavailable or returned an unexpected error.";
/** A runaway loop cannot fill a thread with previews. */
const MAX_PREVIEWS_PER_REQUEST = 3;

export interface ReminderToolContext {
  client: WebClient;
  channel: string;
  /** The thread Eli is answering in. */
  threadTs: string;
  requester: string;
  botUserId: string;
  /** A 1:1 DM with Eli. Defaults to "channel id starts with D". */
  isDm?: boolean;
  zone: ZoneInfo;
  store: ReminderStore;
  maxPerUser: number;
  /** id -> display name, e.g. the names slack.ts already looked up for the thread. */
  names?: ReadonlyMap<string, string>;
  now?: () => number;
}

/**
 * Per-request reminder tools, bound to this conversation and requester. The model never schedules anything:
 * slack_schedule_reminder posts a preview whose Schedule button only the requester can press.
 */
export function reminderTools(ctx: ReminderToolContext): AnyRunnableTool[] {
  const now = ctx.now ?? Date.now;
  const isDm = ctx.isDm ?? ctx.channel.startsWith("D");
  const names = new Map(ctx.names ?? []);
  let members: Promise<Set<string>> | undefined;
  let previews = 0;

  const nameOf = async (id: string): Promise<string> => {
    const known = names.get(id);
    if (known) return known;
    try {
      const res = await ctx.client.users.info({ user: id });
      const p = res.user?.profile;
      const name = p?.display_name || p?.real_name || res.user?.real_name || res.user?.name || "someone";
      names.set(id, name);
      return name;
    } catch {
      return "someone";
    }
  };

  /** Throws a ToolInputError naming anyone who may not be pinged here. */
  const checkMentions = async (ids: string[]): Promise<void> => {
    const others = ids.filter((id) => id !== ctx.requester);
    if (others.includes(ctx.botUserId)) throw new ToolInputError("A reminder can't mention me.");
    if (!others.length) return;
    if (isDm) {
      throw new ToolInputError("In a DM with me I can only remind the person I'm talking to. Ask in a channel where everyone you want to ping is a member.");
    }
    let inChannel: Set<string>;
    try {
      inChannel = await (members ??= conversationMembers(ctx.client, ctx.channel));
    } catch (err) {
      members = undefined;
      const code = slackError(err);
      throw new ToolInputError(
        code === "missing_scope"
          ? "I can't check who is in this conversation (my Slack app is missing a permission), so I can only remind the requester."
          : "I couldn't check who is in this conversation, so I can only remind the requester right now.",
      );
    }
    const outside = others.filter((id) => !inChannel.has(id));
    if (outside.length) {
      throw new ToolInputError(
        `I can only ping people in this conversation. Not a member: ${outside.map((id) => names.get(id) ?? id).join(", ")}.`,
      );
    }
  };

  const ownCount = async (): Promise<number> => {
    try {
      const scheduled = (await listScheduledReminders(ctx.client)).filter((r) => r.owner === ctx.requester).length;
      return scheduled + ctx.store.pendingFor(ctx.requester).length;
    } catch (err) {
      throw new UpstreamError(`chat.scheduledMessages.list failed: ${slackError(err) ?? (err as Error).message}`);
    }
  };

  return [
    betaZodTool({
      name: SCHEDULE_REMINDER_TOOL,
      description:
        "Set up a reminder that Slack posts later in this conversation, even if Eli is offline. This does NOT schedule " +
        "it: it posts a preview with a Schedule button that only the requester can press (it expires in 15 minutes). " +
        "`when` is a local date and time in the requester's time zone, like 2026-11-19T09:00, or ISO 8601 with an " +
        "offset. It must be 2 minutes to 120 days ahead. Ping others only via mention_user_ids (people in this " +
        "conversation). The reminder automatically pings the requester. Tell the person the resolved_local_time and " +
        "that they must press Schedule.",
      inputSchema: z.object({
        when: z.string().min(1).max(64).describe("Local date and time, e.g. 2026-11-19T09:00, or with an offset."),
        message: z.string().min(1).max(4000).describe("What the reminder should say, without @mentions and without a leading \"Reminder:\" (Eli adds that). Under 1000 characters."),
        mention_user_ids: z
          .array(z.string().max(20))
          .max(MAX_MENTIONS)
          .optional()
          .describe("Slack user ids (U...) of people in this conversation to ping. Omit to remind only the requester."),
        in_thread: z.boolean().default(true).describe("Post in this thread (default) or false for the channel itself."),
      }),
      run: jsonTool(
        SCHEDULE_REMINDER_TOOL,
        async (input: { when: string; message: string; mention_user_ids?: string[]; in_thread?: boolean }) => {
          if (previews >= MAX_PREVIEWS_PER_REQUEST) {
            throw new ToolInputError("I've already posted several previews for this request. Ask the person to use one of those.");
          }
          // The scheduled text already starts "Reminder from <@you>:", so drop a duplicate "Reminder:" lead-in.
          const { text: body, removed } = stripBroadcasts(input.message.replace(/^\s*(?:reminder\s*[:\-–]\s*)+/i, ""));
          if (!body) throw new ToolInputError("The reminder message is empty.");
          if (body.length > MAX_MESSAGE_CHARS) {
            throw new ToolInputError(`The reminder is ${body.length} characters; keep it under ${MAX_MESSAGE_CHARS}.`);
          }
          const requested = (input.mention_user_ids ?? []).map((s) => s.trim().replace(/^<@|>$/g, "")).filter(Boolean);
          const bad = requested.filter((id) => !USER_ID_RE.test(id));
          if (bad.length) throw new ToolInputError(`Not Slack user ids: ${bad.join(", ")}. Use ids like U0123ABCD.`);
          const inline = mentionsIn(body);
          const allMentions = [...new Set([...requested, ...inline])];
          if (allMentions.length > MAX_MENTIONS) throw new ToolInputError(`A reminder can ping at most ${MAX_MENTIONS} people.`);
          await checkMentions(allMentions);

          const time = resolveWhen(input.when, ctx.zone, now());
          const count = await ownCount();
          if (count >= ctx.maxPerUser) {
            throw new ToolInputError(
              `The requester already has ${count} reminders scheduled or awaiting confirmation (the limit is ${ctx.maxPerUser}). They can cancel one first.`,
            );
          }

          const leading = requested.filter((id) => id !== ctx.requester && !inline.includes(id));
          const text = buildReminderText(ctx.requester, leading, body);
          for (const id of allMentions) await nameOf(id);
          const requesterName = await nameOf(ctx.requester);
          const pending = ctx.store.add({
            requester: ctx.requester,
            channel: ctx.channel,
            threadTs: input.in_thread === false ? undefined : ctx.threadTs,
            previewThreadTs: ctx.threadTs,
            postAt: time.postAt,
            text,
            displayBody: displayText(text, names),
            local: time.local,
            timeZone: ctx.zone.tz,
          });
          try {
            const res = await ctx.client.chat.postMessage({
              channel: ctx.channel,
              thread_ts: ctx.threadTs,
              ...previewBlocks(pending, requesterName, Math.round(ctx.store.ttlMs / 60000)),
              unfurl_links: false,
              unfurl_media: false,
            });
            pending.previewTs = res.ts;
          } catch (err) {
            ctx.store.remove(pending.id);
            throw new UpstreamError(`preview post failed: ${slackError(err) ?? (err as Error).message}`);
          }
          previews++;
          return {
            status: "awaiting_confirmation",
            pending_id: pending.id,
            resolved_local_time: time.local,
            resolved_iso: time.iso,
            time_zone: ctx.zone.tz + (ctx.zone.assumed ? " (assumed; Slack did not give the requester's zone)" : ""),
            posts_in: pending.threadTs ? "this thread" : "this channel",
            pings: allMentions.length ? ["the requester", ...allMentions.filter((id) => id !== ctx.requester).map((id) => names.get(id) ?? id)] : ["the requester"],
            ...(time.shifted ? { note_dst: "That local time doesn't exist that day (clocks go forward), so it moved an hour later." } : {}),
            ...(removed.length ? { removed_broadcasts: removed } : {}),
            next: "Nothing is scheduled yet. Tell the person to press Schedule on the preview above within 15 minutes.",
          };
        },
        SLACK_UNAVAILABLE,
      ),
    }),

    betaZodTool({
      name: LIST_REMINDERS_TOOL,
      description:
        "List the requester's own reminders in this conversation: ones Slack has scheduled, and previews still " +
        "waiting for them to press Schedule. Times are in the requester's time zone.",
      inputSchema: z.object({}),
      run: jsonTool(
        LIST_REMINDERS_TOOL,
        async () => {
          let scheduled;
          try {
            scheduled = (await listScheduledReminders(ctx.client, ctx.channel)).filter((r) => r.owner === ctx.requester);
          } catch (err) {
            throw new UpstreamError(`chat.scheduledMessages.list failed: ${slackError(err) ?? (err as Error).message}`);
          }
          const pending = ctx.store.pendingFor(ctx.requester, ctx.channel);
          return {
            time_zone: ctx.zone.tz,
            scheduled: scheduled.map((r) => ({
              id: r.id,
              local_time: formatLocal(r.postAt * 1000, ctx.zone.tz),
              iso: isoInZone(r.postAt * 1000, ctx.zone.tz),
              message: displayText(r.body, names).slice(0, 300),
            })),
            awaiting_confirmation: pending.map((r) => ({ id: r.id, local_time: r.local, message: r.displayBody.slice(0, 300) })),
          };
        },
        SLACK_UNAVAILABLE,
      ),
    }),

    betaZodTool({
      name: CANCEL_REMINDER_TOOL,
      description:
        "Cancel one of the requester's own reminders in this conversation, by the id from slack_list_reminders " +
        "(a scheduled one, or a preview awaiting confirmation). Other people's reminders can't be cancelled.",
      inputSchema: z.object({ id: z.string().min(1).max(64).describe("The reminder id from slack_list_reminders.") }),
      run: jsonTool(
        CANCEL_REMINDER_TOOL,
        async ({ id }: { id: string }) => {
          const notYours = new NotAllowedError("No reminder of the requester's with that id in this conversation.");
          const key = id.trim();
          const pending = ctx.store.get(key);
          if (pending) {
            if (pending.requester !== ctx.requester || pending.channel !== ctx.channel) throw notYours;
            if (!ctx.store.transition(key, "cancelled")) throw new ToolInputError("That preview was already handled.");
            if (pending.previewTs) {
              await ctx.client.chat
                .update({ channel: pending.channel, ts: pending.previewTs, ...outcomeBlocks(pending, ":x: Cancelled. Nothing was scheduled.") })
                .catch(() => undefined);
            }
            return { cancelled: key, was: "awaiting_confirmation", local_time: pending.local };
          }
          let mine;
          try {
            mine = (await listScheduledReminders(ctx.client, ctx.channel)).find((r) => r.id === key && r.owner === ctx.requester);
          } catch (err) {
            throw new UpstreamError(`chat.scheduledMessages.list failed: ${slackError(err) ?? (err as Error).message}`);
          }
          if (!mine) throw notYours;
          try {
            await ctx.client.chat.deleteScheduledMessage({ channel: mine.channel, scheduled_message_id: mine.id });
          } catch (err) {
            const code = slackError(err);
            if (code === "invalid_scheduled_message_id") throw new ToolInputError("That reminder has already been posted or cancelled.");
            throw new UpstreamError(`chat.deleteScheduledMessage failed: ${code ?? (err as Error).message}`);
          }
          return { cancelled: mine.id, was: "scheduled", local_time: formatLocal(mine.postAt * 1000, ctx.zone.tz) };
        },
        SLACK_UNAVAILABLE,
      ),
    }),
  ];
}

export interface ReminderRequest {
  client: WebClient;
  channel: string;
  threadTs: string;
  requester: string;
  botUserId: string;
  isDm?: boolean;
  names?: ReadonlyMap<string, string>;
  store: ReminderStore;
  config: ReminderConfig;
  now?: () => number;
}

/**
 * Everything slack.ts needs per request: the requester's time zone (users.info, cached), the three tools, and a
 * context line for the model ("Now: Thu 2026-11-12 14:03 America/New_York ..."). Tools are empty when off.
 */
export async function reminderRequest(req: ReminderRequest): Promise<{ tools: AnyRunnableTool[]; context?: string }> {
  if (!req.config.enabled) return { tools: [] };
  const now = req.now ?? Date.now;
  const people = new Map([...(req.names ?? [])].filter(([id]) => id !== req.botUserId));
  const zone = await requesterTimeZone(req.client, req.requester, req.config.defaultTimeZone, now());
  return {
    tools: reminderTools({ ...req, zone, maxPerUser: req.config.maxPerUser }),
    context: reminderContext(zone, now(), people),
  };
}
