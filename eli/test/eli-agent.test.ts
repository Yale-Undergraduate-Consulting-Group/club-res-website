import Anthropic from "@anthropic-ai/sdk";
import { describe, expect, it } from "vitest";
import { answerText, describeApiError, runAgent, sourcesList, type AgentOptions } from "../src/eli/agent.js";

type Msg = Partial<Anthropic.Beta.BetaMessage> & { content: Anthropic.Beta.BetaContentBlock[] };

/** A stand-in for client.beta.messages.toolRunner: yields the given messages and records the params. */
function fakeClient(messages: Msg[]) {
  const seen: Record<string, unknown>[] = [];
  const client = {
    beta: {
      messages: {
        toolRunner(params: Record<string, unknown>) {
          seen.push(params);
          return (async function* () {
            for (const m of messages) yield m;
          })();
        },
      },
    },
  } as unknown as Anthropic;
  return { client, seen };
}

const text = (t: string) => ({ type: "text", text: t, citations: null }) as Anthropic.Beta.BetaTextBlock;
const toolUse = (name: string) => ({ type: "tool_use", id: "t1", name, input: {} }) as Anthropic.Beta.BetaToolUseBlock;
const usage = (i: number, o: number) => ({ input_tokens: i, output_tokens: o, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }) as Anthropic.Beta.BetaUsage;

function opts(client: Anthropic): AgentOptions {
  return { client, model: "claude-haiku-5-5", effort: "medium", maxTokens: 8000, maxIterations: 10, system: "SYS", tools: [] };
}

describe("runAgent", () => {
  it("sends the cached system prompt and settings, sums usage across steps, and lists tools used", async () => {
    const { client, seen } = fakeClient([
      { content: [toolUse("sharepoint_search")], stop_reason: "tool_use", usage: usage(100, 10) },
      { content: [text("Found it.")], stop_reason: "end_turn", usage: usage(200, 20), model: "claude-haiku-5-5" },
    ]);
    const r = await runAgent(opts(client), [{ role: "user", content: "hi" }]);
    expect(r).toMatchObject({ text: "Found it.", stopReason: "end_turn", toolsUsed: ["sharepoint_search"], servedModel: "claude-haiku-5-5" });
    expect(r.usage).toMatchObject({ input_tokens: 300, output_tokens: 30 });
    expect(seen[0]).toMatchObject({
      model: "claude-haiku-5-5",
      max_tokens: 8000,
      max_iterations: 10,
      output_config: { effort: "medium" },
      system: [{ type: "text", text: "SYS", cache_control: { type: "ephemeral" } }],
    });
  });

  it.each([
    ["refusal", [text("partial")], "Sorry, I can't help with that one."],
    ["max_tokens", [text("long answer")], "long answer\n\n_(I ran out of space here. Ask me to continue.)_"],
    ["tool_use", [toolUse("gdrive_search")], "I ran out of steps before finishing."],
    ["end_turn", [], "I don't have anything to add."],
  ] as const)("handles stop_reason %s", async (stop, content, expected) => {
    const { client } = fakeClient([{ content: [...content], stop_reason: stop, usage: usage(1, 1) }]);
    const r = await runAgent(opts(client), [{ role: "user", content: "x" }]);
    expect(r.text.startsWith(expected)).toBe(true);
  });

  it("fails clearly when Claude returns nothing", async () => {
    const { client } = fakeClient([]);
    await expect(runAgent(opts(client), [{ role: "user", content: "x" }])).rejects.toThrow("Claude returned no message.");
  });
});

// ------------------------------------------------------------------------------------------- web search

const search = (id = "srvtoolu_1") =>
  ({ type: "server_tool_use", id, name: "web_search", input: { query: "q" } }) as Anthropic.Beta.BetaServerToolUseBlock;
const results = (id = "srvtoolu_1", urls = ["https://a.example/x"]) =>
  ({
    type: "web_search_tool_result",
    tool_use_id: id,
    content: urls.map((url) => ({ type: "web_search_result", url, title: url, encrypted_content: "enc", page_age: null })),
  }) as Anthropic.Beta.BetaWebSearchToolResultBlock;
const searchError = (id = "srvtoolu_2") =>
  ({
    type: "web_search_tool_result",
    tool_use_id: id,
    content: { type: "web_search_tool_result_error", error_code: "max_uses_exceeded" },
  }) as Anthropic.Beta.BetaWebSearchToolResultBlock;
const cited = (t: string, ...cites: Array<[url: string, title: string | null]>) =>
  ({
    type: "text",
    text: t,
    citations: cites.map(([url, title]) => ({ type: "web_search_result_location", url, title, cited_text: "…", encrypted_index: "e" })),
  }) as Anthropic.Beta.BetaTextBlock;
const searchUsage = (n: number) => ({ ...usage(10, 5), server_tool_use: { web_search_requests: n, web_fetch_requests: 0 } }) as Anthropic.Beta.BetaUsage;

describe("runAgent with web search", () => {
  const ws = { type: "web_search_20250305", name: "web_search", max_uses: 2 } as Anthropic.Beta.BetaToolUnion;

  it("sends server tools after the client tools, and keeps old callers working without them", async () => {
    const a = fakeClient([{ content: [text("ok")], stop_reason: "end_turn", usage: usage(1, 1) }]);
    const tool = { name: "x", type: "custom" } as unknown as AgentOptions["tools"][number];
    await runAgent({ ...opts(a.client), tools: [tool], serverTools: [ws] }, [{ role: "user", content: "x" }]);
    expect(a.seen[0]!.tools).toEqual([tool, ws]);

    const b = fakeClient([{ content: [text("ok")], stop_reason: "end_turn", usage: usage(1, 1) }]);
    const r = await runAgent(opts(b.client), [{ role: "user", content: "x" }]);
    expect(b.seen[0]!.tools).toEqual([]);
    expect(r.webSearches).toBe(0);
  });

  it("counts searches from usage across steps and lists server tools in toolsUsed", async () => {
    const { client } = fakeClient([
      // Claude called a client tool and a search together: the search runs on the next request.
      { content: [toolUse("sharepoint_search"), search("s1")], stop_reason: "tool_use", usage: usage(10, 5) },
      { content: [results("s1"), search("s2"), results("s2"), text("Answer [A](https://a.example/x).")], stop_reason: "end_turn", usage: searchUsage(2) },
    ]);
    const r = await runAgent(opts(client), [{ role: "user", content: "x" }]);
    expect(r.toolsUsed).toEqual(["sharepoint_search", "web_search", "web_search"]);
    expect(r.webSearches).toBe(2);
    expect(r.text).toBe("Answer [A](https://a.example/x).");
  });

  it("does not count failed searches, and falls back to result blocks when usage under-reports", async () => {
    const failed = fakeClient([{ content: [search("s2"), searchError("s2"), text("No luck.")], stop_reason: "end_turn", usage: searchUsage(0) }]);
    expect((await runAgent(opts(failed.client), [{ role: "user", content: "x" }])).webSearches).toBe(0);

    const unreported = fakeClient([{ content: [search(), results(), text("Hi.")], stop_reason: "end_turn", usage: usage(1, 1) }]);
    expect((await runAgent(opts(unreported.client), [{ role: "user", content: "x" }])).webSearches).toBe(1);
  });

  it("joins citation-split text blocks into one sentence and appends unique sources when the text has no links", async () => {
    const { client } = fakeClient([
      {
        content: [
          search(),
          results(),
          text("Based on the results, "),
          cited("Acme raised $5M", ["https://news.example/acme", "Acme raises $5M [Seed]"]),
          text(" in 2026 and "),
          cited("hired 20 people", ["https://news.example/acme", "dup"], ["https://www.blog.example/p", null]),
          text("."),
        ],
        stop_reason: "end_turn",
        usage: searchUsage(1),
      },
    ]);
    const r = await runAgent(opts(client), [{ role: "user", content: "x" }]);
    expect(r.text).toBe(
      "Based on the results, Acme raised $5M in 2026 and hired 20 people.\n\nSources:\n" +
        "- [Acme raises $5M \\[Seed\\]](https://news.example/acme)\n" +
        "- [blog.example](https://www.blog.example/p)",
    );
  });

  it("joins the content of a paused turn with its continuation", async () => {
    const { client } = fakeClient([
      { content: [text("Part one, "), cited("cited", ["https://a.example/1", "One"]), search(), results()], stop_reason: "pause_turn", usage: searchUsage(1) },
      { content: [text("part two.")], stop_reason: "end_turn", usage: searchUsage(0) },
    ]);
    const r = await runAgent(opts(client), [{ role: "user", content: "x" }]);
    expect(r.text).toBe("Part one, cited\n\npart two.\n\nSources:\n- [One](https://a.example/1)");
    expect(r.webSearches).toBe(1);
  });

  it("does not carry content from before a client tool step into the answer", async () => {
    const { client } = fakeClient([
      { content: [text("Let me look."), toolUse("gdrive_search")], stop_reason: "tool_use", usage: usage(1, 1) },
      { content: [text("Done.")], stop_reason: "end_turn", usage: usage(1, 1) },
    ]);
    expect((await runAgent(opts(client), [{ role: "user", content: "x" }])).text).toBe("Done.");
  });

  it("explains running out of steps on a paused turn", async () => {
    const { client } = fakeClient([{ content: [search(), results(), text("Partial")], stop_reason: "pause_turn", usage: searchUsage(1) }]);
    const r = await runAgent(opts(client), [{ role: "user", content: "x" }]);
    expect(r.text).toBe("Partial\n\nI ran out of steps before finishing. Try a narrower question, or point me at a specific folder or file.");
  });

  it("keeps sources before the cut-off note, and drops them on a refusal", async () => {
    const long = fakeClient([{ content: [cited("Long", ["https://a.example/1", "One"])], stop_reason: "max_tokens", usage: usage(1, 1) }]);
    expect((await runAgent(opts(long.client), [{ role: "user", content: "x" }])).text).toBe(
      "Long\n\nSources:\n- [One](https://a.example/1)\n\n_(I ran out of space here. Ask me to continue.)_",
    );
    const refused = fakeClient([{ content: [cited("x", ["https://a.example/1", "One"])], stop_reason: "refusal", usage: usage(1, 1) }]);
    expect((await runAgent(opts(refused.client), [{ role: "user", content: "x" }])).text).toBe("Sorry, I can't help with that one.");
  });
});

describe("answerText and sourcesList", () => {
  it("separates text that other blocks split apart, and skips empty text", () => {
    expect(answerText([text("  "), search(), text("A"), text("B"), results(), text("C ")])).toBe("AB\n\nC");
  });

  it("caps the list at five unique http(s) sources and ignores non-web citations", () => {
    const many = Array.from({ length: 7 }, (_, i) => [`https://s${i}.example/`, `S${i}`] as [string, string]);
    const docCite = {
      type: "text",
      text: "d",
      citations: [{ type: "char_location", cited_text: "c", document_index: 0, document_title: "Doc", start_char_index: 0, end_char_index: 1, file_id: null }],
    } as unknown as Anthropic.Beta.BetaTextBlock;
    const out = sourcesList([docCite, cited("x", ["javascript:alert(1)", "bad"], ...many)]);
    expect(out.match(/^- /gm)).toHaveLength(5);
    expect(out).not.toContain("javascript");
    expect(out).toContain("[S4](https://s4.example/)");
    expect(out).not.toContain("S5");
    expect(sourcesList([text("no citations")])).toBe("");
  });
});

describe("describeApiError", () => {
  const apiError = (status: number, message: string) => Anthropic.APIError.generate(status, { error: { message } }, message, new Headers());

  it("gives short, safe explanations and never echoes internals", () => {
    expect(describeApiError(apiError(429, "rate"))).toContain("rate-limiting");
    expect(describeApiError(apiError(401, "bad key"))).toContain("API key");
    expect(describeApiError(apiError(404, "model"))).toContain("ELI_MODEL");
    expect(describeApiError(apiError(400, "Your credit balance is too low"))).toContain("run out of credit");
    expect(describeApiError(apiError(400, "messages: bad"))).toContain("rejected");
    expect(describeApiError(apiError(400, "web search is not enabled for this organization"))).toContain("web search");
    expect(describeApiError(new Error("secret internal detail"))).toBe("Sorry, something went wrong on my end.");
  });
});
