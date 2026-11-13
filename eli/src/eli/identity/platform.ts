import { z } from "zod";
import type { IdentityProvider, LinkResult, Member } from "./types.js";

/**
 * YUCG identity: ask the YUCG Outreach platform who a Slack user is.
 *
 * PROPOSED CONTRACT — this endpoint does not exist yet; it needs Andrei's review and a platform app in
 * club-res-website. The platform already stores the link (table user_slack_tokens, written when a member
 * clicks Profile → Integrations → Connect Slack); this endpoint only reads it.
 *
 *   GET {baseUrl}/api/agent/v1/members/by-slack?team_id=T…&user_id=U…
 *   Authorization: Bearer <service token, one per agent, revocable>
 *   200 {"member":{"id":"42","email":"a@yale.edu","name":"A","role":"member","projects":["…"]}}
 *   404 when the Slack user is not linked to an active member.
 *
 * Linking and unlinking stay on the website, so this provider never writes anything.
 */

const responseSchema = z.object({
  member: z.object({
    id: z.union([z.string(), z.number()]).transform(String),
    email: z.string(),
    name: z.string().nullish(),
    role: z.enum(["member", "board", "admin"]).catch("member"),
    projects: z.array(z.string()).default([]),
  }),
});

export interface PlatformIdentityOptions {
  baseUrl: string;
  token: string;
  /** Where members link their Slack account, shown in replies. */
  linkPageUrl?: string;
  timeoutMs?: number;
  cacheMs?: number;
  fetch?: typeof fetch;
  now?: () => number;
  log?: (line: string) => void;
}

export class PlatformIdentityProvider implements IdentityProvider {
  readonly kind = "platform" as const;
  private readonly cache = new Map<string, { member: Member | null; at: number }>();
  private readonly log: (line: string) => void;

  constructor(private readonly opts: PlatformIdentityOptions) {
    this.log = opts.log ?? ((line) => console.error(line));
  }

  describe(): string {
    return `YUCG platform at ${new URL(this.opts.baseUrl).host}`;
  }

  async resolve(teamId: string, slackUserId: string): Promise<Member | null> {
    const k = `${teamId}:${slackUserId}`;
    const now = (this.opts.now ?? Date.now)();
    const hit = this.cache.get(k);
    if (hit && now - hit.at < (this.opts.cacheMs ?? 60_000)) return hit.member;

    const url = new URL("/api/agent/v1/members/by-slack", this.opts.baseUrl);
    url.searchParams.set("team_id", teamId);
    url.searchParams.set("user_id", slackUserId);
    try {
      const res = await (this.opts.fetch ?? fetch)(url, {
        headers: { authorization: `Bearer ${this.opts.token}`, accept: "application/json" },
        signal: AbortSignal.timeout(this.opts.timeoutMs ?? 5000),
        redirect: "error",
      });
      let member: Member | null = null;
      if (res.status === 200) {
        const parsed = responseSchema.safeParse(await res.json());
        if (!parsed.success) throw new Error("unexpected response shape");
        const m = parsed.data.member;
        member = { id: m.id, email: m.email, name: m.name ?? undefined, role: m.role, projects: m.projects, source: "platform" };
      } else if (res.status !== 404) {
        throw new Error(`HTTP ${res.status}`);
      }
      this.cache.set(k, { member, at: now });
      return member;
    } catch (err) {
      // Fail closed: an outage means "not linked", never someone else's identity. Errors are not cached.
      this.log(`[eli] platform identity lookup failed: ${(err as Error).message}`.slice(0, 300));
      return null;
    }
  }

  async link(teamId: string, slackUserId: string): Promise<LinkResult> {
    this.cache.delete(`${teamId}:${slackUserId}`);
    const member = await this.resolve(teamId, slackUserId);
    if (member) return { ok: true, member, alreadyLinked: true };
    const where = this.opts.linkPageUrl ? ` at ${this.opts.linkPageUrl}` : " on the YUCG Outreach website";
    return { ok: false, reason: "elsewhere", message: `Link your Slack account${where}: Profile → Integrations → Connect Slack. Then run this again.` };
  }

  async unlink(): Promise<boolean> {
    return false;
  }
}
