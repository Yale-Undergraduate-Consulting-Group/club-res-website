import path from "node:path";
import type { SharePointConfig } from "../config.js";
import { DelegatedAuth } from "./delegatedAuth.js";
import { GraphSharePointClient } from "./graph.js";
import { MockSharePointClient } from "./mock.js";
import { Scope } from "./scope.js";
import type { LinkResolver, SharePointClient } from "./types.js";

/** Works from both src/ (tsx) and dist/ (tsc): sample-data sits two levels above this file. */
const DEFAULT_MOCK_DIR = path.resolve(import.meta.dirname, "../../sample-data");

export function createClient(config: { sharepoint: SharePointConfig }): SharePointClient & LinkResolver {
  const { sharepoint } = config;
  const scope = new Scope(sharepoint.allowedFolders);
  if (sharepoint.mode === "graph") {
    if (!sharepoint.graph) throw new Error("graph mode without graph config"); // parseSharePointConfig prevents this
    const { graph } = sharepoint;
    const delegated =
      graph.auth === "delegated"
        ? new DelegatedAuth({
            tenantId: graph.tenantId,
            clientId: graph.clientId,
            cachePath: path.resolve(graph.tokenCachePath ?? ".msal-cache.json"),
          })
        : undefined;
    return new GraphSharePointClient({
      graph,
      scope,
      maxDownloadBytes: sharepoint.maxDownloadBytes,
      getAccessToken: delegated ? () => delegated.getAccessToken() : undefined,
    });
  }
  return new MockSharePointClient({
    rootDir: path.resolve(sharepoint.mockDataDir ?? DEFAULT_MOCK_DIR),
    scope,
    maxDownloadBytes: sharepoint.maxDownloadBytes,
  });
}
