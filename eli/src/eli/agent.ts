import Anthropic from "@anthropic-ai/sdk";
import type { Effort } from "./config.js";
import { type UsageTotals, addUsage, emptyUsage } from "./limits.js";
import type { AnyRunnableTool } from "./tools/sharepoint.js";

export interface AgentOptions {
  client: Anthropic;
  model: string;
  effort: Effort;
  maxTokens: number;
  maxIterations: number;
  system: string;
  tools: AnyRunnableTool[];
  /**
   * Anthropic-run tools for this request only, such as `webSearchTool(cfg, maxUses)`. They go after the client
   * tools. Anthropic runs them; the SDK's tool runner passes them through and resumes `pause_turn` turns itself.
   */
  serverTools?: Anthropic.Beta.BetaToolUnion[];
}

export interface AgentResult {
  text: string;
  usage: UsageTotals;
  stopReason: string | null;
  /** Client tools and server tools (e.g. "web_search"), one entry per call, in order. */
  toolsUsed: string[];
  /**
   * Web searches Anthropic ran (and bills) for this request: the larger of the summed
   * `usage.server_tool_use.web_search_requests` and the number of successful `web_search_tool_result` blocks.
   * Always set by runAgent; optional only so older test fakes still type-check.
   */
  webSearches?: number;
  /** The model the API says actually answered (from the response), for the log. */
  servedModel?: string;
}

const REFUSED = "Sorry, I can't help with that one.";
const OUT_OF_STEPS =
  "I ran out of steps before finishing. Try a narrower question, or point me at a specific folder or file.";
const CUT_OFF = "\n\n_(I ran out of space here. Ask me to continue.)_";
const MAX_SOURCES = 5;

/**
 * One Eli turn: Claude plus the read-only tools, looped by the SDK's tool runner until Claude answers in text.
 * Sums token usage and web searches across every step so the cost log is complete.
 */
export async function runAgent(opts: AgentOptions, messages: Anthropic.Beta.BetaMessageParam[]): Promise<AgentResult> {
  const runner = opts.client.beta.messages.toolRunner({
    model: opts.model,
    max_tokens: opts.maxTokens,
    max_iterations: opts.maxIterations,
    output_config: { effort: opts.effort },
    // Identical on every request of a running process, so it is cached.
    system: [{ type: "text", text: opts.system, cache_control: { type: "ephemeral" } }],
    tools: [...opts.tools, ...(opts.serverTools ?? [])],
    messages,
  });

  const usage = emptyUsage();
  const toolsUsed: string[] = [];
  let reportedSearches = 0;
  let searchResults = 0;
  let last: Anthropic.Beta.BetaMessage | undefined;
  // The answer's content. A `pause_turn` reply is continued by the next one, so their content is joined.
  let finalContent: Anthropic.Beta.BetaContentBlock[] = [];
  for await (const message of runner) {
    addUsage(usage, message.usage);
    reportedSearches += message.usage?.server_tool_use?.web_search_requests ?? 0;
    for (const block of message.content) {
      if (block.type === "tool_use" || block.type === "server_tool_use") toolsUsed.push(block.name);
      // A failed search returns an error object instead of a list, and is not billed.
      if (block.type === "web_search_tool_result" && Array.isArray(block.content)) searchResults++;
    }
    finalContent = last?.stop_reason === "pause_turn" ? [...finalContent, ...message.content] : [...message.content];
    last = message;
  }
  if (!last) throw new Error("Claude returned no message.");
  const webSearches = Math.max(reportedSearches, searchResults);

  let text = answerText(finalContent);
  const sources = hasLink(text) ? "" : sourcesList(finalContent);

  switch (last.stop_reason) {
    case "refusal":
      text = REFUSED;
      break;
    case "max_tokens":
      text = (text || "My answer was too long to send.") + sources + CUT_OFF;
      break;
    case "tool_use": // max_iterations reached with a tool call still pending
    case "pause_turn": // max_iterations reached while a server-tool turn was paused
      text = (text ? `${text}${sources}\n\n` : "") + OUT_OF_STEPS;
      break;
    default:
      text = text ? text + sources : "I don't have anything to add.";
  }
  return { text, usage, stopReason: last.stop_reason, toolsUsed, webSearches, servedModel: last.model };
}

/**
 * The reply text. Web search splits one sentence into several text blocks around each citation, so adjacent text
 * blocks are joined as they are; text separated by other blocks (searches, results) becomes its own paragraph.
 */
export function answerText(content: readonly Anthropic.Beta.BetaContentBlock[]): string {
  const parts: string[] = [];
  let current = "";
  for (const block of content) {
    if (block.type === "text") {
      current += block.text;
    } else if (current) {
      parts.push(current);
      current = "";
    }
  }
  if (current) parts.push(current);
  return parts
    .map((p) => p.trim())
    .filter(Boolean)
    .join("\n\n");
}

function hasLink(text: string): boolean {
  return /\]\(\s*https?:\/\/|<https?:\/\/|https?:\/\/\S/.test(text);
}

/** "\n\nSources:\n- [Title](url)" for the unique web pages cited in `content` (at most five), or "" when there are none. */
export function sourcesList(content: readonly Anthropic.Beta.BetaContentBlock[], max = MAX_SOURCES): string {
  const seen = new Map<string, string>();
  for (const block of content) {
    if (block.type !== "text" || !block.citations) continue;
    for (const c of block.citations) {
      if (c.type !== "web_search_result_location" || !/^https?:\/\//i.test(c.url) || seen.has(c.url)) continue;
      seen.set(c.url, c.title?.trim() || hostOf(c.url));
      if (seen.size >= max) break;
    }
    if (seen.size >= max) break;
  }
  if (!seen.size) return "";
  const lines = [...seen].map(([url, title]) => `- [${escapeLinkText(title)}](${url.replace(/\)/g, "%29").replace(/ /g, "%20")})`);
  return `\n\nSources:\n${lines.join("\n")}`;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function escapeLinkText(s: string): string {
  return s.replace(/\s+/g, " ").replace(/[[\]]/g, (m) => `\\${m}`).slice(0, 120);
}

/** A short, user-safe explanation of an API failure. Details go to the log, never to Slack. */
export function describeApiError(err: unknown): string {
  if (err instanceof Anthropic.RateLimitError) return "Claude is rate-limiting Eli right now. Try again in a minute.";
  if (err instanceof Anthropic.AuthenticationError) return "Eli's Anthropic API key isn't working. Ask whoever runs Eli to check it.";
  if (err instanceof Anthropic.PermissionDeniedError) return "Eli's Anthropic account can't use this model or feature.";
  if (err instanceof Anthropic.NotFoundError) return "Eli's model setting (ELI_MODEL) isn't a model this account can use.";
  if (err instanceof Anthropic.APIError && /credit balance|spend limit|usage limit/i.test(err.message)) {
    return "Eli's Anthropic account has run out of credit or hit its spending limit.";
  }
  if (err instanceof Anthropic.BadRequestError && /web[ _]search/i.test(err.message)) {
    return "Claude rejected Eli's web search settings (web search may be turned off for Eli's Anthropic organization). Ask whoever runs Eli to check.";
  }
  if (err instanceof Anthropic.BadRequestError) return "Claude rejected that request. Try rephrasing or shortening the thread.";
  if (err instanceof Anthropic.InternalServerError || err instanceof Anthropic.APIConnectionError) {
    return "Claude is having trouble right now. Try again shortly.";
  }
  return "Sorry, something went wrong on my end.";
}
