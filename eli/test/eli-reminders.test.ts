import { ToolError } from "@anthropic-ai/sdk/lib/tools/ToolError";
import { describe, expect, it, vi } from "vitest";
import {
  CANCEL_ACTION,
  MAX_AHEAD_MS,
  REMINDERS_PROMPT,
  ReminderStore,
  SCHEDULE_ACTION,
  type ReminderClick,
  type ZoneInfo,
  buildReminderText,
  handleReminderClick,
  parseReminderConfig,
  parseReminderText,
  registerReminderActions,
  reminderContext,
  resolveWhen,
  stripBroadcasts,
  withContextNote,
  zonedToUtc,
} from "../src/eli/reminders.js";
import {
  CANCEL_REMINDER_TOOL,
  LIST_REMINDERS_TOOL,
  SCHEDULE_REMINDER_TOOL,
  reminderRequest,
  reminderTools,
} from "../src/eli/tools/reminders.js";

const NY: ZoneInfo = { tz: "America/New_York", assumed: false };
const BOT = "UELI0001";
const ME = "USAM0001";
const JORDAN = "UJORDAN1";
const OUTSIDER = "UOUTSIDE1";
/** Thu 2026-11-12 14:03 in New York (EST, UTC-5). */
const NOW = Date.parse("2026-11-12T19:03:00Z");

// ---------------------------------------------------------------------------------------------- fake Slack

interface Scheduled {
  id: string;
  channel_id: string;
  post_at: number;
  text: string;
  thread_ts?: string;
}

function slackErr(code: string): Error {
  return Object.assign(new Error(`An API error occurred: ${code}`), { data: { ok: false, error: code } });
}

function fakeWeb(opts: { members?: string[]; scheduled?: Scheduled[]; tz?: string; failMembers?: string; failSchedule?: string } = {}) {
  const calls: Array<{ method: string; args: Record<string, unknown> }> = [];
  const scheduled: Scheduled[] = opts.scheduled ?? [];
  const members = opts.members ?? [ME, JORDAN, BOT];
  let nextId = 1;
  const rec = <R>(method: string, impl: (args: Record<string, unknown>) => R | Promise<R>) =>
    vi.fn(async (args: Record<string, unknown> = {}) => {
      calls.push({ method, args });
      return impl(args);
    });
  const page = <T>(items: T[], cursor: unknown, size: number) => {
    const start = Number(cursor ?? 0);
    const next = start + size < items.length ? String(start + size) : "";
    return { slice: items.slice(start, start + size), next };
  };
  const client = {
    chat: {
      postMessage: rec("chat.postMessage", () => ({ ok: true, ts: "9.0001" })),
      postEphemeral: rec("chat.postEphemeral", () => ({ ok: true })),
      update: rec("chat.update", () => ({ ok: true })),
      scheduleMessage: rec("chat.scheduleMessage", (a) => {
        if (opts.failSchedule) throw slackErr(opts.failSchedule);
        const id = `Q${String(nextId++).padStart(4, "0")}`;
        scheduled.push({ id, channel_id: a.channel as string, post_at: a.post_at as number, text: a.text as string, thread_ts: a.thread_ts as string | undefined });
        return { ok: true, scheduled_message_id: id, post_at: a.post_at };
      }),
      deleteScheduledMessage: rec("chat.deleteScheduledMessage", (a) => {
        const i = scheduled.findIndex((s) => s.id === a.scheduled_message_id && s.channel_id === a.channel);
        if (i < 0) throw slackErr("invalid_scheduled_message_id");
        scheduled.splice(i, 1);
        return { ok: true };
      }),
      scheduledMessages: {
        // Two per page, so pagination is exercised.
        list: rec("chat.scheduledMessages.list", (a) => {
          const items = scheduled.filter((s) => !a.channel || s.channel_id === a.channel);
          const { slice, next } = page(items, a.cursor, 2);
          return {
            ok: true,
            scheduled_messages: slice.map(({ id, channel_id, post_at, text }) => ({ id, channel_id, post_at, text, date_created: 1 })),
            response_metadata: { next_cursor: next },
          };
        }),
      },
    },
    conversations: {
      members: rec("conversations.members", (a) => {
        if (opts.failMembers) throw slackErr(opts.failMembers);
        const { slice, next } = page(members, a.cursor, 2);
        return { ok: true, members: slice, response_metadata: { next_cursor: next } };
      }),
    },
    users: {
      info: rec("users.info", (a) => ({
        ok: true,
        user: { tz: opts.tz ?? "America/New_York", profile: { display_name: { [ME]: "Sam", [JORDAN]: "Jordan Lee" }[a.user as string] ?? "" } },
      })),
    },
  };
  return { client: client as never, calls, scheduled };
}

type Runnable = { name: string; run: (input: unknown) => Promise<string> };

function tools(web: ReturnType<typeof fakeWeb>, over: Partial<Parameters<typeof reminderTools>[0]> = {}) {
  let now = NOW;
  const store = new ReminderStore({ now: () => now });
  const list = reminderTools({
    client: web.client,
    channel: "C1",
    threadTs: "1.0",
    requester: ME,
    botUserId: BOT,
    zone: NY,
    store,
    maxPerUser: 5,
    now: () => now,
    ...over,
  }) as unknown as Runnable[];
  const by = (name: string) => list.find((t) => t.name === name)!;
  const call = async (name: string, input: unknown) => JSON.parse(await by(name).run(input));
  return { store, call, advance: (ms: number) => (now += ms), setNow: (t: number) => (now = t) };
}

const reminderInput = (over: Record<string, unknown> = {}) => ({ when: "2026-11-19T09:00", message: "send the Acme deck", in_thread: true, ...over });

// ---------------------------------------------------------------------------------------------- time

describe("resolveWhen", () => {
  it("reads a local time in the requester's zone and states it", () => {
    const t = resolveWhen("2026-11-19T09:00", NY, NOW);
    expect(t.postAt).toBe(Date.parse("2026-11-19T14:00:00Z") / 1000);
    expect(t.local).toBe("Thu, Nov 19, 2026, 9:00 AM EST");
    expect(t.iso).toBe("2026-11-19T09:00:00-05:00");
    expect(resolveWhen("2026-11-19 9:00", NY, NOW).postAt).toBe(t.postAt);
  });

  it("honours an explicit offset or Z, whatever the requester's zone", () => {
    expect(resolveWhen("2026-11-19T09:00-08:00", NY, NOW).postAt).toBe(Date.parse("2026-11-19T17:00:00Z") / 1000);
    expect(resolveWhen("2026-11-19T09:00:00Z", NY, NOW).local).toBe("Thu, Nov 19, 2026, 4:00 AM EST");
    expect(resolveWhen("2026-11-19T09:00+0530", NY, NOW).postAt).toBe(Date.parse("2026-11-19T03:30:00Z") / 1000);
  });

  it("uses other zones, including half-hour offsets", () => {
    const kolkata = resolveWhen("2026-11-13T09:00", { tz: "Asia/Kolkata", assumed: false }, NOW);
    expect(kolkata.postAt).toBe(Date.parse("2026-11-13T03:30:00Z") / 1000);
    expect(kolkata.iso).toBe("2026-11-13T09:00:00+05:30");
    const london = resolveWhen("2026-11-13T09:00", { tz: "Europe/London", assumed: false }, NOW);
    expect(london.postAt).toBe(Date.parse("2026-11-13T09:00:00Z") / 1000);
  });

  it("handles DST: summer offset after spring-forward, skipped times move later, repeated times take the first", () => {
    const march = Date.parse("2027-03-10T15:00:00Z");
    expect(resolveWhen("2027-03-12T09:00", NY, march).iso).toBe("2027-03-12T09:00:00-05:00");
    expect(resolveWhen("2027-03-15T09:00", NY, march).iso).toBe("2027-03-15T09:00:00-04:00");
    const gap = resolveWhen("2027-03-14T02:30", NY, march);
    expect(gap.shifted).toBe(true);
    expect(gap.iso).toBe("2027-03-14T03:30:00-04:00");
    // 2026-11-01 01:30 happens twice in New York; the earlier (EDT) one is used.
    expect(zonedToUtc({ y: 2026, mo: 11, d: 1, h: 1, mi: 30, s: 0 }, "America/New_York")).toEqual({
      ms: Date.parse("2026-11-01T05:30:00Z"),
      shifted: false,
    });
  });

  it("refuses past, too-soon, too-far, impossible and unreadable times", () => {
    expect(() => resolveWhen("2026-11-12T14:00", NY, NOW)).toThrow(/in the past/);
    expect(() => resolveWhen("2026-11-12T14:04", NY, NOW)).toThrow(/less than 2 minutes/);
    expect(resolveWhen("2026-11-12T14:06", NY, NOW).local).toContain("2:06 PM");
    expect(() => resolveWhen(new Date(NOW + MAX_AHEAD_MS + 60_000).toISOString(), NY, NOW)).toThrow(/120 days/);
    expect(() => resolveWhen(new Date(NOW + MAX_AHEAD_MS - 60_000).toISOString(), NY, NOW)).not.toThrow();
    expect(() => resolveWhen("2026-02-30T09:00", NY, NOW)).toThrow(/not a real date/);
    expect(() => resolveWhen("next thursday", NY, NOW)).toThrow(/Could not read/);
    expect(() => resolveWhen("2026-11-19", NY, NOW)).toThrow(/Could not read/);
  });

  it("gives the model the current local time, zone and thread people", () => {
    const ctx = reminderContext(NY, NOW, new Map([[JORDAN, "Jordan Lee"]]));
    expect(ctx).toContain("Now: Thu 2026-11-12 14:03 America/New_York (UTC-05:00)");
    expect(ctx).toContain("Jordan Lee = UJORDAN1");
    expect(reminderContext({ tz: "America/New_York", assumed: true }, NOW)).toContain("assumed");
  });

  it("appends the context as a separate block on the last user turn", () => {
    const out = withContextNote([{ role: "user" as const, content: "remind me" }], "NOTE");
    expect(out).toEqual([{ role: "user", content: [{ type: "text", text: "remind me" }, { type: "text", text: "NOTE" }] }]);
  });
});

// ---------------------------------------------------------------------------------------------- text

describe("reminder text", () => {
  it("strips broadcasts and user-group mentions", () => {
    const r = stripBroadcasts("<!channel> hey <!here|here> and <!subteam^S0123|@leads> @here @everyone look at this, not a@here.com");
    expect(r.text).toBe("hey and here everyone look at this, not a@here.com");
    expect(r.removed).toEqual(["<!channel>", "<!here|here>", "<!subteam^S0123|@leads>", "@here", "@everyone"]);
  });

  it("round-trips ownership through the prefix and ignores look-alikes", () => {
    const text = buildReminderText(ME, [JORDAN], "send the deck");
    expect(text).toBe(":alarm_clock: Reminder from <@USAM0001>: <@UJORDAN1> send the deck");
    expect(parseReminderText(text)).toEqual({ owner: ME, body: "<@UJORDAN1> send the deck" });
    expect(parseReminderText("hello :alarm_clock: Reminder from <@USAM0001>: x")).toBeUndefined();
    expect(parseReminderText("Lunch!")).toBeUndefined();
    expect(parseReminderText(undefined)).toBeUndefined();
  });
});

describe("parseReminderConfig", () => {
  it("defaults to on, 5 per person, New York", () => {
    expect(parseReminderConfig({})).toEqual({ enabled: true, maxPerUser: 5, defaultTimeZone: "America/New_York" });
    expect(parseReminderConfig({ ELI_REMINDERS: "off", ELI_MAX_REMINDERS_PER_USER: "2", ELI_DEFAULT_TIME_ZONE: "Europe/London" })).toEqual({
      enabled: false,
      maxPerUser: 2,
      defaultTimeZone: "Europe/London",
    });
    expect(() => parseReminderConfig({ ELI_DEFAULT_TIME_ZONE: "Mars/Olympus" })).toThrow(/IANA/);
    expect(() => parseReminderConfig({ ELI_REMINDERS: "maybe" })).toThrow(/ELI_REMINDERS/);
  });

  it("has a prompt section covering confirmation, local time and calendars", () => {
    expect(REMINDERS_PROMPT).toMatch(/presses \*\*Schedule\*\*/);
    expect(REMINDERS_PROMPT).toMatch(/resolved local time/);
    expect(REMINDERS_PROMPT).toMatch(/calendar events/);
  });
});

// ---------------------------------------------------------------------------------------------- schedule tool

describe("slack_schedule_reminder", () => {
  it("posts a preview with buttons, schedules nothing, and pings nobody in the preview", async () => {
    const web = fakeWeb();
    const { call, store } = tools(web, { names: new Map([[JORDAN, "Jordan Lee"]]) });
    const out = await call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [JORDAN] }));
    expect(out).toMatchObject({ status: "awaiting_confirmation", resolved_local_time: "Thu, Nov 19, 2026, 9:00 AM EST", posts_in: "this thread" });
    expect(web.calls.some((c) => c.method === "chat.scheduleMessage")).toBe(false);

    const post = web.calls.find((c) => c.method === "chat.postMessage")!;
    expect(post.args).toMatchObject({ channel: "C1", thread_ts: "1.0" });
    const json = JSON.stringify(post.args);
    expect(json).toContain(SCHEDULE_ACTION);
    expect(json).toContain(CANCEL_ACTION);
    expect(json).not.toMatch(/<@U/); // names, not mentions
    expect(json).toContain("@Jordan Lee send the Acme deck");

    const pending = store.get(out.pending_id)!;
    expect(pending.text).toBe(":alarm_clock: Reminder from <@USAM0001>: <@UJORDAN1> send the Acme deck");
    expect(pending.previewTs).toBe("9.0001");
  });

  it("only lets the requester and conversation members be pinged, checking every members page", async () => {
    const web = fakeWeb({ members: ["UA1", "UA2", "UA3", ME, JORDAN] });
    const { call } = tools(web);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [JORDAN] }))).resolves.toMatchObject({ status: "awaiting_confirmation" });
    expect(web.calls.filter((c) => c.method === "conversations.members").length).toBe(3);

    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [OUTSIDER] }))).rejects.toThrow(/Not a member: UOUTSIDE1/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ message: `hey <@${OUTSIDER}>` }))).rejects.toThrow(/Not a member/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [BOT] }))).rejects.toThrow(/can't mention me/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: ["@sam"] }))).rejects.toThrow(/Not Slack user ids/);
  });

  it("in a DM allows only the requester, without asking Slack for members", async () => {
    const web = fakeWeb();
    const { call } = tools(web, { channel: "D123" });
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [JORDAN] }))).rejects.toThrow(/DM/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [ME] }))).resolves.toMatchObject({ status: "awaiting_confirmation" });
    expect(web.calls.some((c) => c.method === "conversations.members")).toBe(false);
  });

  it("fails closed when members cannot be read", async () => {
    const web = fakeWeb({ failMembers: "missing_scope" });
    const { call } = tools(web);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [JORDAN] }))).rejects.toThrow(/missing a permission/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput())).resolves.toMatchObject({ status: "awaiting_confirmation" });
  });

  it("strips broadcasts, caps length, and validates time", async () => {
    const web = fakeWeb();
    const { call, store } = tools(web);
    const out = await call(SCHEDULE_REMINDER_TOOL, reminderInput({ message: "<!channel> @here standup" }));
    expect(out.removed_broadcasts).toEqual(["<!channel>", "@here"]);
    expect(store.get(out.pending_id)!.text).toBe(":alarm_clock: Reminder from <@USAM0001>: here standup");
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ message: "<!everyone>" }))).rejects.toThrow(/empty/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ message: "x".repeat(1001) }))).rejects.toThrow(/1001 characters/);
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput({ when: "2026-11-12T14:04" }))).rejects.toThrow(ToolError);
  });

  it("drops a duplicate 'Reminder:' lead-in, since the prefix already says it", async () => {
    const { call, store } = tools(fakeWeb());
    const out = await call(SCHEDULE_REMINDER_TOOL, reminderInput({ message: "Reminder: send the Acme deck" }));
    expect(store.get(out.pending_id)!.text).toBe(":alarm_clock: Reminder from <@USAM0001>: send the Acme deck");
  });

  it("caps reminders per person, counting scheduled ones in every channel plus waiting previews", async () => {
    const mine = (id: string, channel: string): Scheduled => ({ id, channel_id: channel, post_at: NOW / 1000 + 9999, text: buildReminderText(ME, [], "x") });
    const theirs: Scheduled = { id: "QX", channel_id: "C1", post_at: NOW / 1000 + 9999, text: buildReminderText(JORDAN, [], "x") };
    const web = fakeWeb({ scheduled: [mine("Q1", "C1"), mine("Q2", "C2"), theirs, mine("Q3", "C3")] });
    const { call } = tools(web, { maxPerUser: 4 });
    await call(SCHEDULE_REMINDER_TOOL, reminderInput()); // 3 scheduled + this preview = 4
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput())).rejects.toThrow(/already has 4 .* limit is 4/);
  });

  it("limits previews per request", async () => {
    const web = fakeWeb();
    const { call } = tools(web, { maxPerUser: 50 });
    for (let i = 0; i < 3; i++) await call(SCHEDULE_REMINDER_TOOL, reminderInput());
    await expect(call(SCHEDULE_REMINDER_TOOL, reminderInput())).rejects.toThrow(/several previews/);
  });
});

// ---------------------------------------------------------------------------------------------- list + cancel

describe("slack_list_reminders and slack_cancel_reminder", () => {
  const seed = (): Scheduled[] => [
    { id: "Q1", channel_id: "C1", post_at: Date.parse("2026-11-19T14:00:00Z") / 1000, text: buildReminderText(ME, [JORDAN], "deck") },
    { id: "Q2", channel_id: "C1", post_at: Date.parse("2026-11-20T14:00:00Z") / 1000, text: buildReminderText(JORDAN, [], "theirs") },
    { id: "Q3", channel_id: "C2", post_at: Date.parse("2026-11-20T14:00:00Z") / 1000, text: buildReminderText(ME, [], "other channel") },
    { id: "Q4", channel_id: "C1", post_at: Date.parse("2026-11-21T14:00:00Z") / 1000, text: "not an Eli reminder" },
  ];

  it("lists only the requester's own reminders in this conversation, after a restart too", async () => {
    const web = fakeWeb({ scheduled: seed() });
    const { call } = tools(web, { names: new Map([[JORDAN, "Jordan Lee"]]) }); // fresh store = restarted Eli
    const out = await call(LIST_REMINDERS_TOOL, {});
    expect(out.scheduled).toEqual([
      { id: "Q1", local_time: "Thu, Nov 19, 2026, 9:00 AM EST", iso: "2026-11-19T09:00:00-05:00", message: "@Jordan Lee deck" },
    ]);
    expect(out.awaiting_confirmation).toEqual([]);
  });

  it("shows previews awaiting confirmation and can cancel them", async () => {
    const web = fakeWeb();
    const { call } = tools(web);
    const { pending_id } = await call(SCHEDULE_REMINDER_TOOL, reminderInput());
    const out = await call(LIST_REMINDERS_TOOL, {});
    expect(out.awaiting_confirmation).toEqual([{ id: pending_id, local_time: "Thu, Nov 19, 2026, 9:00 AM EST", message: expect.stringContaining("send the Acme deck") }]);
    await expect(call(CANCEL_REMINDER_TOOL, { id: pending_id })).resolves.toMatchObject({ was: "awaiting_confirmation" });
    expect(web.calls.find((c) => c.method === "chat.update")!.args).toMatchObject({ ts: "9.0001", text: expect.stringContaining("Cancelled") });
    expect((await call(LIST_REMINDERS_TOOL, {})).awaiting_confirmation).toEqual([]);
  });

  it("cancels only the requester's own scheduled reminders in this conversation", async () => {
    const web = fakeWeb({ scheduled: seed() });
    const { call } = tools(web);
    await expect(call(CANCEL_REMINDER_TOOL, { id: "Q2" })).rejects.toThrow(/No reminder of the requester's/); // someone else's
    await expect(call(CANCEL_REMINDER_TOOL, { id: "Q3" })).rejects.toThrow(/No reminder/); // another channel
    await expect(call(CANCEL_REMINDER_TOOL, { id: "Q4" })).rejects.toThrow(/No reminder/); // not a reminder
    await expect(call(CANCEL_REMINDER_TOOL, { id: "Q1" })).resolves.toMatchObject({ cancelled: "Q1", was: "scheduled" });
    const deletes = web.calls.filter((c) => c.method === "chat.deleteScheduledMessage");
    expect(deletes.map((d) => d.args)).toEqual([{ channel: "C1", scheduled_message_id: "Q1" }]);
    expect(web.scheduled.map((s) => s.id)).toEqual(["Q2", "Q3", "Q4"]);
  });

  it("does not cancel someone else's preview", async () => {
    const web = fakeWeb();
    const mineTools = tools(web);
    const { pending_id } = await mineTools.call(SCHEDULE_REMINDER_TOOL, reminderInput());
    const jordan = reminderTools({
      client: web.client, channel: "C1", threadTs: "1.0", requester: JORDAN, botUserId: BOT, zone: NY, store: mineTools.store, maxPerUser: 5, now: () => NOW,
    }) as unknown as Runnable[];
    await expect(jordan.find((t) => t.name === CANCEL_REMINDER_TOOL)!.run({ id: pending_id })).rejects.toThrow(/No reminder/);
  });
});

// ---------------------------------------------------------------------------------------------- buttons

describe("Schedule / Cancel buttons", () => {
  async function preview(over: Parameters<typeof fakeWeb>[0] = {}) {
    const web = fakeWeb(over);
    const t = tools(web);
    const { pending_id } = await t.call(SCHEDULE_REMINDER_TOOL, reminderInput({ mention_user_ids: [JORDAN] }));
    const click = (user: string, action = SCHEDULE_ACTION): ReminderClick => ({
      team: { id: "T1" },
      user: { id: user },
      channel: { id: "C1" },
      message: { ts: "9.0001", thread_ts: "1.0" },
      actions: [{ action_id: action, value: `${pending_id}|${ME}` }],
    });
    const deps = { maxPerUser: 5, now: () => t.advance(0), log: () => undefined };
    return { web, ...t, pending_id, click, deps };
  }

  it("schedules when the requester confirms, in the thread, with the owner prefix, and updates the preview", async () => {
    const p = await preview();
    await handleReminderClick(p.web.client, p.click(ME), p.store, p.deps);
    const sched = p.web.calls.find((c) => c.method === "chat.scheduleMessage")!;
    expect(sched.args).toMatchObject({
      channel: "C1",
      thread_ts: "1.0",
      post_at: Date.parse("2026-11-19T14:00:00Z") / 1000,
      text: ":alarm_clock: Reminder from <@USAM0001>: <@UJORDAN1> send the Acme deck",
      parse: "none",
      link_names: false,
    });
    expect(sched.args).not.toHaveProperty("metadata");
    expect(p.store.get(p.pending_id)).toMatchObject({ status: "scheduled", scheduledId: "Q0001" });
    const upd = p.web.calls.find((c) => c.method === "chat.update")!;
    expect(upd.args).toMatchObject({ channel: "C1", ts: "9.0001", text: expect.stringContaining("Scheduled for Thu, Nov 19, 2026, 9:00 AM EST") });
    expect(JSON.stringify(upd.args)).not.toContain(SCHEDULE_ACTION); // buttons gone
  });

  it("tells anyone else only the requester can confirm, and does nothing", async () => {
    const p = await preview();
    await handleReminderClick(p.web.client, p.click(JORDAN), p.store, p.deps);
    await handleReminderClick(p.web.client, p.click(JORDAN, CANCEL_ACTION), p.store, p.deps);
    const eph = p.web.calls.filter((c) => c.method === "chat.postEphemeral");
    expect(eph.map((e) => e.args)).toEqual([
      { channel: "C1", user: JORDAN, thread_ts: "1.0", text: "Only <@USAM0001> can confirm or cancel this reminder." },
      { channel: "C1", user: JORDAN, thread_ts: "1.0", text: "Only <@USAM0001> can confirm or cancel this reminder." },
    ]);
    expect(p.web.calls.some((c) => c.method === "chat.scheduleMessage" || c.method === "chat.update")).toBe(false);
    expect(p.store.get(p.pending_id)!.status).toBe("pending");
  });

  it("is idempotent: a double click (even concurrent) schedules once", async () => {
    const p = await preview();
    await Promise.all([
      handleReminderClick(p.web.client, p.click(ME), p.store, p.deps),
      handleReminderClick(p.web.client, p.click(ME), p.store, p.deps),
    ]);
    await handleReminderClick(p.web.client, p.click(ME), p.store, p.deps);
    await handleReminderClick(p.web.client, p.click(ME, CANCEL_ACTION), p.store, p.deps);
    expect(p.web.calls.filter((c) => c.method === "chat.scheduleMessage").length).toBe(1);
    expect(p.web.calls.filter((c) => c.method === "chat.update").length).toBe(1);
    expect(p.web.scheduled.length).toBe(1);
  });

  it("cancel discards it and updates the preview once", async () => {
    const p = await preview();
    await handleReminderClick(p.web.client, p.click(ME, CANCEL_ACTION), p.store, p.deps);
    await handleReminderClick(p.web.client, p.click(ME, CANCEL_ACTION), p.store, p.deps);
    await handleReminderClick(p.web.client, p.click(ME), p.store, p.deps);
    expect(p.web.calls.some((c) => c.method === "chat.scheduleMessage")).toBe(false);
    const upd = p.web.calls.filter((c) => c.method === "chat.update");
    expect(upd.length).toBe(1);
    expect(upd[0]!.args.text).toContain("Cancelled");
  });

  it("says so when the preview has expired, or is unknown after a restart", async () => {
    const p = await preview();
    p.advance(15 * 60 * 1000 + 1);
    await handleReminderClick(p.web.client, p.click(ME), p.store, p.deps);
    await handleReminderClick(p.web.client, p.click(ME), p.store, p.deps);
    expect(p.web.calls.some((c) => c.method === "chat.scheduleMessage")).toBe(false);
    const upd = p.web.calls.filter((c) => c.method === "chat.update");
    expect(upd.length).toBe(1);
    expect(upd[0]!.args.text).toContain("expired");

    const restarted = new ReminderStore();
    await handleReminderClick(p.web.client, p.click(ME), restarted, p.deps);
    expect(p.web.calls.filter((c) => c.method === "chat.update").at(-1)!.args.text).toContain("expired");
    // After a restart, the owner encoded in the button still gates other people.
    await handleReminderClick(p.web.client, p.click(JORDAN), restarted, p.deps);
    expect(p.web.calls.at(-1)!).toMatchObject({ method: "chat.postEphemeral", args: { text: "Only <@USAM0001> can confirm or cancel this reminder." } });
  });

  it("re-checks the cap and the time at confirm, and reports Slack errors without retrying", async () => {
    const capped = await preview();
    capped.web.scheduled.push(
      ...Array.from({ length: 5 }, (_, i) => ({ id: `QQ${i}`, channel_id: `C${i + 5}`, post_at: NOW / 1000 + 9999, text: buildReminderText(ME, [], "x") })),
    );
    await handleReminderClick(capped.web.client, capped.click(ME), capped.store, capped.deps);
    expect(capped.web.calls.some((c) => c.method === "chat.scheduleMessage")).toBe(false);
    expect(capped.web.calls.find((c) => c.method === "chat.update")!.args.text).toMatch(/already have 5 scheduled reminders/);

    const late = await preview();
    late.setNow(Date.parse("2026-11-19T13:59:50Z")); // well past the TTL too, so give the store a long one
    const longStore = late.store;
    (longStore as unknown as { ttlMs: number }).ttlMs = 30 * 24 * 60 * 60 * 1000;
    await handleReminderClick(late.web.client, late.click(ME), longStore, late.deps);
    expect(late.web.calls.find((c) => c.method === "chat.update")!.args.text).toMatch(/already passed/);

    const failing = await preview({ failSchedule: "restricted_too_many" });
    await handleReminderClick(failing.web.client, failing.click(ME), failing.store, failing.deps);
    await handleReminderClick(failing.web.client, failing.click(ME), failing.store, failing.deps);
    expect(failing.web.calls.filter((c) => c.method === "chat.scheduleMessage").length).toBe(1);
    expect(failing.store.get(failing.pending_id)!.status).toBe("failed");
    expect(failing.web.calls.find((c) => c.method === "chat.update")!.args.text).toMatch(/Too many messages/);
  });

  it("registers both buttons with Bolt and acks before working", async () => {
    const handlers = new Map<string, (args: unknown) => Promise<void>>();
    const app = { action: vi.fn((id: string, fn: (args: unknown) => Promise<void>) => handlers.set(id, fn)) };
    const p = await preview();
    registerReminderActions(app as never, p.store, p.deps);
    expect([...handlers.keys()]).toEqual([SCHEDULE_ACTION, CANCEL_ACTION]);
    const ack = vi.fn(async () => undefined);
    await handlers.get(SCHEDULE_ACTION)!({ ack, body: { type: "block_actions", ...p.click(ME) }, client: p.web.client });
    expect(ack).toHaveBeenCalledTimes(1);
    expect(p.store.get(p.pending_id)!.status).toBe("scheduled");
  });

  it("ignores clicks from another workspace", async () => {
    const p = await preview();
    await handleReminderClick(p.web.client, { ...p.click(ME), team: { id: "T999" } }, p.store, { ...p.deps, allowedTeamId: "T1" });
    expect(p.store.get(p.pending_id)!.status).toBe("pending");
  });
});

// ---------------------------------------------------------------------------------------------- per-request wiring

describe("reminderRequest", () => {
  it("looks up the requester's zone, builds the tools and a context line; nothing when off", async () => {
    const web = fakeWeb({ tz: "America/Los_Angeles" });
    const store = new ReminderStore();
    const config = { enabled: true, maxPerUser: 5, defaultTimeZone: "America/New_York" };
    const base = { client: web.client, channel: "C1", threadTs: "1.0", requester: "ULATZ0001", botUserId: BOT, store, now: () => NOW };
    const on = await reminderRequest({ ...base, config, names: new Map([[BOT, "Eli"], [JORDAN, "Jordan Lee"]]) });
    expect(on.tools.map((t) => t.name)).toEqual([SCHEDULE_REMINDER_TOOL, LIST_REMINDERS_TOOL, CANCEL_REMINDER_TOOL]);
    expect(on.context).toContain("Now: Thu 2026-11-12 11:03 America/Los_Angeles (UTC-08:00)");
    expect(on.context).toContain("Jordan Lee = UJORDAN1");
    expect(on.context).not.toContain(BOT);
    expect(await reminderRequest({ ...base, config: { ...config, enabled: false } })).toEqual({ tools: [] });
  });
});
