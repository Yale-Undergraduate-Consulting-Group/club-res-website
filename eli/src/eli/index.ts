import { App, LogLevel } from "@slack/bolt";
import { loadEliConfig } from "./config.js";
import { createBrain } from "./setup.js";
import { ReminderStore, registerReminderActions } from "./reminders.js";
import { EliSlack } from "./slack.js";
import path from "node:path";
import { consoleReplyFor, helpFor, onboardFor, registerCommands } from "./commands.js";
import { signInLink, slackNames, startConsole } from "./console/server.js";
import { registerStatsCommand } from "./statsCommand.js";
import { slackDownloader } from "./tools/slackFiles.js";
import { createIdentityProvider } from "./identity/index.js";
import { registerIdentityCommands } from "./identity/commands.js";

async function main(): Promise<void> {
  let config;
  try {
    config = loadEliConfig();
  } catch (err) {
    console.error((err as Error).message);
    process.exit(1);
  }

  const brain = createBrain(config);
  // Socket Mode: Eli connects out to Slack, so it needs no public URL or server.
  const app = new App({
    token: config.slack.botToken,
    appToken: config.slack.appToken,
    socketMode: true,
    logLevel: LogLevel.WARN,
  });

  const auth = await app.client.auth.test();
  if (!auth.user_id) throw new Error("Slack did not return Eli's bot user id; check SLACK_BOT_TOKEN.");
  if (config.slack.allowedTeamId && auth.team_id !== config.slack.allowedTeamId) {
    throw new Error(`SLACK_BOT_TOKEN belongs to workspace ${auth.team_id}, but ELI_ALLOWED_TEAM_ID is ${config.slack.allowedTeamId}.`);
  }

  const consoleViewers: ReadonlySet<string> = new Set([...config.boardUserIds, ...config.adminUserIds]);
  const consoleDeps = config.console && {
    secret: config.console.secret,
    publicUrl: config.console.publicUrl,
    viewers: consoleViewers,
    usageLogPath: config.usageLogPath,
    timeZone: config.console.timeZone,
    limits: config.limits,
    names: slackNames(app.client),
  };
  const commandDeps = {
    consoleViewers,
    consoleLink: consoleDeps ? (user: string) => signInLink(consoleDeps, user) : undefined,
    allowedTeamId: config.slack.allowedTeamId ?? auth.team_id,
    adminUserIds: config.adminUserIds,
    requestRecipients: config.requestRecipients,
    requestLogPath: path.join(path.dirname(config.usageLogPath), "eli-requests.log"),
    features: {
      webSearch: config.webSearch.enabled,
      reminders: config.reminders.enabled,
      links: Boolean(config.sharepoint || config.gdrive),
      userPerHour: config.limits.userPerHour,
    },
  };
  const reminderStore = new ReminderStore();
  const identity = createIdentityProvider(config.identity);
  const requireLink = config.identity.mode !== "off" && config.identity.requireLink;
  const eli = new EliSlack({
    identity,
    requireLink,
    botUserId: auth.user_id,
    allowedTeamId: config.slack.allowedTeamId ?? auth.team_id,
    model: config.model,
    limits: config.limits,
    usageLogPath: config.usageLogPath,
    answer: brain.answer,
    webSearch: config.webSearch,
    reminders: config.reminders.enabled ? { store: reminderStore, config: config.reminders } : undefined,
    downloadFile: slackDownloader(config.slack.botToken),
    maxDownloadBytes: config.gdrive?.maxDownloadBytes ?? config.sharepoint?.maxDownloadBytes,
    help: (user) => helpFor(user, commandDeps),
    onboard: (user) => onboardFor(user, commandDeps),
    console: (user) => consoleReplyFor(user, commandDeps),
  });
  eli.register(app);
  if (config.reminders.enabled) {
    registerReminderActions(app, reminderStore, {
      maxPerUser: config.reminders.maxPerUser,
      allowedTeamId: config.slack.allowedTeamId ?? auth.team_id,
    });
  }
  registerStatsCommand(app, {
    usageLogPath: config.usageLogPath,
    adminUserIds: config.adminUserIds,
    allowedTeamId: config.slack.allowedTeamId ?? auth.team_id,
    personalStats: (user) => eli.personalStats(user),
  });
  registerCommands(app, commandDeps);
  if (identity) registerIdentityCommands(app, { identity, allowedTeamId: config.slack.allowedTeamId ?? auth.team_id });

  await app.start();
  if (consoleDeps && config.console) await startConsole(consoleDeps, { port: config.console.port, host: config.console.host });
  console.error(
    [
      "",
      "Eli is running (Ctrl+C to stop)",
      `  Slack workspace: ${auth.team ?? "?"} (${auth.team_id}) as @${auth.user ?? "eli"}`,
      ...(await brain.describe()),
      `  member linking:  ${identity ? `${identity.describe()}${requireLink ? "; linking required" : "; optional"} (/eli-link, /eli-whoami, /eli-unlink)` : "off (ELI_IDENTITY=local or platform to enable)"}`,
      `  usage stats:     "stats" in a DM or /eli-stats for anyone's own; workspace-wide for ${config.adminUserIds.size} admin${config.adminUserIds.size === 1 ? "" : "s"}`,
      `  console:         ${config.console ? `${config.console.publicUrl} (listening on ${config.console.host}:${config.console.port}); ${consoleViewers.size} viewer${consoleViewers.size === 1 ? "" : "s"}, sign in via /eli-console or "console" in a DM` : "off (ELI_CONSOLE=on to enable)"}`,
      `  commands:        /help, /onboard ("help" / "onboard" in a DM), /requests ${config.requestRecipients.size ? `pings ${[...config.requestRecipients].join(", ")} (backup: eli-requests.log)` : "off (set ELI_FEATURE_REQUESTS_TO or ELI_ADMIN_USER_IDS)"}`,
      "",
    ].join("\n"),
  );

  let stopping = false;
  const stop = async (signal: string) => {
    if (stopping) return;
    stopping = true;
    console.error(`Received ${signal}, stopping Eli...`);
    await app.stop().catch(() => undefined);
    process.exit(0);
  };
  process.on("SIGINT", () => void stop("SIGINT"));
  process.on("SIGTERM", () => void stop("SIGTERM"));
}

main().catch((err) => {
  console.error(`Eli failed to start: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
