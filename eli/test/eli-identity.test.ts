import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadEliConfig } from "../src/eli/config.js";
import { emptyUsage } from "../src/eli/limits.js";
import { EliSlack } from "../src/eli/slack.js";
import { createIdentityProvider, parseIdentityConfig, type SlackProfileReader } from "../src/eli/identity/index.js";
import { handleLinkCommand, handleUnlinkCommand, handleWhoamiCommand } from "../src/eli/identity/commands.js";
import { LocalIdentityProvider } from "../src/eli/identity/local.js";
import { PlatformIdentityProvider } from "../src/eli/identity/platform.js";

let dir: string;
let rosterPath: string;
let linksPath: string;

const roster = (members: unknown[]) => fs.writeFile(rosterPath, JSON.stringify({ members }));
const ana = { id: "ana", email: "Ana@Yale.edu", name: "Ana", role: "board", projects: ["Acme"] };

type Profile = { email?: string; is_restricted?: boolean; is_bot?: boolean; deleted?: boolean };
const slackWith = (profiles: Record<string, Profile>): SlackProfileReader => ({
  users: {
    info: vi.fn(async ({ user }: { user: string }) => {
      const p = profiles[user];
      return { user: p ? { is_bot: p.is_bot, deleted: p.deleted, is_restricted: p.is_restricted, profile: { email: p.email } } : undefined };
    }),
  },
});

beforeEach(async () => {
  dir = await fs.mkdtemp(path.join(os.tmpdir(), "eli-identity-"));
  rosterPath = path.join(dir, "members.json");
  linksPath = path.join(dir, "links.json");
  await roster([ana]);
});
afterEach(() => fs.rm(dir, { recursive: true, force: true }));

const local = () => new LocalIdentityProvider({ rosterPath, linksPath, log: () => undefined });

describe("LocalIdentityProvider", () => {
  it("links by the Slack profile email (case-insensitive), persists it, and resolves it", async () => {
    const id = local();
    expect(await id.resolve("T1", "U1")).toBeNull();
    const r = await id.link("T1", "U1", slackWith({ U1: { email: "ana@yale.EDU" } }));
    expect(r).toMatchObject({ ok: true, alreadyLinked: false, member: { id: "ana", role: "board", projects: ["Acme"], source: "local" } });
    expect(await local().resolve("T1", "U1")).toMatchObject({ id: "ana" }); // a fresh instance reads the file
    expect((await fs.stat(linksPath)).mode & 0o777).toBe(0o600);
    expect(await id.link("T1", "U1", slackWith({ U1: { email: "ana@yale.edu" } }))).toMatchObject({ ok: true, alreadyLinked: true });
    expect(await id.resolve("T2", "U1")).toBeNull(); // links are per workspace
  });

  it("refuses emails off the roster, guests, bots, and profiles without a visible email", async () => {
    const id = local();
    const slack = slackWith({ U1: { email: "bob@yale.edu" }, U2: { email: "ana@yale.edu", is_restricted: true }, U3: { is_bot: true }, U4: {} });
    expect(await id.link("T1", "U1", slack)).toMatchObject({ ok: false, reason: "not_on_roster" });
    expect(await id.link("T1", "U2", slack)).toMatchObject({ ok: false, message: expect.stringContaining("Guest") });
    expect(await id.link("T1", "U3", slack)).toMatchObject({ ok: false, reason: "no_email" });
    expect(await id.link("T1", "U4", slack)).toMatchObject({ ok: false, message: expect.stringContaining("users:read.email") });
    await expect(fs.stat(linksPath)).rejects.toThrow(); // nothing written
  });

  it("revokes at once when someone is removed from the roster or their email changes", async () => {
    const id = local();
    await id.link("T1", "U1", slackWith({ U1: { email: "ana@yale.edu" } }));
    await roster([{ ...ana, email: "ana.new@yale.edu" }]);
    await fs.utimes(rosterPath, new Date(), new Date(Date.now() + 5000)); // make sure the mtime moves
    expect(await id.resolve("T1", "U1")).toBeNull();
    await roster([]);
    await fs.utimes(rosterPath, new Date(), new Date(Date.now() + 10000));
    expect(await id.resolve("T1", "U1")).toBeNull();
  });

  it("keeps one Slack account per member per workspace, and unlinks", async () => {
    const id = local();
    await id.link("T1", "U1", slackWith({ U1: { email: "ana@yale.edu" } }));
    await id.link("T1", "U9", slackWith({ U9: { email: "ana@yale.edu" } }));
    expect(await id.resolve("T1", "U1")).toBeNull();
    expect(await id.resolve("T1", "U9")).toMatchObject({ id: "ana" });
    expect(await id.unlink("T1", "U9")).toBe(true);
    expect(await id.unlink("T1", "U9")).toBe(false);
    expect(await id.resolve("T1", "U9")).toBeNull();
  });

  it("rejects a roster with duplicate emails and reports linking as unavailable", async () => {
    await roster([ana, { ...ana, id: "ana2" }]);
    expect(await local().link("T1", "U1", slackWith({ U1: { email: "ana@yale.edu" } }))).toMatchObject({ ok: false, reason: "unavailable" });
  });
});

describe("PlatformIdentityProvider (proposed contract)", () => {
  const member = { id: 42, email: "ana@yale.edu", name: "Ana", role: "member", projects: ["Acme"] };
  const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status });

  it("sends the service token, maps 200 to a member and 404 to null, and caches", async () => {
    const f = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe("https://club.example/api/agent/v1/members/by-slack?team_id=T1&user_id=U1");
      expect((init?.headers as Record<string, string>).authorization).toBe(`Bearer ${"s".repeat(32)}`);
      return json(200, { member });
    });
    const id = new PlatformIdentityProvider({ baseUrl: "https://club.example", token: "s".repeat(32), fetch: f as typeof fetch, log: () => undefined });
    expect(await id.resolve("T1", "U1")).toEqual({ id: "42", email: "ana@yale.edu", name: "Ana", role: "member", projects: ["Acme"], source: "platform" });
    await id.resolve("T1", "U1");
    expect(f).toHaveBeenCalledTimes(1);

    const none = new PlatformIdentityProvider({ baseUrl: "https://club.example", token: "t", fetch: (async () => json(404)) as typeof fetch });
    expect(await none.resolve("T1", "U2")).toBeNull();
  });

  it("fails closed on errors and bad responses, without caching them", async () => {
    let n = 0;
    const f = vi.fn(async () => (++n === 1 ? json(500) : n === 2 ? json(200, { nope: true }) : json(200, { member })));
    const id = new PlatformIdentityProvider({ baseUrl: "https://club.example", token: "t", fetch: f as typeof fetch, log: () => undefined });
    expect(await id.resolve("T1", "U1")).toBeNull();
    expect(await id.resolve("T1", "U1")).toBeNull();
    expect(await id.resolve("T1", "U1")).toMatchObject({ id: "42" });
  });

  it("points people at the website to link, and never unlinks itself", async () => {
    const id = new PlatformIdentityProvider({ baseUrl: "https://club.example", token: "t", linkPageUrl: "https://club.example/profile", fetch: (async () => json(404)) as typeof fetch });
    expect(await id.link("T1", "U1")).toMatchObject({ ok: false, reason: "elsewhere", message: expect.stringContaining("https://club.example/profile") });
    expect(await id.unlink()).toBe(false);
  });
});

describe("identity config", () => {
  it("is off by default and validates each mode", () => {
    expect(parseIdentityConfig({})).toEqual({ mode: "off" });
    expect(parseIdentityConfig({ ELI_IDENTITY: "local", ELI_REQUIRE_LINK: "on" })).toEqual({
      mode: "local", rosterPath: "eli-members.json", linksPath: "eli-links.json", requireLink: true,
    });
    expect(() => parseIdentityConfig({ ELI_REQUIRE_LINK: "on" })).toThrow(/needs ELI_IDENTITY/);
    expect(() => parseIdentityConfig({ ELI_IDENTITY: "platform" })).toThrow(/ELI_PLATFORM_URL is required/);
    expect(() => parseIdentityConfig({ ELI_IDENTITY: "platform", ELI_PLATFORM_URL: "http://club.example", ELI_PLATFORM_TOKEN: "x".repeat(32) })).toThrow(/https/);
    const token = "secret-token-value-that-is-long-enough";
    try {
      parseIdentityConfig({ ELI_IDENTITY: "platform", ELI_PLATFORM_URL: "http://club.example", ELI_PLATFORM_TOKEN: token });
    } catch (err) {
      expect((err as Error).message).not.toContain(token);
    }
    expect(createIdentityProvider({ mode: "off" })).toBeUndefined();
  });

  it("is part of loadEliConfig", () => {
    const env = { ANTHROPIC_API_KEY: "k", ELI_SHAREPOINT: "off", ELI_IDENTITY: "local" };
    expect(loadEliConfig(env, { needSlack: false }).identity).toMatchObject({ mode: "local" });
    expect(() => loadEliConfig({ ...env, ELI_IDENTITY: "platform" }, { needSlack: false })).toThrow(/ELI_PLATFORM_URL/);
  });
});

describe("identity slash commands", () => {
  const cmd = (user = "U1", team = "T1") => {
    const respond = vi.fn(async (_m: unknown) => undefined);
    return { respond, req: (client: SlackProfileReader) => ({ command: { user_id: user, team_id: team, text: "", channel_id: "C1" }, ack: async () => undefined, respond, client }) };
  };
  const text = (respond: ReturnType<typeof vi.fn>) => JSON.stringify(respond.mock.calls.at(-1)?.[0]);

  it("links, shows who you are, and unlinks, privately", async () => {
    const deps = { identity: local(), allowedTeamId: "T1", log: () => undefined };
    const slack = slackWith({ U1: { email: "ana@yale.edu" } });
    const c = cmd();
    await handleWhoamiCommand(c.req(slack), deps);
    expect(text(c.respond)).toContain("not linked");
    await handleLinkCommand(c.req(slack), deps);
    expect(c.respond.mock.calls.at(-1)?.[0]).toMatchObject({ response_type: "ephemeral" });
    expect(text(c.respond)).toContain("Linked");
    await handleWhoamiCommand(c.req(slack), deps);
    expect(text(c.respond)).toContain("Ana");
    await handleUnlinkCommand(c.req(slack), deps);
    expect(text(c.respond)).toContain("Unlinked");
  });

  it("ignores other workspaces", async () => {
    const deps = { identity: local(), allowedTeamId: "T1", log: () => undefined };
    const c = cmd("U1", "T999");
    await handleLinkCommand(c.req(slackWith({ U1: { email: "ana@yale.edu" } })), deps);
    expect(c.respond).not.toHaveBeenCalled();
  });
});

describe("EliSlack with identity", () => {
  const fakeWeb = () => {
    const calls: Array<{ method: string; args: Record<string, unknown> }> = [];
    const rec = (method: string, result: unknown = { ok: true }) =>
      vi.fn(async (args: Record<string, unknown>) => {
        calls.push({ method, args });
        return result;
      });
    const client = {
      chat: { postMessage: rec("chat.postMessage"), postEphemeral: rec("chat.postEphemeral") },
      reactions: { add: rec("reactions.add"), remove: rec("reactions.remove") },
      conversations: { replies: rec("conversations.replies", { ok: true, messages: [{ ts: "2.0", user: "U1", text: "hi" }] }) },
      users: { info: vi.fn(async () => ({ ok: true, user: { profile: { display_name: "Ana" } } })) },
    };
    return { client: client as never, calls };
  };
  const base = {
    botUserId: "UELI",
    model: "claude-haiku-5-5",
    limits: { userPerHour: 5, workspacePerDay: 50 },
    usageLogPath: path.join(import.meta.dirname, "../.vitest-eli-usage.log"),
    log: () => undefined,
  };
  const req = { team: "T1", channel: "C1", ts: "2.0", threadTs: "1.0", user: "U1", text: "hi" };
  const ok = vi.fn(async (_m: unknown[]) => ({ text: "hello", usage: emptyUsage(), stopReason: "end_turn", toolsUsed: [] as string[] }));

  it("refuses unlinked people when linking is required, without calling Claude", async () => {
    const { client, calls } = fakeWeb();
    const answer = vi.fn(ok);
    await new EliSlack({ ...base, answer, identity: local(), requireLink: true }).handle(client, req);
    expect(answer).not.toHaveBeenCalled();
    expect(calls.find((c) => c.method === "chat.postEphemeral")!.args).toMatchObject({ user: "U1", text: expect.stringContaining("/eli-link") });
  });

  it("tells Claude who is asking (no email) once linked", async () => {
    const identity = local();
    await identity.link("T1", "U1", slackWith({ U1: { email: "ana@yale.edu" } }));
    const answer = vi.fn(ok);
    const { client } = fakeWeb();
    await new EliSlack({ ...base, answer, identity, requireLink: true }).handle(client, { ...req, ts: "3.0" });
    const sent = JSON.stringify(answer.mock.calls[0]![0]);
    expect(sent).toContain("the person asking is Ana, role board, projects: Acme");
    expect(sent).not.toContain("ana@yale.edu");
  });
});
