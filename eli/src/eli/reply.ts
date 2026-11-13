import type { types } from "@slack/bolt";

type KnownBlock = types.KnownBlock;

/** All `markdown` blocks in one Slack message share a 12,000-character limit; leave headroom. */
export const CHUNK_LIMIT = 11_000;
/** Notification / fallback text. Slack truncates very long `text` anyway. */
const FALLBACK_LIMIT = 3_000;

/** Split a reply on paragraph, then line, then hard boundaries so each piece fits one Slack message. */
export function splitForSlack(text: string, limit = CHUNK_LIMIT): string[] {
  const chunks: string[] = [];
  let rest = text.trim();
  while (rest.length > limit) {
    let cut = rest.lastIndexOf("\n\n", limit);
    if (cut < limit / 2) cut = rest.lastIndexOf("\n", limit);
    if (cut < limit / 2) cut = rest.lastIndexOf(" ", limit);
    if (cut < limit / 2) cut = limit;
    chunks.push(rest.slice(0, cut).trimEnd());
    rest = rest.slice(cut).trimStart();
  }
  if (rest) chunks.push(rest);
  return chunks.length ? chunks : [""];
}

/** One chat.postMessage payload: a markdown block (Slack renders standard Markdown) plus plain fallback text. */
export function slackPayload(chunk: string): { text: string; blocks: KnownBlock[] } {
  const text = chunk.length > FALLBACK_LIMIT ? chunk.slice(0, FALLBACK_LIMIT - 1) + "…" : chunk;
  return { text, blocks: [{ type: "markdown", text: chunk }] };
}
