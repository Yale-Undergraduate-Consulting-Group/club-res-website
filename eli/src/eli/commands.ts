import fs from "node:fs/promises";
import type { App, RespondArguments, SlashCommand, webApi } from "@slack/bolt";
import { stripBroadcasts } from "./reminders.js";
import { slackPayload } from "./reply.js";
import { STATS_COMMAND } from "./statsCommand.js";
import { normalizeDmCommand } from "./stats.js";

export const HELP_COMMAND = "/help";
export const REQUESTS_COMMAND = "/requests";
export const ONBOARD_COMMAND = "/onboard";
export const CONSOLE_COMMAND = "/eli-console";
export const MAX_REQUEST_CHARS = 2000;
export const REQUESTS_PER_USER_PER_DAY = 5;
const DAY_MS = 24 * 60 * 60 * 1000;

// ---------------------------------------------------------------------------------------------- /help

export interface HelpFeatures {
  webSearch: boolean;
  reminders: boolean;
  /** SharePoint/OneDrive or Google Drive links. */
  links: boolean;
  /** Someone receives /requests. */
  requests: boolean;
  isAdmin: boolean;
  /** This person may open the web console. */
  console?: boolean;
  /** Requests per person per hour, shown in the onboarding tour. */
  userPerHour?: number;
}

/** [what to type, what it does]; `asks` are already formatted. Each summary stays under 10 words (a test checks). */
export function helpEntries(f: HelpFeatures): { commands: [string, string][]; asks: [string, string][] } {
  const commands: [string, string][] = [
    [HELP_COMMAND, "List Eli's commands."],
    [ONBOARD_COMMAND, "A one-minute tour for new members."],
    [STATS_COMMAND, "See your usage and remaining limits."],
  ];
  if (f.isAdmin) commands.push([`${STATS_COMMAND} [days]`, "Workspace-wide usage and cost (admins only)."]);
  if (f.requests) commands.push([`${REQUESTS_COMMAND} <your idea>`, "Send Eli's maintainer a feature request."]);
  if (f.console) commands.push([CONSOLE_COMMAND, "Get a sign-in link to the usage dashboard."]);

  const asks: [string, string][] = [
    ["`stats`", "Your usage, free (DM only)."],
    ["`help`", "This list (DM only)."],
    ["`onboard`", "The quick tour (DM only)."],
    ["Any question or draft", "Emails, outlines, summaries, quick answers."],
    ["@Eli in a thread", "Summarize or answer using that thread."],
    ["Attach a file", "Read and summarize pptx, docx, xlsx, pdf."],
  ];
  if (f.links) asks.push(["Paste a SharePoint or Drive link", "Search and read files in that folder."]);
  if (f.webSearch) asks.push(["Ask about current events", "I search the web, with sources."]);
  if (f.reminders) asks.push(["\"remind me Friday 9am to …\"", "Schedule a reminder in this thread."]);
  return { commands, asks };
}

export function helpText(f: HelpFeatures): string {
  const { commands, asks } = helpEntries(f);
  const line = ([what, does]: [string, string]) => `• \`${what}\`: ${does}`;
  return [
    "**Eli commands**",
    ...commands.map(line),
    "",
    "**Things you can ask me** (DM me, or tag @Eli in a channel thread)",
    ...asks.map(([what, does]) => `• ${what}: ${does}`),
  ].join("\n");
}

/** The /onboard tour. Lists only what is switched on. */
export function onboardText(f: HelpFeatures): string {
  const tries = [
    '• "Draft a short cold email to a fintech startup about a pricing project"',
    "• In a channel thread: `@Eli summarize this thread and list the open questions`",
    '• Attach a deck or doc and ask "what are the three key takeaways?"',
  ];
  if (f.links) tries.push('• Paste a SharePoint or Google Drive folder link and ask "any past pricing projects in here?"');
  if (f.webSearch) tries.push('• "What\'s the latest public news on <company>?" (I\'ll search the web and cite sources)');
  if (f.reminders) tries.push('• "Remind me Thursday at 9am to send the deck" (press **Schedule** to confirm)');
  const limit = f.userPerHour ? `You get ${f.userPerHour} requests an hour` : "There's an hourly request limit";
  return [
    "**Welcome to Eli** :wave:",
    "I'm YUCG's assistant in Slack. Here's the one-minute tour.",
    "",
    "**1. How to talk to me**",
    "• DM me anything, or tag `@Eli` in a channel thread.",
    "• Reply in the same thread to follow up. I remember that thread, not your other chats.",
    "• In channels I only read threads I'm tagged in.",
    "",
    "**2. Try one of these**",
    ...tries,
    "",
    "**3. Good to know**",
    "• Don't paste NDA or client-confidential material unless your project lead says it's OK.",
    "• I can be wrong. Check numbers, names and links before anything goes to a client.",
    `• ${limit}. Send me \`stats\` to see what's left.`,
    "",
    "**4. Commands**",
    `\`${HELP_COMMAND}\` all commands · \`${STATS_COMMAND}\` your usage${f.requests ? ` · \`${REQUESTS_COMMAND} <idea>\` suggest a feature` : ""}`,
  ].join("\n");
}

/** "onboard", "get started" and similar, sent on its own in a DM (or after @Eli). */
export function isOnboardRequest(text: string): boolean {
  return /^\/?(?:onboard(?:ing)?|onboard me|get(?:ting)? started|start here|tour)(?:\s+please)?$/.test(normalizeDmCommand(text));
}

/** "console" or "dashboard" sent on its own in a DM (or after @Eli). */
export function isConsoleRequest(text: string): boolean {
  return /^\/?(?:console|dashboard|eli console|eli-console)$/.test(normalizeDmCommand(text));
}

/** "help" or "commands" sent on its own in a DM (or after @Eli). */
export function isHelpRequest(text: string): boolean {
  return /^\/?(?:help|commands|eli help)(?:\s+please)?$/.test(normalizeDmCommand(text));
}

// ---------------------------------------------------------------------------------------------- /requests


/** Per-person daily cap so /requests can't be used to spam the maintainer. In memory; resets on restart. */
export class RequestLimiter {
  private readonly sent = new Map<string, number[]>();
  constructor(
    private readonly perDay = REQUESTS_PER_USER_PER_DAY,
    private readonly now: () => number = Date.now,
  ) {}
  take(user: string): boolean {
    const t = this.now();
    const mine = (this.sent.get(user) ?? []).filter((x) => t - x < DAY_MS);
    if (mine.length >= this.perDay) {
      this.sent.set(user, mine);
      return false;
    }
    mine.push(t);
    this.sent.set(user, mine);
    return true;
  }
}

const fallbackLimiter = new RequestLimiter();

export interface CommandDeps {
  allowedTeamId?: string;
  adminUserIds: ReadonlySet<string>;
  /** Who gets pinged for /requests. Empty disables the command. */
  requestRecipients: ReadonlySet<string>;
  /** JSONL backup of every request, so none are lost if a DM fails. */
  requestLogPath: string;
  features: Omit<HelpFeatures, "isAdmin" | "requests" | "console">;
  /** A one-time console sign-in link for viewers, undefined for anyone else. Omit when the console is off. */
  consoleLink?: (user: string) => string | undefined;
  /** Who may open the console (board + admins). */
  consoleViewers?: ReadonlySet<string>;
  requestLimiter?: RequestLimiter;
  log?: (line: string) => void;
  now?: () => Date;
}

export interface CommandRequest {
  command: Pick<SlashCommand, "user_id" | "team_id" | "text" | "channel_id">;
  ack: () => Promise<void>;
  respond: (message: RespondArguments) => Promise<unknown>;
  client: { chat: Pick<webApi.WebClient["chat"], "postMessage"> };
}

type HelpDeps = Pick<CommandDeps, "adminUserIds" | "requestRecipients" | "features" | "consoleLink" | "consoleViewers">;
const featuresFor = (user: string, deps: HelpDeps): HelpFeatures => ({
  ...deps.features,
  isAdmin: deps.adminUserIds.has(user),
  requests: deps.requestRecipients.size > 0,
  console: Boolean(deps.consoleLink && deps.consoleViewers?.has(user)),
});

export function helpFor(user: string, deps: HelpDeps): string {
  return helpText(featuresFor(user, deps));
}

/** The reply to `/eli-console` or "console". */
export function consoleReplyFor(user: string, deps: Pick<CommandDeps, "consoleLink">): string {
  if (!deps.consoleLink) return "The Eli console isn't turned on.";
  const link = deps.consoleLink(user);
  if (!link) return "The Eli console is only for board members and admins.";
  return `:bar_chart: **Eli console:** [open the usage dashboard](${link})\nThis link is just for you. It works once and expires in 10 minutes, then you stay signed in for 12 hours.`;
}

async function start(req: CommandRequest, deps: CommandDeps, name: string) {
  const log = deps.log ?? ((line) => console.error(line));
  try {
    await req.ack();
  } catch (err) {
    log(`[eli] could not acknowledge ${name}: ${(err as Error).message}`);
  }
  const reply = async (markdown: string) => {
    try {
      await req.respond({ response_type: "ephemeral", ...slackPayload(markdown) });
    } catch {
      try {
        await req.respond({ response_type: "ephemeral", text: markdown });
      } catch (err) {
        log(`[eli] could not answer ${name}: ${(err as Error).message}`);
      }
    }
  };
  const allowed = !(deps.allowedTeamId && req.command.team_id && req.command.team_id !== deps.allowedTeamId);
  return { log, reply, allowed };
}

export function onboardFor(user: string, deps: HelpDeps): string {
  return onboardText(featuresFor(user, deps));
}

/** `/eli-console`. Public for tests. Never throws. */
export async function handleConsoleCommand(req: CommandRequest, deps: CommandDeps): Promise<void> {
  const { reply, allowed } = await start(req, deps, CONSOLE_COMMAND);
  if (allowed) await reply(consoleReplyFor(req.command.user_id, deps));
}

/** `/onboard`. Public for tests. Never throws. */
export async function handleOnboardCommand(req: CommandRequest, deps: CommandDeps): Promise<void> {
  const { reply, allowed } = await start(req, deps, ONBOARD_COMMAND);
  if (allowed) await reply(onboardFor(req.command.user_id, deps));
}

/** `/help`. Public for tests. Never throws. */
export async function handleHelpCommand(req: CommandRequest, deps: CommandDeps): Promise<void> {
  const { reply, allowed } = await start(req, deps, HELP_COMMAND);
  if (allowed) await reply(helpFor(req.command.user_id, deps));
}

/** `/requests <idea>`: DMs each recipient and confirms privately to the sender. Public for tests. Never throws. */
export async function handleRequestsCommand(req: CommandRequest, deps: CommandDeps): Promise<void> {
  const { log, reply, allowed } = await start(req, deps, REQUESTS_COMMAND);
  if (!allowed) return;
  const { user_id: user, channel_id: channel } = req.command;
  if (deps.requestRecipients.size === 0) return reply("Feature requests aren't set up for Eli yet.");

  const { text: idea } = stripBroadcasts(req.command.text ?? "");
  if (!idea) return reply(`Add your idea after the command, for example: \`${REQUESTS_COMMAND} let Eli read Notion pages\``);
  if (idea.length > MAX_REQUEST_CHARS) {
    return reply(`That's ${idea.length.toLocaleString()} characters; please keep it under ${MAX_REQUEST_CHARS.toLocaleString()}.`);
  }
  if (!(deps.requestLimiter ?? fallbackLimiter).take(user)) {
    return reply(`You've sent ${REQUESTS_PER_USER_PER_DAY} requests today, which is the daily limit. Try again tomorrow.`);
  }

  const at = (deps.now?.() ?? new Date()).toISOString();
  try {
    await fs.appendFile(deps.requestLogPath, JSON.stringify({ at, user, channel, text: idea }) + "\n", { encoding: "utf8", mode: 0o600 });
  } catch (err) {
    log(`[eli] could not write feature request log: ${(err as Error).message}`);
  }

  // Only public/private channel ids render as a link; DMs and group DMs are left out.
  const where = /^[CG]/.test(channel ?? "") ? ` in <#${channel}>` : "";
  const quoted = idea.split("\n").map((l) => `> ${l}`).join("\n");
  const message = `:bulb: **Feature request from <@${user}>**${where}\n${quoted}`;
  const delivered: string[] = [];
  for (const to of deps.requestRecipients) {
    try {
      // A user id as the channel posts in that person's DM with Eli (chat:write is enough).
      await req.client.chat.postMessage({ channel: to, ...slackPayload(message), unfurl_links: false });
      delivered.push(to);
    } catch (err) {
      log(`[eli] could not deliver feature request to ${to}: ${(err as Error).message}`);
    }
  }
  log(`[eli] feature request from ${user}: delivered to ${delivered.length}/${deps.requestRecipients.size}`);
  if (delivered.length === 0) {
    return reply("Sorry, I couldn't deliver that right now. It's saved, and Eli's maintainer will still see it.");
  }
  const names = delivered.map((u) => `<@${u}>`).join(" and ");
  await reply(`Thanks! I sent your request to ${names}:\n${quoted}`);
}

export function registerCommands(app: App, deps: CommandDeps): void {
  const withLimiter = { ...deps, requestLimiter: deps.requestLimiter ?? new RequestLimiter() };
  app.command(HELP_COMMAND, async ({ command, ack, respond, client }) => {
    await handleHelpCommand({ command, ack: () => ack(), respond, client }, withLimiter);
  });
  app.command(ONBOARD_COMMAND, async ({ command, ack, respond, client }) => {
    await handleOnboardCommand({ command, ack: () => ack(), respond, client }, withLimiter);
  });
  app.command(CONSOLE_COMMAND, async ({ command, ack, respond, client }) => {
    await handleConsoleCommand({ command, ack: () => ack(), respond, client }, withLimiter);
  });
  app.command(REQUESTS_COMMAND, async ({ command, ack, respond, client }) => {
    await handleRequestsCommand({ command, ack: () => ack(), respond, client }, withLimiter);
  });
}
