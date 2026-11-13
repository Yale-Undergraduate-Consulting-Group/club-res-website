import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  type CommandDeps,
  type CommandRequest,
  type HelpFeatures,
  MAX_REQUEST_CHARS,
  RequestLimiter,
  handleHelpCommand,
  handleOnboardCommand,
  isOnboardRequest,
  onboardText,
  handleRequestsCommand,
  helpEntries,
  helpText,
  isHelpRequest,
} from "../src/eli/commands.js";
import { loadEliConfig } from "../src/eli/config.js";

const ALL: HelpFeatures = { webSearch: true, reminders: true, links: true, requests: true, isAdmin: true };
const words = (s: string) => s.trim().split(/\s+/).length;

describe("help", () => {
  it("keeps every summary under 10 words", () => {
    const { commands, asks } = helpEntries(ALL);
    for (const [what, does] of [...commands, ...asks]) expect(words(does), `${what}: ${does}`).toBeLessThan(10);
  });

  it("lists every slash command, and only the features that are on", () => {
    const full = helpText(ALL);
    for (const c of ["/help", "/eli-stats", "/eli-stats [days]", "/requests <your idea>"]) expect(full).toContain(`\`${c}\``);
    expect(full).toContain("web");
    const bare = helpText({ webSearch: false, reminders: false, links: false, requests: false, isAdmin: false });
    expect(bare).not.toMatch(/\/requests|\[days\]|search the web|remind me|SharePoint/);
    expect(bare).toContain("`/eli-stats`");
  });

  it("recognises help in a DM, but not real questions", () => {
    for (const t of ["help", "Help!", "/help", "commands", "<@U0BOT1> help", "help *Sent using* <@U0C67DKFT25>"]) {
      expect(isHelpRequest(t), t).toBe(true);
    }
    for (const t of ["help me draft an email", "can you help?", "helpful", "what can you do"]) expect(isHelpRequest(t), t).toBe(false);
  });
});

async function setup(over: Partial<CommandDeps> = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "eli-cmd-"));
  const deps: CommandDeps = {
    allowedTeamId: "T1",
    adminUserIds: new Set(["UADMIN01"]),
    requestRecipients: new Set(["URYAN001"]),
    requestLogPath: path.join(dir, "eli-requests.log"),
    features: { webSearch: false, reminders: true, links: true },
    requestLimiter: new RequestLimiter(),
    log: () => undefined,
    now: () => new Date("2026-11-12T20:00:00Z"),
    ...over,
  };
  const run = async (text: string, user = "USAM0001", { channel = "C0GENERAL", team = "T1", fail = false } = {}) => {
    const respond = vi.fn(async (_m: unknown) => undefined);
    const postMessage = vi.fn(async (_m: unknown) => {
      if (fail) throw new Error("channel_not_found");
      return { ok: true };
    });
    const req = {
      command: { user_id: user, team_id: team, text, channel_id: channel },
      ack: async () => undefined,
      respond,
      client: { chat: { postMessage } },
    } as unknown as CommandRequest;
    await handleRequestsCommand(req, deps);
    return {
      replies: respond.mock.calls.map((c) => c[0] as { response_type: string; text: string }),
      posts: postMessage.mock.calls.map((c) => c[0] as { channel: string; text: string }),
    };
  };
  return { deps, run, log: () => fs.readFile(deps.requestLogPath, "utf8").catch(() => "") };
}

describe("/requests", () => {
  it("DMs the maintainer with who asked and where, confirms privately, and keeps a backup", async () => {
    const { run, log } = await setup();
    const { replies, posts } = await run("let Eli read Notion pages\nand Google Calendar");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.channel).toBe("URYAN001");
    expect(posts[0]!.text).toContain("Feature request from <@USAM0001>");
    expect(posts[0]!.text).toContain("in <#C0GENERAL>");
    expect(posts[0]!.text).toContain("> let Eli read Notion pages\n> and Google Calendar");
    expect(replies).toEqual([expect.objectContaining({ response_type: "ephemeral" })]);
    expect(replies[0]!.text).toContain("sent your request to <@URYAN001>");
    const line = JSON.parse((await log()).trim());
    expect(line).toMatchObject({ user: "USAM0001", channel: "C0GENERAL", text: "let Eli read Notion pages\nand Google Calendar" });
  });

  it("leaves DM channel ids out and strips @channel / @here", async () => {
    const { run } = await setup();
    const { posts } = await run("<!channel> please add @here dark mode", "USAM0001", { channel: "D0ABC" });
    expect(posts[0]!.text).not.toContain("<#D0ABC>");
    expect(posts[0]!.text).not.toMatch(/<!channel>|@here/);
  });

  it("asks for the idea when the text is empty or too long, and sends nothing", async () => {
    const { run } = await setup();
    const empty = await run("   ");
    expect(empty.posts).toHaveLength(0);
    expect(empty.replies[0]!.text).toContain("Add your idea after the command");
    const long = await run("x".repeat(MAX_REQUEST_CHARS + 1));
    expect(long.posts).toHaveLength(0);
    expect(long.replies[0]!.text).toContain("keep it under");
  });

  it("limits each person to 5 a day", async () => {
    const { run } = await setup();
    for (let i = 0; i < 5; i++) expect((await run(`idea ${i}`)).posts).toHaveLength(1);
    const sixth = await run("idea 6");
    expect(sixth.posts).toHaveLength(0);
    expect(sixth.replies[0]!.text).toContain("daily limit");
    expect((await run("someone else's idea", "UOTHER01")).posts).toHaveLength(1);
  });

  it("still saves the request when the DM fails, and says so", async () => {
    const { run, log } = await setup();
    const { replies } = await run("offline idea", "USAM0001", { fail: true });
    expect(replies[0]!.text).toContain("couldn't deliver");
    expect(await log()).toContain("offline idea");
  });

  it("is off without recipients, and ignores other workspaces", async () => {
    const off = await setup({ requestRecipients: new Set() });
    expect((await off.run("idea")).replies[0]!.text).toContain("aren't set up");
    const { run } = await setup();
    const other = await run("idea", "USAM0001", { team: "T2" });
    expect(other.posts).toHaveLength(0);
    expect(other.replies).toHaveLength(0);
  });
});

describe("/help command", () => {
  it("answers privately, with the admin line only for admins", async () => {
    const { deps } = await setup();
    const ask = async (user: string) => {
      const respond = vi.fn(async (_m: unknown) => undefined);
      const req = {
        command: { user_id: user, team_id: "T1", text: "", channel_id: "C1" },
        ack: async () => undefined,
        respond,
        client: { chat: { postMessage: vi.fn() } },
      } as unknown as CommandRequest;
      await handleHelpCommand(req, deps);
      return (respond.mock.calls[0]![0] as { response_type: string; text: string });
    };
    const member = await ask("USAM0001");
    expect(member.response_type).toBe("ephemeral");
    expect(member.text).toContain("/requests");
    expect(member.text).not.toContain("[days]");
    expect((await ask("UADMIN01")).text).toContain("[days]");
  });
});

describe("config", () => {
  const base = { SLACK_BOT_TOKEN: "xoxb-x", SLACK_APP_TOKEN: "xapp-x", ANTHROPIC_API_KEY: "k", ELI_SHAREPOINT: "off" };
  it("sends requests to the admins unless ELI_FEATURE_REQUESTS_TO says otherwise", () => {
    expect([...loadEliConfig({ ...base, ELI_ADMIN_USER_IDS: "UADMIN01" }).requestRecipients]).toEqual(["UADMIN01"]);
    expect([...loadEliConfig({ ...base, ELI_ADMIN_USER_IDS: "UADMIN01", ELI_FEATURE_REQUESTS_TO: "URYAN001" }).requestRecipients]).toEqual([
      "URYAN001",
    ]);
    expect(loadEliConfig({ ...base, ELI_ADMIN_USER_IDS: "UADMIN01", ELI_FEATURE_REQUESTS_TO: "off" }).requestRecipients.size).toBe(0);
    expect(() => loadEliConfig({ ...base, ELI_FEATURE_REQUESTS_TO: "ryan" })).toThrow(/ELI_FEATURE_REQUESTS_TO/);
  });
});

describe("/onboard", () => {
  it("tours only the features that are on, with the hourly limit and commands", () => {
    const full = onboardText({ ...ALL, userPerHour: 20 });
    expect(full).toContain("Welcome to Eli");
    expect(full).toMatch(/SharePoint or Google Drive folder link/);
    expect(full).toMatch(/search the web/);
    expect(full).toMatch(/Remind me Thursday/);
    expect(full).toContain("You get 20 requests an hour.");
    for (const c of ["/help", "/eli-stats", "/requests <idea>"]) expect(full).toContain(`\`${c}\``);
    const bare = onboardText({ webSearch: false, reminders: false, links: false, requests: false, isAdmin: false });
    expect(bare).not.toMatch(/Drive|search the web|Remind me|\/requests/);
    expect(bare.length).toBeLessThan(1500);
  });

  it("is listed in /help with a short summary, and answers privately", async () => {
    expect(helpText(ALL)).toContain("`/onboard`");
    const { deps } = await setup();
    const respond = vi.fn(async (_m: unknown) => undefined);
    const req = {
      command: { user_id: "USAM0001", team_id: "T1", text: "", channel_id: "C1" },
      ack: async () => undefined,
      respond,
      client: { chat: { postMessage: vi.fn() } },
    } as unknown as CommandRequest;
    await handleOnboardCommand(req, deps);
    expect(respond.mock.calls[0]![0]).toMatchObject({ response_type: "ephemeral", text: expect.stringContaining("Welcome to Eli") });
  });

  it("recognises onboard / get started in a DM, but not real questions", () => {
    for (const t of ["onboard", "Onboard me", "/onboard", "get started", "Getting started!", "<@U0BOT1> onboarding"]) {
      expect(isOnboardRequest(t), t).toBe(true);
    }
    for (const t of ["onboard the new analysts", "how do I get started on the Acme deck", "start"]) expect(isOnboardRequest(t), t).toBe(false);
  });
});
