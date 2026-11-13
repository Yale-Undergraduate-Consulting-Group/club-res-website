import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { z } from "zod";
import { cleanEnv, parseSharePointConfig, type SharePointConfig } from "../config.js";
import { parseReminderConfig, type ReminderConfig } from "./reminders.js";
import { parseAdminIds, parseUserIds } from "./statsCommand.js";
import { parseWebSearchConfig, type WebSearchConfig } from "./webSearch.js";
import { parseIdentityConfig, type IdentityConfig } from "./identity/index.js";

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export interface EliConfig {
  slack: { botToken: string; appToken: string; allowedTeamId?: string };
  model: string;
  effort: Effort;
  maxTokens: number;
  maxIterations: number;
  limits: { userPerHour: number; workspacePerDay: number };
  usageLogPath: string;
  /** Undefined when ELI_SHAREPOINT=off. */
  sharepoint?: SharePointConfig;
  /** Undefined when no Google service-account key is configured. */
  gdrive?: { keyFile: string; maxDownloadBytes: number };
  /** Web search; `enabled` is false unless ELI_WEB_SEARCH=on. */
  webSearch: WebSearchConfig;
  /** Slack user ids allowed to run /eli-stats. Empty means nobody. */
  adminUserIds: ReadonlySet<string>;
  reminders: ReminderConfig;
  /** Who /requests pings: ELI_FEATURE_REQUESTS_TO, else the admins. Empty disables /requests. */
  requestRecipients: ReadonlySet<string>;
  /** View-only access to the web console (ELI_BOARD_USER_IDS). Admins can always view it too. */
  boardUserIds: ReadonlySet<string>;
  /** The board's web console; undefined unless ELI_CONSOLE=on. */
  console?: ConsoleConfig;
  /** Slack-to-member linking (ELI_IDENTITY); mode "off" by default. */
  identity: IdentityConfig;
}

export interface ConsoleConfig {
  port: number;
  host: string;
  publicUrl: string;
  secret: string;
  timeZone: string;
}

const consoleSchema = z.object({
  ELI_CONSOLE: z.enum(["on", "off"]).default("off"),
  ELI_CONSOLE_PORT: z.coerce.number().int().min(1).max(65535).default(8787),
  ELI_CONSOLE_HOST: z.string().default("127.0.0.1"),
  ELI_CONSOLE_URL: z.string().url("must be a full URL such as https://eli-console.example.org").optional(),
  ELI_CONSOLE_SECRET: z.string().min(32, "must be at least 32 characters (openssl rand -hex 32)").optional(),
  ELI_CONSOLE_TIME_ZONE: z.string().default("America/New_York"),
});

/** Parse the console settings, pushing every problem onto `problems`. */
function parseConsole(env: NodeJS.ProcessEnv, problems: string[]): ConsoleConfig | undefined {
  const parsed = consoleSchema.safeParse(cleanEnv(env));
  if (!parsed.success) {
    problems.push(...parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`));
    return undefined;
  }
  const c = parsed.data;
  if (c.ELI_CONSOLE === "off") return undefined;
  if (!c.ELI_CONSOLE_SECRET) problems.push("ELI_CONSOLE_SECRET is required when ELI_CONSOLE=on (generate one with: openssl rand -hex 32)");
  const publicUrl = (c.ELI_CONSOLE_URL ?? `http://localhost:${c.ELI_CONSOLE_PORT}`).replace(/\/+$/, "");
  const local = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(publicUrl);
  if (!publicUrl.startsWith("https://") && !local) problems.push("ELI_CONSOLE_URL must use https:// unless it is localhost");
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: c.ELI_CONSOLE_TIME_ZONE });
  } catch {
    problems.push(`ELI_CONSOLE_TIME_ZONE: "${c.ELI_CONSOLE_TIME_ZONE}" is not a time zone (e.g. America/New_York)`);
  }
  return { port: c.ELI_CONSOLE_PORT, host: c.ELI_CONSOLE_HOST, publicUrl, secret: c.ELI_CONSOLE_SECRET ?? "", timeZone: c.ELI_CONSOLE_TIME_ZONE };
}

const envSchema = z.object({
  SLACK_BOT_TOKEN: z.string().startsWith("xoxb-", "must be the bot token (starts with xoxb-)").optional(),
  SLACK_APP_TOKEN: z.string().startsWith("xapp-", "must be the app-level token (starts with xapp-)").optional(),
  ANTHROPIC_API_KEY: z.string().optional(),
  ANTHROPIC_AUTH_TOKEN: z.string().optional(),
  ANTHROPIC_PROFILE: z.string().optional(),
  ELI_MODEL: z.string().default("claude-haiku-5-5"),
  ELI_EFFORT: z.enum(["low", "medium", "high", "xhigh", "max"]).default("medium"),
  ELI_MAX_TOKENS: z.coerce.number().int().min(1000).max(64000).default(8000),
  ELI_MAX_TOOL_STEPS: z.coerce.number().int().min(1).max(30).default(10),
  ELI_ALLOWED_TEAM_ID: z.string().regex(/^[TE][A-Z0-9]+$/, "must be a Slack team id such as T0123ABCD").optional(),
  ELI_USER_REQUESTS_PER_HOUR: z.coerce.number().int().min(1).default(20),
  ELI_WORKSPACE_REQUESTS_PER_DAY: z.coerce.number().int().min(1).default(300),
  ELI_USAGE_LOG: z.string().default("eli-usage.log"),
  ELI_SHAREPOINT: z.enum(["on", "off"]).default("on"),
  ELI_GOOGLE_KEY_FILE: z.string().optional(),
  MAX_DOWNLOAD_BYTES: z.coerce.number().int().positive().default(25 * 1024 * 1024),
});

/**
 * True when `ant auth login` has saved a Console sign-in the Anthropic SDK can use instead of an API key
 * (credentials under $ANTHROPIC_CONFIG_DIR, default ~/.config/anthropic). Only checks that one exists.
 */
export function hasAnthropicLogin(env: NodeJS.ProcessEnv = process.env): boolean {
  const dir = env.ANTHROPIC_CONFIG_DIR || path.join(os.homedir(), ".config", "anthropic");
  try {
    return fs.readdirSync(path.join(dir, "credentials")).some((f) => f.endsWith(".json"));
  } catch {
    return false;
  }
}

/**
 * Parse Eli's settings. `needSlack: false` is for the command-line tools, which never connect to Slack.
 * Throws one Error listing every problem. Never includes secret values in messages.
 */
export function loadEliConfig(
  env: NodeJS.ProcessEnv = process.env,
  { needSlack = true, anthropicLogin = hasAnthropicLogin }: { needSlack?: boolean; anthropicLogin?: (env: NodeJS.ProcessEnv) => boolean } = {},
): EliConfig {
  const parsed = envSchema.safeParse(cleanEnv(env));
  if (!parsed.success) {
    throw new Error(
      "Invalid Eli configuration:\n" + parsed.error.issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`).join("\n"),
    );
  }
  const e = parsed.data;
  const problems: string[] = [];
  // The SDK takes an API key, a token, or a saved `ant auth login` sign-in, in that order.
  if (!e.ANTHROPIC_API_KEY && !e.ANTHROPIC_AUTH_TOKEN && !e.ANTHROPIC_PROFILE && !anthropicLogin(env)) {
    problems.push(
      "ANTHROPIC_API_KEY is required, or sign in once with `ant auth login` (put a key only in .env; never in code or chat)",
    );
  }
  if (needSlack && !e.SLACK_BOT_TOKEN) problems.push("SLACK_BOT_TOKEN is required (Slack app → OAuth & Permissions)");
  if (needSlack && !e.SLACK_APP_TOKEN) {
    problems.push("SLACK_APP_TOKEN is required (Slack app → Basic Information → App-Level Tokens, scope connections:write)");
  }

  let sharepoint: SharePointConfig | undefined;
  if (e.ELI_SHAREPOINT === "on") {
    try {
      sharepoint = parseSharePointConfig(env);
    } catch (err) {
      problems.push((err as Error).message.replace(/^Invalid configuration:\n/, "SharePoint settings:\n"));
    }
  }

  let webSearch: WebSearchConfig | undefined;
  try {
    webSearch = parseWebSearchConfig(env);
  } catch (err) {
    problems.push((err as Error).message);
  }
  let adminUserIds: ReadonlySet<string> = new Set();
  try {
    adminUserIds = parseAdminIds(env);
  } catch (err) {
    problems.push((err as Error).message);
  }

  let requestRecipients: ReadonlySet<string> = adminUserIds;
  try {
    const raw = env.ELI_FEATURE_REQUESTS_TO?.trim();
    if (raw?.toLowerCase() === "off") requestRecipients = new Set();
    else if (raw) requestRecipients = parseUserIds("ELI_FEATURE_REQUESTS_TO", raw);
  } catch (err) {
    problems.push((err as Error).message);
  }

  let boardUserIds: ReadonlySet<string> = new Set();
  try {
    boardUserIds = parseUserIds("ELI_BOARD_USER_IDS", env.ELI_BOARD_USER_IDS);
  } catch (err) {
    problems.push((err as Error).message);
  }
  const consoleConfig = parseConsole(env, problems);

  let reminders: ReminderConfig = { enabled: false, maxPerUser: 5, defaultTimeZone: "America/New_York" };
  try {
    reminders = parseReminderConfig(env);
  } catch (err) {
    problems.push((err as Error).message);
  }

  let identity: IdentityConfig = { mode: "off" };
  try {
    identity = parseIdentityConfig(env);
  } catch (err) {
    problems.push(...(err as Error).message.split("\n"));
  }

  if (problems.length) throw new Error("Invalid Eli configuration:\n" + problems.map((p) => `  - ${p}`).join("\n"));

  return {
    slack: { botToken: e.SLACK_BOT_TOKEN ?? "", appToken: e.SLACK_APP_TOKEN ?? "", allowedTeamId: e.ELI_ALLOWED_TEAM_ID },
    model: e.ELI_MODEL,
    effort: e.ELI_EFFORT,
    maxTokens: e.ELI_MAX_TOKENS,
    maxIterations: e.ELI_MAX_TOOL_STEPS,
    limits: { userPerHour: e.ELI_USER_REQUESTS_PER_HOUR, workspacePerDay: e.ELI_WORKSPACE_REQUESTS_PER_DAY },
    usageLogPath: e.ELI_USAGE_LOG,
    sharepoint,
    gdrive: e.ELI_GOOGLE_KEY_FILE ? { keyFile: e.ELI_GOOGLE_KEY_FILE, maxDownloadBytes: e.MAX_DOWNLOAD_BYTES } : undefined,
    webSearch: webSearch!,
    adminUserIds,
    reminders,
    requestRecipients,
    boardUserIds,
    console: consoleConfig,
    identity,
  };
}
