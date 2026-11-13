import { describeApiError } from "./agent.js";
import { loadEliConfig } from "./config.js";
import { estimateCostUsd } from "./limits.js";
import { estimateWebSearchUsd } from "./webSearch.js";
import { createBrain } from "./setup.js";

/** `npm run eli:ask -- "question"`: one Eli answer in the terminal, without Slack. Costs one normal request. */
async function main(): Promise<void> {
  const question = process.argv.slice(2).join(" ").trim();
  if (!question) {
    console.error('Usage: npm run eli:ask -- "your question"');
    process.exit(2);
  }
  const config = loadEliConfig(process.env, { needSlack: false });
  const brain = createBrain(config);
  console.error((await brain.describe()).join("\n") + "\n");
  try {
    const ws = config.webSearch;
    const result = await brain.answer(
      [{ role: "user", content: `[Terminal user]: ${question}` }],
      [],
      ws.enabled ? { webSearch: { maxUses: ws.maxUsesPerQuestion } } : {},
    );
    console.log(result.text);
    const u = result.usage;
    const searches = result.webSearches ?? 0;
    const tokenCost = estimateCostUsd(config.model, u);
    const cost = tokenCost === undefined ? undefined : tokenCost + estimateWebSearchUsd(searches);
    console.error(
      `\n— answered by ${result.servedModel ?? config.model}; ${u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens} input / ${u.output_tokens} output tokens` +
        (searches ? `, ${searches} web search${searches === 1 ? "" : "es"}` : "") +
        (cost !== undefined ? `, about $${cost.toFixed(4)}` : "") +
        (result.toolsUsed.length ? `; tools: ${result.toolsUsed.join(", ")}` : ""),
    );
  } catch (err) {
    console.error(describeApiError(err));
    console.error(err instanceof Error ? `${err.name}: ${err.message}` : String(err));
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
