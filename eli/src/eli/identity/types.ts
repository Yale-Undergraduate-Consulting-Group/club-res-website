/**
 * Who a Slack user is in the club. Eli asks an IdentityProvider; it never decides membership itself.
 *
 * Two providers share this interface, so moving from the test workspace to YUCG is a config change:
 *  - "local":    a roster file you control, linked by the person's verified Slack email (test workspace).
 *  - "platform": YUCG Outreach decides, through its own Slack linking (Profile → Integrations → Connect Slack).
 */

export type MemberRole = "member" | "board" | "admin";

export interface Member {
  /** Stable id in the source system (roster id, or the platform's user id). */
  id: string;
  email: string;
  name?: string;
  role: MemberRole;
  /** Project ids or names this member is on. */
  projects: string[];
  /** Where this answer came from. */
  source: "local" | "platform";
}

export type LinkResult =
  | { ok: true; member: Member; alreadyLinked: boolean }
  | { ok: false; reason: "not_on_roster" | "no_email" | "elsewhere" | "unavailable"; message: string };

/** The Slack calls a provider may make. A subset of WebClient so tests can fake it. */
export interface SlackProfileReader {
  users: {
    info(args: { user: string }): Promise<{ user?: { is_bot?: boolean; deleted?: boolean; is_restricted?: boolean; is_ultra_restricted?: boolean; profile?: { email?: string; real_name?: string; display_name?: string } } }>;
  };
}

export interface IdentityProvider {
  readonly kind: "local" | "platform";
  /** The member behind this Slack user, or null if not linked (or no longer on the roster). Never throws. */
  resolve(teamId: string, slackUserId: string): Promise<Member | null>;
  /** Link this Slack user. For "platform" this only explains where to link (the website owns it). */
  link(teamId: string, slackUserId: string, slack: SlackProfileReader): Promise<LinkResult>;
  /** Remove the link. Returns false if there was none (or the source system owns unlinking). */
  unlink(teamId: string, slackUserId: string): Promise<boolean>;
  /** One line for the startup banner. */
  describe(): string;
}

/** A short note for Claude about who is asking. No email: the model doesn't need it. */
export function memberContextNote(m: Member): string {
  const projects = m.projects.length ? m.projects.join(", ") : "none listed";
  return `(Context from Eli, not from the user: the person asking is ${m.name ?? "a club member"}, role ${m.role}, projects: ${projects}.)`;
}
