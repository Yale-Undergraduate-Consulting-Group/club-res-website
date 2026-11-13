import type { App } from "@slack/bolt";
import type { CommandRequest } from "../commands.js";
import { slackPayload } from "../reply.js";
import type { IdentityProvider, Member, SlackProfileReader } from "./types.js";

export const LINK_COMMAND = "/eli-link";
export const UNLINK_COMMAND = "/eli-unlink";
export const WHOAMI_COMMAND = "/eli-whoami";

export interface IdentityCommandDeps {
  identity: IdentityProvider;
  allowedTeamId?: string;
  log?: (line: string) => void;
}

type IdentityRequest = Omit<CommandRequest, "client"> & { client: SlackProfileReader };

function describeMember(m: Member): string {
  const projects = m.projects.length ? m.projects.join(", ") : "none listed";
  return `**${m.name ?? m.id}** (${m.email})\n• Role: ${m.role}\n• Projects: ${projects}`;
}

async function begin(req: IdentityRequest, deps: IdentityCommandDeps, name: string) {
  const log = deps.log ?? ((line) => console.error(line));
  try {
    await req.ack();
  } catch (err) {
    log(`[eli] could not acknowledge ${name}: ${(err as Error).message}`);
  }
  const reply = async (markdown: string) => {
    try {
      await req.respond({ response_type: "ephemeral", ...slackPayload(markdown) });
    } catch (err) {
      log(`[eli] could not answer ${name}: ${(err as Error).message}`);
    }
  };
  const team = req.command.team_id ?? "";
  const allowed = Boolean(team) && !(deps.allowedTeamId && team !== deps.allowedTeamId);
  return { reply, allowed, team, user: req.command.user_id };
}

/** `/eli-link`. Public for tests. Never throws. */
export async function handleLinkCommand(req: IdentityRequest, deps: IdentityCommandDeps): Promise<void> {
  const { reply, allowed, team, user } = await begin(req, deps, LINK_COMMAND);
  if (!allowed) return;
  try {
    const r = await deps.identity.link(team, user, req.client);
    if (!r.ok) return reply(r.message);
    return reply(`${r.alreadyLinked ? "You're already linked as" : ":white_check_mark: Linked. You're"} ${describeMember(r.member)}`);
  } catch (err) {
    (deps.log ?? console.error)(`[eli] link failed: ${(err as Error).message}`.slice(0, 500));
    return reply("Sorry, linking failed. Try again in a minute.");
  }
}

/** `/eli-unlink`. Public for tests. Never throws. */
export async function handleUnlinkCommand(req: IdentityRequest, deps: IdentityCommandDeps): Promise<void> {
  const { reply, allowed, team, user } = await begin(req, deps, UNLINK_COMMAND);
  if (!allowed) return;
  if (deps.identity.kind === "platform") {
    return reply("Your link is managed on the YUCG Outreach website: Profile → Integrations → Disconnect Slack.");
  }
  try {
    return reply((await deps.identity.unlink(team, user)) ? "Unlinked. Run `/eli-link` to link again." : "You weren't linked.");
  } catch (err) {
    (deps.log ?? console.error)(`[eli] unlink failed: ${(err as Error).message}`.slice(0, 500));
    return reply("Sorry, unlinking failed. Try again in a minute.");
  }
}

/** `/eli-whoami`. Public for tests. Never throws. */
export async function handleWhoamiCommand(req: IdentityRequest, deps: IdentityCommandDeps): Promise<void> {
  const { reply, allowed, team, user } = await begin(req, deps, WHOAMI_COMMAND);
  if (!allowed) return;
  const m = await deps.identity.resolve(team, user);
  return reply(m ? `You're linked as ${describeMember(m)}` : "You're not linked to a club member. Run `/eli-link`.");
}

export function registerIdentityCommands(app: App, deps: IdentityCommandDeps): void {
  app.command(LINK_COMMAND, async ({ command, ack, respond, client }) => {
    await handleLinkCommand({ command, ack: () => ack(), respond, client: client as unknown as SlackProfileReader }, deps);
  });
  app.command(UNLINK_COMMAND, async ({ command, ack, respond, client }) => {
    await handleUnlinkCommand({ command, ack: () => ack(), respond, client: client as unknown as SlackProfileReader }, deps);
  });
  app.command(WHOAMI_COMMAND, async ({ command, ack, respond, client }) => {
    await handleWhoamiCommand({ command, ack: () => ack(), respond, client: client as unknown as SlackProfileReader }, deps);
  });
}
