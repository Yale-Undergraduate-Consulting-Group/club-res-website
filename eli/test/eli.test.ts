import path from "node:path";
import { ToolError } from "@anthropic-ai/sdk/lib/tools/ToolError";
import { describe, expect, it, vi } from "vitest";
import { loadEliConfig } from "../src/eli/config.js";
import { DriveReader, NOT_SHARED, parseDriveLink, type DriveApi, type DriveFile } from "../src/eli/gdrive/client.js";
import { RateLimiter, addUsage, emptyUsage, estimateCostUsd, limitMessage } from "../src/eli/limits.js";
import { buildSystemPrompt } from "../src/eli/prompt.js";
import { CHUNK_LIMIT, slackPayload, splitForSlack } from "../src/eli/reply.js";
import { EliSlack } from "../src/eli/slack.js";
import { slackToPlain, threadToMessages, type SlackMessage } from "../src/eli/thread.js";
import { jsonTool } from "../src/eli/tools/sharepoint.js";
import { InvalidPathError, NotAllowedError, UpstreamError } from "../src/sharepoint/errors.js";
import { MockSharePointClient } from "../src/sharepoint/mock.js";
import { Scope } from "../src/sharepoint/scope.js";
import type { SearchHit, SharePointClient } from "../src/sharepoint/types.js";
import { GENERIC_ERROR_MESSAGE } from "../src/tools/result.js";
import { runSearch } from "../src/tools/search.js";

const BOT = "UELI0001";
const names = new Map([
  ["UJORDAN1", "Jordan Lee"],
  ["USAM0001", "Sam"],
]);

// ---------------------------------------------------------------------------------------------- thread

describe("slackToPlain", () => {
  it("removes Eli's mention, names other people, and unwraps links and channels", () => {
    const text =
      "<@UELI0001> ask <@USAM0001> about <https://contoso.sharepoint.com/x|the deck> in <#C123|proj-acme> &amp; <https://a.b/c>";
    expect(slackToPlain(text, BOT, names)).toBe("ask @Sam about the deck (https://contoso.sharepoint.com/x) in #proj-acme & https://a.b/c");
  });
});

describe("threadToMessages", () => {
  const msg = (user: string, text: string, ts: string): SlackMessage => ({ user, text, ts });

  it("maps Eli to assistant, people to named user turns, and merges consecutive turns", () => {
    const out = threadToMessages(
      [msg("UJORDAN1", "We need comps for Acme", "1"), msg("USAM0001", "agreed", "2"), msg(BOT, "Here are three.", "3"), msg("UJORDAN1", "<@UELI0001> summarise this", "4")],
      { botUserId: BOT, names },
    );
    expect(out).toEqual([
      { role: "user", content: "[Jordan Lee]: We need comps for Acme\n\n[Sam]: agreed" },
      { role: "assistant", content: "Here are three." },
      { role: "user", content: "[Jordan Lee]: summarise this" },
    ]);
  });

  it("drops a leading Eli message and skips edits and joins", () => {
    const out = threadToMessages(
      [msg(BOT, "hi", "1"), { ts: "2", user: "USAM0001", text: "x joined", subtype: "channel_join" }, msg("USAM0001", "question", "3")],
      { botUserId: BOT, names },
    );
    expect(out).toEqual([{ role: "user", content: "[Sam]: question" }]);
  });

  it("keeps the newest messages within the character budget and always keeps the request", () => {
    const thread = Array.from({ length: 10 }, (_, i) => msg("USAM0001", `message ${i} ${"x".repeat(90)}`, String(i)));
    const out = threadToMessages(thread, { botUserId: BOT, names, maxChars: 350 });
    const content = out.map((m) => m.content as string).join("\n");
    expect(content).toContain("message 9");
    expect(content).not.toContain("message 0");

    const huge = threadToMessages([msg("USAM0001", "y".repeat(1000), "1")], { botUserId: BOT, names, maxChars: 100 });
    expect((huge[0]!.content as string).endsWith("[message truncated]")).toBe(true);
  });

  it("notes attachments it cannot open", () => {
    const out = threadToMessages([{ ts: "1", user: "USAM0001", text: "see file", files: [{ name: "deck.pptx" }] }], {
      botUserId: BOT,
      names,
    });
    expect(out[0]!.content).toContain("(attached: deck.pptx");
  });
});

// ---------------------------------------------------------------------------------------------- reply

describe("splitForSlack", () => {
  it("keeps short replies whole and splits long ones on paragraph boundaries under the limit", () => {
    expect(splitForSlack("hello")).toEqual(["hello"]);
    const para = "a".repeat(6000);
    const chunks = splitForSlack(`${para}\n\n${para}\n\n${para}`);
    expect(chunks.length).toBe(3);
    for (const c of chunks) expect(c.length).toBeLessThanOrEqual(CHUNK_LIMIT);
  });

  it("hard-splits text with no break points", () => {
    const chunks = splitForSlack("b".repeat(25_000));
    expect(chunks.map((c) => c.length)).toEqual([CHUNK_LIMIT, CHUNK_LIMIT, 3000]);
  });

  it("builds a markdown block with a short fallback text", () => {
    const p = slackPayload("x".repeat(5000));
    expect(p.blocks).toEqual([{ type: "markdown", text: "x".repeat(5000) }]);
    expect(p.text.length).toBe(3000);
  });
});

// ---------------------------------------------------------------------------------------------- limits

describe("RateLimiter", () => {
  it("enforces the per-person hourly limit and recovers after the window", () => {
    let now = 0;
    const rl = new RateLimiter(2, 100, () => now);
    expect(rl.take("U1").ok).toBe(true);
    expect(rl.take("U1").ok).toBe(true);
    const third = rl.take("U1");
    expect(third).toMatchObject({ ok: false, scope: "user" });
    expect(rl.take("U2").ok).toBe(true); // other people unaffected
    now = 60 * 60 * 1000 + 1;
    expect(rl.take("U1").ok).toBe(true);
  });

  it("enforces the workspace daily limit", () => {
    const rl = new RateLimiter(100, 2, () => 0);
    rl.take("A");
    rl.take("B");
    const d = rl.take("C");
    expect(d).toMatchObject({ ok: false, scope: "workspace" });
    if (!d.ok) expect(limitMessage(d, 100, 2)).toContain("daily limit of 2 requests");
  });
});

describe("cost estimate", () => {
  it("prices Haiku 5.5 and sums usage across steps", () => {
    const u = emptyUsage();
    addUsage(u, { input_tokens: 1_000_000, output_tokens: 0 });
    addUsage(u, { input_tokens: 0, output_tokens: 1_000_000, cache_read_input_tokens: null });
    expect(estimateCostUsd("claude-haiku-5-5", u)).toBeCloseTo(0.6, 6);
    expect(estimateCostUsd("some-unknown-model", u)).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------------------------- config + prompt

describe("loadEliConfig", () => {
  it("lists every missing secret without echoing values", () => {
    expect(() => loadEliConfig({}, { anthropicLogin: () => false })).toThrow(/ANTHROPIC_API_KEY is required[\s\S]*SLACK_BOT_TOKEN[\s\S]*SLACK_APP_TOKEN/);
    expect(() => loadEliConfig({ SLACK_BOT_TOKEN: "xapp-wrong" })).toThrow(/starts with xoxb-/);
  });

  it("accepts a saved `ant auth login` sign-in instead of an API key", () => {
    expect(loadEliConfig({}, { needSlack: false, anthropicLogin: () => true }).model).toBe("claude-haiku-5-5");
    expect(() => loadEliConfig({}, { needSlack: false, anthropicLogin: () => false })).toThrow(/ant auth login/);
  });

  it("works without Slack for the CLI and defaults to Haiku 5.5 with mock SharePoint", () => {
    const c = loadEliConfig({ ANTHROPIC_API_KEY: "k" }, { needSlack: false });
    expect(c.model).toBe("claude-haiku-5-5");
    expect(c.sharepoint?.mode).toBe("mock");
    expect(c.gdrive).toBeUndefined();
  });

  it("accepts delegated graph mode without a client secret or site", () => {
    const c = loadEliConfig(
      { ANTHROPIC_API_KEY: "k", SHAREPOINT_MODE: "graph", GRAPH_AUTH: "delegated", AZURE_CLIENT_ID: "abc", SHAREPOINT_ALLOWED_FOLDERS: "YUCG Eli" },
      { needSlack: false },
    );
    expect(c.sharepoint?.graph).toMatchObject({ auth: "delegated", tenantId: "organizations", clientId: "abc" });
    expect(c.sharepoint?.allowedFolders).toEqual(["YUCG Eli"]);
  });

  it("still fails closed: graph mode needs allowed folders", () => {
    expect(() =>
      loadEliConfig({ ANTHROPIC_API_KEY: "k", SHAREPOINT_MODE: "graph", GRAPH_AUTH: "delegated", AZURE_CLIENT_ID: "abc" }, { needSlack: false }),
    ).toThrow(/SHAREPOINT_ALLOWED_FOLDERS is required/);
  });
});

describe("buildSystemPrompt", () => {
  it("mentions only the connected sources", () => {
    expect(buildSystemPrompt({ sharepoint: true, gdrive: false })).toContain("sharepoint_open_link");
    expect(buildSystemPrompt({ sharepoint: true, gdrive: false })).not.toContain("gdrive_open_link");
    expect(buildSystemPrompt({ sharepoint: false, gdrive: false })).toContain("No SharePoint or Google Drive is connected");
    expect(buildSystemPrompt({ sharepoint: false, gdrive: false })).toContain("slack_read_file");
  });
});

// ---------------------------------------------------------------------------------------------- tools

describe("jsonTool", () => {
  it("returns JSON and turns errors into model-safe ToolErrors", async () => {
    expect(await jsonTool("t", async () => ({ a: 1 }))(undefined)).toBe('{"a":1}');

    const notFound = await jsonTool("t", async () => {
      throw new NotAllowedError();
    })(undefined).catch((e) => e);
    expect(notFound).toBeInstanceOf(ToolError);
    expect(notFound.content).toBe("Item not found or outside the allowed folders.");

    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const upstream = await jsonTool("t", async () => {
      throw new UpstreamError("secret-ish detail");
    }, "Drive is down")(undefined).catch((e) => e);
    expect(upstream.content).toBe("Drive is down");
    const bug = await jsonTool("t", async () => {
      throw new TypeError("boom");
    })(undefined).catch((e) => e);
    expect(bug.content).toBe(GENERIC_ERROR_MESSAGE);
    spy.mockRestore();
  });
});

describe("runSearch folderPath", () => {
  const hit = (p: string): SearchHit => ({ id: p, name: p.split("/").at(-1)!, kind: "file", path: p, webUrl: `https://x/${p}` });
  const client = {
    search: vi.fn(async () => [hit("Eli/2023/Acme/deck.pptx"), hit("Eli/2024/Beta/deck.pptx"), hit("Eli/2023 Extra/x.docx")]),
    listFolder: vi.fn(),
    getFile: vi.fn(),
  } satisfies SharePointClient;

  it("over-fetches, then keeps only hits inside the folder (on a folder boundary)", async () => {
    const res = await runSearch(client, { query: "deck", top: 5, folderPath: "eli/2023" });
    expect(client.search).toHaveBeenCalledWith("deck", 25);
    expect(res.results.map((r) => r.path)).toEqual(["Eli/2023/Acme/deck.pptx"]);
  });

  it("rejects traversal in folderPath", async () => {
    await expect(runSearch(client, { query: "deck", top: 5, folderPath: "Eli/../Secret" })).rejects.toBeInstanceOf(InvalidPathError);
  });
});

describe("MockSharePointClient.resolveLink", () => {
  const client = new MockSharePointClient({
    rootDir: path.resolve(import.meta.dirname, "../sample-data"),
    scope: new Scope(["Past Projects"]),
    maxDownloadBytes: 10_000_000,
  });

  it("opens folder and file links inside the allowed folders, including Slack-wrapped ones", async () => {
    const folder = await client.resolveLink("https://mock.sharepoint.local/Past%20Projects/2023");
    expect(folder).toMatchObject({ kind: "folder", path: "Past Projects/2023" });
    const [first] = await client.listFolder("Past Projects/2022/Northwind Grocers - Loyalty Pricing Review");
    const file = await client.resolveLink(`<${first!.webUrl}|deck>`);
    expect(file).toMatchObject({ kind: "file", path: first!.path });
  });

  it("refuses out-of-scope, missing and foreign links", async () => {
    await expect(client.resolveLink("https://mock.sharepoint.local/Client%20Confidential/Strategy%20Notes.md")).rejects.toBeInstanceOf(NotAllowedError);
    await expect(client.resolveLink("https://mock.sharepoint.local/Past%20Projects/nope")).rejects.toBeInstanceOf(NotAllowedError);
    await expect(client.resolveLink("https://mock.sharepoint.local/Past%20Projects/..%2F..%2Fetc")).rejects.toBeInstanceOf(InvalidPathError);
    await expect(client.resolveLink("https://evil.example.com/Past%20Projects")).rejects.toBeInstanceOf(InvalidPathError);
    await expect(client.resolveLink("http://mock.sharepoint.local/Past%20Projects")).rejects.toBeInstanceOf(InvalidPathError);
  });
});

// ---------------------------------------------------------------------------------------------- Google Drive

describe("parseDriveLink", () => {
  const id = "1AbCdEfGhIjKlMnOpQrStUv";
  it.each([
    [`https://drive.google.com/drive/folders/${id}?usp=sharing`, "folder"],
    [`https://drive.google.com/drive/u/0/folders/${id}`, "folder"],
    [`https://drive.google.com/file/d/${id}/view`, "file"],
    [`https://docs.google.com/presentation/d/${id}/edit#slide=id.p`, "file"],
    [`https://drive.google.com/open?id=${id}`, "unknown"],
    [id, "unknown"],
  ])("%s", (link, kind) => {
    expect(parseDriveLink(link)).toEqual({ kind, id });
  });

  it("rejects other hosts", () => {
    expect(() => parseDriveLink("https://example.com/drive/folders/abcdefghijk")).toThrow(InvalidPathError);
  });
});

class FakeDrive implements DriveApi {
  files: Array<DriveFile & { parent?: string; text?: string }> = [];
  fullText = new Map<string, string[]>();
  async get(id: string) {
    return this.files.find((f) => f.id === id);
  }
  async list(q: string) {
    const parent = /^'([^']+)' in parents/.exec(q)?.[1];
    if (parent) return { files: this.files.filter((f) => f.parent === parent) };
    const term = /fullText contains '([^']+)'/.exec(q)?.[1] ?? "";
    const ids = this.fullText.get(term) ?? [];
    return { files: this.files.filter((f) => ids.includes(f.id)) };
  }
  async download(id: string) {
    return Buffer.from(this.files.find((f) => f.id === id)?.text ?? "");
  }
  async exportAs(id: string, mime: string) {
    return Buffer.from(`${mime}:${this.files.find((f) => f.id === id)?.text ?? ""}`);
  }
}

describe("DriveReader", () => {
  const FOLDER = "application/vnd.google-apps.folder";
  function setup() {
    const api = new FakeDrive();
    api.files = [
      { id: "root000000", name: "YUCG Eli", mimeType: FOLDER },
      { id: "y20230000a", name: "2023", mimeType: FOLDER, parent: "root000000" },
      { id: "acme000000", name: "Acme Pricing.pptx", mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation", parent: "y20230000a", webViewLink: "https://d/acme" },
      { id: "notes00000", name: "Kickoff notes", mimeType: "application/vnd.google-apps.document", parent: "y20230000a", text: "pricing kickoff" },
      { id: "plain00000", name: "readme", mimeType: "text/plain", parent: "root000000", text: "hello there" },
      { id: "outside000", name: "Elsewhere.docx", mimeType: "text/plain", text: "pricing" },
    ];
    api.fullText.set("pricing", ["notes00000", "outside000"]);
    return { api, reader: new DriveReader(api, { maxDownloadBytes: 1000 }) };
  }

  it("lists the folder tree with paths", async () => {
    const { reader } = setup();
    const folder = await reader.resolve("https://drive.google.com/drive/folders/root000000");
    const { files } = await reader.listTree(folder);
    expect(files.map((f) => f.path).sort()).toEqual(["2023/Acme Pricing.pptx", "2023/Kickoff notes", "readme"]);
  });

  it("searches names and full text but only inside the linked folder", async () => {
    const { reader } = setup();
    const folder = await reader.resolve("root000000");
    const hits = await reader.search(folder, "pricing", 10);
    expect(hits.map((h) => h.id).sort()).toEqual(["acme000000", "notes00000"]);
  });

  it("exports Google Docs and reads plain uploads; unshared items are not found", async () => {
    const { reader } = setup();
    const doc = await reader.read("notes00000", 5000);
    expect(doc).toMatchObject({ format: "google-doc", text: "text/plain:pricing kickoff" });
    const txt = await reader.read("plain00000", 5000);
    expect(txt.text).toBe("hello there");
    await expect(reader.read("missing000", 5000)).rejects.toThrow(NOT_SHARED);
  });
});

// ---------------------------------------------------------------------------------------------- Slack flow

function fakeWeb() {
  const calls: Array<{ method: string; args: Record<string, unknown> }> = [];
  const rec = (method: string, result: unknown = { ok: true }) =>
    vi.fn(async (args: Record<string, unknown>) => {
      calls.push({ method, args });
      return result;
    });
  const client = {
    chat: { postMessage: rec("chat.postMessage"), postEphemeral: rec("chat.postEphemeral") },
    reactions: { add: rec("reactions.add"), remove: rec("reactions.remove") },
    conversations: {
      replies: rec("conversations.replies", {
        ok: true,
        messages: [
          { ts: "1.0", user: "UJORDAN1", text: "Acme wants pricing comps" },
          { ts: "2.0", user: "USAM0001", text: "<@UELI0001> any past projects?" },
          { ts: "3.0", user: "UJORDAN1", text: "later reply, not part of the request" },
        ],
      }),
    },
    users: {
      info: vi.fn(async ({ user }: { user: string }) => ({ ok: true, user: { profile: { display_name: names.get(user) ?? "" } } })),
    },
  };
  return { client: client as never, calls };
}

describe("EliSlack.handle", () => {
  const base = {
    botUserId: BOT,
    model: "claude-haiku-5-5",
    limits: { userPerHour: 5, workspacePerDay: 50 },
    usageLogPath: path.join(import.meta.dirname, "../.vitest-eli-usage.log"),
    log: () => undefined,
  };
  const req = { team: "T1", channel: "C1", ts: "2.0", threadTs: "1.0", user: "USAM0001", text: "any past projects?" };
  const ok = async (_messages: unknown[]) => ({
    text: "Yes: the 2022 Northwind pricing review.",
    usage: emptyUsage(),
    stopReason: "end_turn",
    toolsUsed: [] as string[],
  });

  it("reads the thread up to the request, answers in the thread with a markdown block, and cleans up the reaction", async () => {
    const answer = vi.fn(ok);
    const { client, calls } = fakeWeb();
    await new EliSlack({ ...base, answer }).handle(client, req);

    expect(answer).toHaveBeenCalledTimes(1);
    expect(answer.mock.calls[0]![0]).toEqual([
      { role: "user", content: "[Jordan Lee]: Acme wants pricing comps\n\n[Sam]: any past projects?" },
    ]);
    const post = calls.find((c) => c.method === "chat.postMessage")!;
    expect(post.args).toMatchObject({ channel: "C1", thread_ts: "1.0", blocks: [{ type: "markdown" }] });
    expect(calls.map((c) => c.method)).toEqual(["reactions.add", "conversations.replies", "chat.postMessage", "reactions.remove"]);
  });

  it("answers each message once, ignores other workspaces, and rate-limits privately", async () => {
    const answer = vi.fn(ok);
    const { client, calls } = fakeWeb();
    const eli = new EliSlack({ ...base, limits: { userPerHour: 1, workspacePerDay: 50 }, allowedTeamId: "T1", answer });
    await eli.handle(client, req);
    await eli.handle(client, req); // duplicate delivery
    await eli.handle(client, { ...req, team: "T999", ts: "9.0" });
    await eli.handle(client, { ...req, ts: "4.0" }); // second request this hour
    expect(answer).toHaveBeenCalledTimes(1);
    const eph = calls.find((c) => c.method === "chat.postEphemeral");
    expect(eph?.args).toMatchObject({ user: "USAM0001", text: expect.stringContaining("limit of 1 request per hour") });
  });

  it("answers `stats` from the log without calling Claude or using a request: in the thread in a DM, privately in a channel", async () => {
    const answer = vi.fn(ok);
    const { client, calls } = fakeWeb();
    const eli = new EliSlack({ ...base, limits: { userPerHour: 1, workspacePerDay: 50 }, answer });
    await eli.handle(client, { ...req, channel: "D1", ts: "5.0", text: "stats" });
    await eli.handle(client, { ...req, ts: "6.0", text: `<@${BOT}> my usage` });
    expect(answer).not.toHaveBeenCalled();
    const dm = calls.find((c) => c.method === "chat.postMessage")!;
    expect(dm.args).toMatchObject({ channel: "D1", thread_ts: "1.0", text: expect.stringContaining("Your Eli usage") });
    const eph = calls.find((c) => c.method === "chat.postEphemeral")!;
    expect(eph.args).toMatchObject({ channel: "C1", user: "USAM0001", text: expect.stringContaining("1 of 1 requests left this hour") });
    await eli.handle(client, { ...req, ts: "7.0" }); // the hour's one request is still available
    expect(answer).toHaveBeenCalledTimes(1);
  });

  it("answers `help` without calling Claude or using a request", async () => {
    const answer = vi.fn(ok);
    const { client, calls } = fakeWeb();
    const eli = new EliSlack({ ...base, limits: { userPerHour: 1, workspacePerDay: 50 }, answer, help: (u) => `help for ${u}` });
    await eli.handle(client, { ...req, channel: "D1", ts: "8.0", text: "help" });
    expect(answer).not.toHaveBeenCalled();
    expect(calls.find((c) => c.method === "chat.postMessage")!.args).toMatchObject({ channel: "D1", text: "help for USAM0001" });
    const tour = new EliSlack({ ...base, answer, onboard: (u) => `tour for ${u}` });
    await tour.handle(client, { ...req, channel: "D1", ts: "8.5", text: "get started" });
    expect(answer).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.method === "chat.postMessage").at(-1)!.args).toMatchObject({ text: "tour for USAM0001" });
    await eli.handle(client, { ...req, ts: "9.0", text: "help me draft an email" }); // a real request
    expect(answer).toHaveBeenCalledTimes(1);
  });

  it("posts a short apology, not the error, when answering fails", async () => {
    const { client, calls } = fakeWeb();
    await new EliSlack({ ...base, answer: async () => Promise.reject(new Error("internal detail")) }).handle(client, req);
    const post = calls.find((c) => c.method === "chat.postMessage")!;
    expect(post.args.text).toBe("Sorry, something went wrong on my end.");
    expect(calls.at(-1)!.method).toBe("reactions.remove");
  });
});

// ---------------------------------------------------------------------------------------------- Slack attachments

import { threadFiles } from "../src/eli/thread.js";
import { SLACK_FILE_TOOL, slackDownloader, slackFileTools } from "../src/eli/tools/slackFiles.js";
import { FileTooLargeError } from "../src/sharepoint/errors.js";

describe("Slack attachments", () => {
  const thread: SlackMessage[] = [
    { ts: "1", user: "USAM0001", text: "deck attached", files: [{ id: "F1", name: "notes.txt", size: 11, url_private_download: "https://files.slack.com/files-pri/T1-F1/notes.txt" }] },
    { ts: "2", user: "USAM0001", text: "no download url", files: [{ id: "F2", name: "x.pdf" }] },
  ];
  const runTool = async (tools: ReturnType<typeof slackFileTools>, input: unknown) => {
    const t = tools[0] as unknown as { parse?: (i: unknown) => unknown; run: (i: unknown) => Promise<string> };
    return t.run(t.parse ? t.parse(input) : input).catch((e) => e);
  };

  it("shows file ids in the thread text and collects only downloadable files", () => {
    const msgs = threadToMessages(thread, { botUserId: BOT, names });
    expect(msgs[0]!.content).toContain("(attached: notes.txt [file F1])");
    expect(threadFiles(thread).map((f) => f.id)).toEqual(["F1"]);
  });

  it("reads an attached file and refuses ids that are not in this thread", async () => {
    const download = vi.fn(async () => Buffer.from("hello world"));
    const tools = slackFileTools(threadFiles(thread), download, 1000);
    expect(tools.map((t) => t.name)).toEqual([SLACK_FILE_TOOL]);
    const ok = JSON.parse(await runTool(tools, { file_id: "F1" }));
    expect(ok).toMatchObject({ file: { id: "F1", name: "notes.txt" }, content: "hello world" });
    expect(download).toHaveBeenCalledWith("https://files.slack.com/files-pri/T1-F1/notes.txt", 1000);

    const refused = await runTool(tools, { file_id: "F999" });
    expect(refused.content).toBe("That file is not attached to this thread, so Eli cannot open it.");
    expect(slackFileTools([], download, 1000)).toEqual([]);
  });

  it("only sends the bot token to files.slack.com and enforces the size limit", async () => {
    const fetchImpl = vi.fn(async () => new Response("abcdef", { headers: { "content-type": "text/plain" } }));
    const dl = slackDownloader("xoxb-secret", fetchImpl as unknown as typeof fetch);
    await expect(dl("https://evil.example.com/f", 100)).rejects.toThrow("Unexpected Slack file URL.");
    expect(fetchImpl).not.toHaveBeenCalled();
    expect((await dl("https://files.slack.com/f", 100)).toString()).toBe("abcdef");
    const init = (fetchImpl.mock.calls[0] as unknown as [URL, RequestInit])[1];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer xoxb-secret");
    await expect(dl("https://files.slack.com/f", 3)).rejects.toBeInstanceOf(FileTooLargeError);
  });

  it("explains a missing files:read scope instead of parsing Slack's sign-in page", async () => {
    const fetchImpl = async () => new Response("<html>", { headers: { "content-type": "text/html; charset=utf-8" } });
    await expect(slackDownloader("t", fetchImpl as typeof fetch)("https://files.slack.com/f", 100)).rejects.toThrow(/files:read/);
  });
});

describe("buildSystemPrompt safety wording", () => {
  it("never steers confidential files into the shared folder, and speaks in the first person", () => {
    const p = buildSystemPrompt({ sharepoint: true, gdrive: true });
    expect(p).toContain("never suggest it for anything marked confidential");
    expect(p).not.toContain("suggest moving the file into the shared Eli folder");
    expect(p).toContain('Speak as yourself ("I can…")');
  });
});
