import type Anthropic from "@anthropic-ai/sdk";
import type { SlackFile } from "./tools/slackFiles.js";

/** The fields Eli reads from a Slack message (conversations.replies / event payloads). */
export interface SlackMessage {
  ts: string;
  user?: string;
  bot_id?: string;
  text?: string;
  subtype?: string;
  files?: Array<{ id?: string; name?: string; title?: string; mimetype?: string; size?: number; url_private_download?: string }>;
}

export interface ThreadOptions {
  /** Eli's own Slack user id: its messages become assistant turns. */
  botUserId: string;
  /** Slack user id -> display name. */
  names: ReadonlyMap<string, string>;
  maxMessages?: number;
  maxChars?: number;
}

const DEFAULT_MAX_MESSAGES = 30;
const DEFAULT_MAX_CHARS = 20_000;

/**
 * Slack's message markup to plain text: <@U1> mentions, <#C1|name> channels, <url|label> links and
 * HTML entities. Eli's own mention is removed.
 */
export function slackToPlain(text: string, botUserId: string, names: ReadonlyMap<string, string>): string {
  return text
    .replace(/<@([UW][A-Z0-9]+)(?:\|[^>]*)?>/g, (_m, id: string) => (id === botUserId ? "" : `@${names.get(id) ?? "someone"}`))
    .replace(/<#[CG][A-Z0-9]+\|([^>]*)>/g, (_m, name: string) => `#${name}`)
    .replace(/<!(here|channel|everyone)>/g, "@$1")
    .replace(/<((?:https?|mailto):[^|>]+)\|([^>]+)>/g, (_m, url: string, label: string) => (label === url ? url : `${label} (${url})`))
    .replace(/<((?:https?|mailto):[^>]+)>/g, "$1")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

function messageText(m: SlackMessage, opts: ThreadOptions): string {
  let text = slackToPlain(m.text ?? "", opts.botUserId, opts.names);
  const files = (m.files ?? [])
    .map((f) => {
      const name = f.name ?? f.title;
      if (!name) return undefined;
      return f.id ? `${name} [file ${f.id}]` : name;
    })
    .filter(Boolean);
  if (files.length) text += `${text ? "\n" : ""}(attached: ${files.join(", ")})`;
  return text;
}

/**
 * Turn a Slack thread (oldest first) into Claude messages: Eli's messages are assistant turns, everyone else's
 * are user turns prefixed with "[Name]: ". Keeps the newest messages within the limits, merges consecutive turns
 * from the same side, and always starts and ends on a user turn.
 */
export function threadToMessages(thread: readonly SlackMessage[], opts: ThreadOptions): Anthropic.MessageParam[] {
  const maxMessages = opts.maxMessages ?? DEFAULT_MAX_MESSAGES;
  const maxChars = opts.maxChars ?? DEFAULT_MAX_CHARS;

  const turns: Array<{ role: "user" | "assistant"; text: string }> = [];
  for (const m of thread) {
    if (m.subtype && m.subtype !== "thread_broadcast" && m.subtype !== "file_share") continue;
    const fromEli = m.user === opts.botUserId;
    const body = messageText(m, opts);
    if (!body) continue;
    const who = m.user ? (opts.names.get(m.user) ?? "Someone") : "A bot";
    turns.push(fromEli ? { role: "assistant", text: body } : { role: "user", text: `[${who}]: ${body}` });
  }

  // Newest first, within budget. The newest message (the request) is always kept, truncated if it alone is too big.
  const kept: typeof turns = [];
  let chars = 0;
  for (let i = turns.length - 1; i >= 0 && kept.length < maxMessages; i--) {
    const t = turns[i]!;
    if (kept.length === 0 && t.text.length > maxChars) {
      kept.push({ ...t, text: t.text.slice(0, maxChars) + "\n[message truncated]" });
      chars = maxChars;
      continue;
    }
    if (chars + t.text.length > maxChars) break;
    kept.push(t);
    chars += t.text.length;
  }
  kept.reverse();

  while (kept.length && kept[0]!.role !== "user") kept.shift();
  while (kept.length && kept[kept.length - 1]!.role !== "user") kept.pop();

  const out: Anthropic.MessageParam[] = [];
  for (const t of kept) {
    const prev = out[out.length - 1];
    if (prev && prev.role === t.role) prev.content = `${prev.content as string}\n\n${t.text}`;
    else out.push({ role: t.role, content: t.text });
  }
  return out;
}

/** Files attached anywhere in the thread that Eli could download (the allow-list for slack_read_file). */
export function threadFiles(thread: readonly SlackMessage[]): SlackFile[] {
  const out = new Map<string, SlackFile>();
  for (const m of thread) {
    for (const f of m.files ?? []) {
      if (!f.id || !f.url_private_download) continue;
      out.set(f.id, {
        id: f.id,
        name: f.name ?? f.title ?? f.id,
        mimetype: f.mimetype,
        size: f.size,
        url_private_download: f.url_private_download,
      });
    }
  }
  return [...out.values()];
}
