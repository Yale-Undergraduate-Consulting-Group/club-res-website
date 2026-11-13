/**
 * A coarse topic for each request, worked out from the question and the tools Eli used. Only the topic label is
 * logged; the question text itself is never stored.
 */
export const TOPICS = [
  "Past projects & files",
  "Web research",
  "Reminders",
  "Drafting",
  "Summaries",
  "Analysis & advice",
  "Other questions",
] as const;
export type Topic = (typeof TOPICS)[number];

const DRAFTING = /\b(draft|write|rewrite|email|e-mail|outline|memo|cover letter|message to|reply to|proposal|agenda|slides?|deck outline|polish|proofread|edit this)\b/i;
const SUMMARY = /\b(summari[sz]e|summary|recap|tl;?dr|key (points|takeaways)|catch me up|what did (we|they) (say|decide))\b/i;
const ANALYSIS = /\b(analy[sz]e|compare|pros and cons|recommend|should (we|i)|strategy|framework|estimate|market size|sizing|pricing|evaluate|brainstorm|ideas? for)\b/i;
const REMINDER = /\b(remind|reminder|ping me|ping @|nudge)\b/i;
const FILE_LINK = /sharepoint\.com|onedrive|drive\.google\.com|docs\.google\.com/i;

export function classifyTopic(text: string, tools: readonly string[]): Topic {
  if (tools.some((t) => t.startsWith("sharepoint_") || t.startsWith("gdrive_") || t === "slack_read_file") || FILE_LINK.test(text)) {
    return "Past projects & files";
  }
  if (tools.some((t) => t.includes("reminder")) || REMINDER.test(text)) return "Reminders";
  if (tools.includes("web_search")) return "Web research";
  if (SUMMARY.test(text)) return "Summaries";
  if (DRAFTING.test(text)) return "Drafting";
  if (ANALYSIS.test(text)) return "Analysis & advice";
  return "Other questions";
}
