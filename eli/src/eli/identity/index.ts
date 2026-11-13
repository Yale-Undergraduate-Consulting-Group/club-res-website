import { z } from "zod";
import { cleanEnv } from "../../config.js";
import { LocalIdentityProvider } from "./local.js";
import { PlatformIdentityProvider } from "./platform.js";
import type { IdentityProvider } from "./types.js";

export type { IdentityProvider, Member, LinkResult, SlackProfileReader } from "./types.js";
export { memberContextNote } from "./types.js";

export type IdentityConfig =
  | { mode: "off" }
  | { mode: "local"; rosterPath: string; linksPath: string; requireLink: boolean }
  | { mode: "platform"; baseUrl: string; token: string; linkPageUrl?: string; requireLink: boolean };

const schema = z.object({
  ELI_IDENTITY: z.enum(["off", "local", "platform"]).default("off"),
  ELI_REQUIRE_LINK: z.enum(["on", "off"]).default("off"),
  ELI_MEMBERS_FILE: z.string().default("eli-members.json"),
  ELI_LINKS_FILE: z.string().default("eli-links.json"),
  ELI_PLATFORM_URL: z.string().url().optional(),
  ELI_PLATFORM_TOKEN: z.string().min(32, "must be at least 32 characters").optional(),
  ELI_PLATFORM_LINK_PAGE: z.string().url().optional(),
});

/** Throws one Error listing every problem; never echoes the token. */
export function parseIdentityConfig(env: NodeJS.ProcessEnv): IdentityConfig {
  const parsed = schema.safeParse(cleanEnv(env));
  if (!parsed.success) throw new Error(parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("\n"));
  const e = parsed.data;
  const requireLink = e.ELI_REQUIRE_LINK === "on";
  if (e.ELI_IDENTITY === "off") {
    if (requireLink) throw new Error("ELI_REQUIRE_LINK=on needs ELI_IDENTITY=local or platform");
    return { mode: "off" };
  }
  if (e.ELI_IDENTITY === "local") return { mode: "local", rosterPath: e.ELI_MEMBERS_FILE, linksPath: e.ELI_LINKS_FILE, requireLink };
  const problems: string[] = [];
  if (!e.ELI_PLATFORM_URL) problems.push("ELI_PLATFORM_URL is required when ELI_IDENTITY=platform");
  else if (!/^https:\/\//.test(e.ELI_PLATFORM_URL) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/.test(e.ELI_PLATFORM_URL)) {
    problems.push("ELI_PLATFORM_URL must use https:// unless it is localhost");
  }
  if (!e.ELI_PLATFORM_TOKEN) problems.push("ELI_PLATFORM_TOKEN is required when ELI_IDENTITY=platform");
  if (problems.length) throw new Error(problems.join("\n"));
  return { mode: "platform", baseUrl: e.ELI_PLATFORM_URL!, token: e.ELI_PLATFORM_TOKEN!, linkPageUrl: e.ELI_PLATFORM_LINK_PAGE, requireLink };
}

export function createIdentityProvider(config: IdentityConfig, log?: (line: string) => void): IdentityProvider | undefined {
  switch (config.mode) {
    case "off":
      return undefined;
    case "local":
      return new LocalIdentityProvider({ rosterPath: config.rosterPath, linksPath: config.linksPath, log });
    case "platform":
      return new PlatformIdentityProvider({ baseUrl: config.baseUrl, token: config.token, linkPageUrl: config.linkPageUrl, log });
  }
}
