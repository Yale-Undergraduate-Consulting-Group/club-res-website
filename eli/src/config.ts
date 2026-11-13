import { z } from "zod";
import { Scope } from "./sharepoint/scope.js";

export interface GraphConfig {
  /**
   * "app" (default): client credentials; needs a tenant admin to grant the app read access.
   * "delegated": signed in as one person with `npm run eli:microsoft -- login`; reads what that person can open.
   */
  auth?: "app" | "delegated";
  tenantId: string;
  clientId: string;
  /** App mode only. */
  clientSecret?: string;
  /** Required in app mode. Optional in delegated mode, where it defaults to the person's own OneDrive. */
  siteId?: string;
  /** Delegated mode only: the MSAL token cache file. */
  tokenCachePath?: string;
  /** Optional; defaults to the site's default document library. */
  driveId?: string;
  /** Required by Graph /search/query for app-only calls (NAM, EUR, APC, ...). */
  searchRegion: string;
}

export interface SharePointConfig {
  mode: "mock" | "graph";
  /** Normalized, validated library-relative folders. Never empty. */
  allowedFolders: string[];
  maxDownloadBytes: number;
  graph?: GraphConfig;
  /** Mock mode only. Defaults to ./sample-data next to the project root. */
  mockDataDir?: string;
}

const sharePointEnvSchema = z.object({
  SHAREPOINT_MODE: z.enum(["mock", "graph"]).default("mock"),
  SHAREPOINT_ALLOWED_FOLDERS: z.string().optional(),
  MOCK_DATA_DIR: z.string().optional(),
  MAX_DOWNLOAD_BYTES: z.coerce.number().int().positive().default(25 * 1024 * 1024),
  AZURE_TENANT_ID: z.string().optional(),
  AZURE_CLIENT_ID: z.string().optional(),
  AZURE_CLIENT_SECRET: z.string().optional(),
  SHAREPOINT_SITE_ID: z.string().optional(),
  SHAREPOINT_DRIVE_ID: z.string().optional(),
  GRAPH_SEARCH_REGION: z.string().default("NAM"),
  GRAPH_AUTH: z.enum(["app", "delegated"]).default("app"),
  MSAL_CACHE_PATH: z.string().default(".msal-cache.json"),
});

type SharePointEnv = z.infer<typeof sharePointEnvSchema>;

/** `FOO=` in a .env file yields "", which should mean "unset". */
export function cleanEnv(env: NodeJS.ProcessEnv): Record<string, string> {
  return Object.fromEntries(
    Object.entries(env).filter((entry): entry is [string, string] => entry[1] !== undefined && entry[1] !== ""),
  );
}

/** Validate the SharePoint settings, pushing every problem onto `problems`. */
function sharePointFrom(e: SharePointEnv, problems: string[]): SharePointConfig {
  const folders = (e.SHAREPOINT_ALLOWED_FOLDERS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (folders.length === 0) {
    if (e.SHAREPOINT_MODE === "graph") {
      problems.push("SHAREPOINT_ALLOWED_FOLDERS is required in graph mode (fail closed: no default scope)");
    } else {
      folders.push("Past Projects");
    }
  }

  let graph: GraphConfig | undefined;
  if (e.SHAREPOINT_MODE === "graph" && e.GRAPH_AUTH === "delegated") {
    if (!e.AZURE_CLIENT_ID) problems.push("delegated graph mode requires: AZURE_CLIENT_ID");
    else {
      graph = {
        auth: "delegated",
        tenantId: e.AZURE_TENANT_ID ?? "organizations",
        clientId: e.AZURE_CLIENT_ID,
        siteId: e.SHAREPOINT_SITE_ID,
        driveId: e.SHAREPOINT_DRIVE_ID,
        searchRegion: e.GRAPH_SEARCH_REGION,
        tokenCachePath: e.MSAL_CACHE_PATH,
      };
    }
  } else if (e.SHAREPOINT_MODE === "graph") {
    const missing = (["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "SHAREPOINT_SITE_ID"] as const).filter(
      (k) => !e[k],
    );
    if (missing.length) problems.push(`graph mode requires: ${missing.join(", ")}`);
    else {
      graph = {
        tenantId: e.AZURE_TENANT_ID!,
        clientId: e.AZURE_CLIENT_ID!,
        clientSecret: e.AZURE_CLIENT_SECRET!,
        siteId: e.SHAREPOINT_SITE_ID!,
        driveId: e.SHAREPOINT_DRIVE_ID,
        searchRegion: e.GRAPH_SEARCH_REGION,
      };
    }
  }

  let allowedFolders: string[] = [];
  try {
    allowedFolders = [...new Scope(folders).roots];
  } catch (err) {
    problems.push(`SHAREPOINT_ALLOWED_FOLDERS: ${(err as Error).message}`);
  }

  return {
    mode: e.SHAREPOINT_MODE,
    allowedFolders,
    maxDownloadBytes: e.MAX_DOWNLOAD_BYTES,
    graph,
    mockDataDir: e.MOCK_DATA_DIR,
  };
}

function formatIssues(issues: readonly { path: PropertyKey[]; message: string }[]): string {
  return "Invalid configuration:\n" + issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`).join("\n");
}

function formatProblems(problems: string[]): string {
  return "Invalid configuration:\n" + problems.map((p) => `  - ${p}`).join("\n");
}

/** Parse and validate only the SharePoint settings. Throws one Error listing every problem. */
export function parseSharePointConfig(env: NodeJS.ProcessEnv = process.env): SharePointConfig {
  const parsed = sharePointEnvSchema.safeParse(cleanEnv(env));
  if (!parsed.success) throw new Error(formatIssues(parsed.error.issues));
  const problems: string[] = [];
  const sharepoint = sharePointFrom(parsed.data, problems);
  if (problems.length) throw new Error(formatProblems(problems));
  return sharepoint;
}
