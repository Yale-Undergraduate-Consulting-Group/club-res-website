import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import type { IdentityProvider, LinkResult, Member, SlackProfileReader } from "./types.js";

/**
 * Test-workspace identity: a roster file you edit, plus a links file Eli writes.
 *
 * Linking trusts the email on the person's Slack profile (Slack verifies it at sign-up) and requires that
 * email to be on the roster. Guests, bots and deactivated accounts are refused. The roster is re-read when
 * it changes, so removing someone from it revokes them at once without touching the links file.
 */

const memberSchema = z.object({
  id: z.string().min(1).max(100),
  email: z.string().email(),
  name: z.string().max(200).optional(),
  role: z.enum(["member", "board", "admin"]).default("member"),
  projects: z.array(z.string().max(200)).max(100).default([]),
});
const rosterSchema = z.object({ members: z.array(memberSchema).max(5000) });

const linkSchema = z.object({ memberId: z.string(), email: z.string(), linkedAt: z.string() });
const linksSchema = z.record(z.string(), linkSchema);
type Links = z.infer<typeof linksSchema>;
type RosterMember = z.infer<typeof memberSchema>;

export interface LocalIdentityOptions {
  rosterPath: string;
  linksPath: string;
  /** Refuse Slack guest accounts (default true). */
  refuseGuests?: boolean;
  now?: () => Date;
  log?: (line: string) => void;
}

const key = (team: string, user: string) => `${team}:${user}`;
const norm = (email: string) => email.trim().toLowerCase();

export class LocalIdentityProvider implements IdentityProvider {
  readonly kind = "local" as const;
  private roster?: { mtimeMs: number; byEmail: Map<string, RosterMember>; byId: Map<string, RosterMember> };
  private links?: Links;
  private writing: Promise<void> = Promise.resolve();
  private readonly log: (line: string) => void;

  constructor(private readonly opts: LocalIdentityOptions) {
    this.log = opts.log ?? ((line) => console.error(line));
  }

  describe(): string {
    return `local roster ${this.opts.rosterPath}, links in ${this.opts.linksPath}`;
  }

  async resolve(teamId: string, slackUserId: string): Promise<Member | null> {
    try {
      const link = (await this.readLinks())[key(teamId, slackUserId)];
      if (!link) return null;
      const roster = await this.readRoster();
      const m = roster.byId.get(link.memberId);
      // Off the roster, or the roster email changed since linking: treat as unlinked.
      if (!m || norm(m.email) !== norm(link.email)) return null;
      return toMember(m);
    } catch (err) {
      this.log(`[eli] identity lookup failed: ${(err as Error).message}`.slice(0, 500));
      return null;
    }
  }

  async link(teamId: string, slackUserId: string, slack: SlackProfileReader): Promise<LinkResult> {
    let roster;
    try {
      roster = await this.readRoster();
    } catch (err) {
      this.log(`[eli] cannot read roster: ${(err as Error).message}`.slice(0, 500));
      return { ok: false, reason: "unavailable", message: "Linking isn't available right now (Eli can't read its member list). Tell Eli's maintainer." };
    }

    let user;
    try {
      user = (await slack.users.info({ user: slackUserId })).user;
    } catch (err) {
      const code = (err as { data?: { error?: string } }).data?.error;
      this.log(`[eli] users.info failed during linking: ${code ?? (err as Error).message}`);
      return { ok: false, reason: "unavailable", message: "I couldn't read your Slack profile just now. Try again in a minute." };
    }
    if (!user || user.is_bot || user.deleted) return { ok: false, reason: "no_email", message: "This account can't be linked." };
    if ((this.opts.refuseGuests ?? true) && (user.is_restricted || user.is_ultra_restricted)) {
      return { ok: false, reason: "not_on_roster", message: "Guest accounts can't link to a club member." };
    }
    const email = user.profile?.email;
    if (!email) {
      return {
        ok: false,
        reason: "no_email",
        message: "I can't see the email on your Slack profile. Eli's maintainer needs to add the `users:read.email` scope and reinstall the app.",
      };
    }
    const m = roster.byEmail.get(norm(email));
    if (!m) {
      return { ok: false, reason: "not_on_roster", message: `Your Slack email isn't on Eli's member list. Ask Eli's maintainer to add it.` };
    }

    const k = key(teamId, slackUserId);
    const links = await this.readLinks();
    const existing = links[k];
    if (existing && existing.memberId === m.id && norm(existing.email) === norm(m.email)) {
      return { ok: true, member: toMember(m), alreadyLinked: true };
    }
    await this.update((l) => {
      // One Slack account per member per workspace: a re-link from a new Slack account replaces the old one.
      for (const [other, v] of Object.entries(l)) if (v.memberId === m.id && other.startsWith(`${teamId}:`)) delete l[other];
      l[k] = { memberId: m.id, email: norm(m.email), linkedAt: (this.opts.now?.() ?? new Date()).toISOString() };
    });
    this.log(`[eli] linked ${slackUserId} in ${teamId} to member ${m.id}`);
    return { ok: true, member: toMember(m), alreadyLinked: false };
  }

  async unlink(teamId: string, slackUserId: string): Promise<boolean> {
    const k = key(teamId, slackUserId);
    if (!(await this.readLinks())[k]) return false;
    await this.update((l) => {
      delete l[k];
    });
    this.log(`[eli] unlinked ${slackUserId} in ${teamId}`);
    return true;
  }

  private async readRoster() {
    const stat = await fs.stat(this.opts.rosterPath);
    if (this.roster && this.roster.mtimeMs === stat.mtimeMs) return this.roster;
    const raw = JSON.parse(await fs.readFile(this.opts.rosterPath, "utf8"));
    const parsed = rosterSchema.safeParse(raw);
    if (!parsed.success) {
      throw new Error(`${this.opts.rosterPath} is not a valid roster: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`);
    }
    const byEmail = new Map<string, RosterMember>();
    const byId = new Map<string, RosterMember>();
    for (const m of parsed.data.members) {
      if (byId.has(m.id)) throw new Error(`${this.opts.rosterPath}: duplicate member id "${m.id}"`);
      if (byEmail.has(norm(m.email))) throw new Error(`${this.opts.rosterPath}: duplicate email for member "${m.id}"`);
      byId.set(m.id, m);
      byEmail.set(norm(m.email), m);
    }
    this.roster = { mtimeMs: stat.mtimeMs, byEmail, byId };
    return this.roster;
  }

  private async readLinks(): Promise<Links> {
    if (this.links) return this.links;
    try {
      const parsed = linksSchema.safeParse(JSON.parse(await fs.readFile(this.opts.linksPath, "utf8")));
      if (!parsed.success) throw new Error(`${this.opts.linksPath} is corrupt; fix or delete it`);
      this.links = parsed.data;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      this.links = {};
    }
    return this.links;
  }

  /** Serialized read-modify-write; the file is replaced atomically and readable only by its owner. */
  private update(change: (links: Links) => void): Promise<void> {
    const run = async () => {
      const links = { ...(await this.readLinks()) };
      change(links);
      const tmp = `${this.opts.linksPath}.${process.pid}.tmp`;
      await fs.mkdir(path.dirname(path.resolve(this.opts.linksPath)), { recursive: true });
      await fs.writeFile(tmp, JSON.stringify(links, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
      await fs.rename(tmp, this.opts.linksPath);
      this.links = links;
    };
    this.writing = this.writing.then(run, run);
    return this.writing;
  }
}

function toMember(m: RosterMember): Member {
  return { id: m.id, email: m.email, name: m.name, role: m.role, projects: m.projects, source: "local" };
}
