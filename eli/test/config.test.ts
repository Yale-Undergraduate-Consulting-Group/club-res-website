import { describe, expect, it } from "vitest";
import { cleanEnv, parseSharePointConfig } from "../src/config.js";

const graphEnv = {
  SHAREPOINT_MODE: "graph",
  AZURE_TENANT_ID: "tenant",
  AZURE_CLIENT_ID: "client",
  AZURE_CLIENT_SECRET: "secret",
  SHAREPOINT_SITE_ID: "contoso.sharepoint.com,a,b",
  SHAREPOINT_ALLOWED_FOLDERS: "Past Projects",
};

describe("parseSharePointConfig", () => {
  it("defaults to mock mode with the sample folder", () => {
    const c = parseSharePointConfig({});
    expect(c.mode).toBe("mock");
    expect(c.allowedFolders).toEqual(["Past Projects"]);
    expect(c.maxDownloadBytes).toBe(25 * 1024 * 1024);
  });

  it("treats empty strings (FOO= in .env) as unset", () => {
    expect(cleanEnv({ A: "", B: "x", C: undefined })).toEqual({ B: "x" });
    expect(parseSharePointConfig({ AZURE_TENANT_ID: "", SHAREPOINT_ALLOWED_FOLDERS: "" }).allowedFolders).toEqual(["Past Projects"]);
  });

  it("requires allowed folders and app credentials in graph mode", () => {
    expect(() => parseSharePointConfig({ SHAREPOINT_MODE: "graph" })).toThrow(/ALLOWED_FOLDERS is required/);
    const { AZURE_CLIENT_SECRET: _omit, ...partial } = graphEnv;
    expect(() => parseSharePointConfig(partial)).toThrow(/AZURE_CLIENT_SECRET/);
    const ok = parseSharePointConfig(graphEnv);
    expect(ok.graph).toMatchObject({ tenantId: "tenant", clientId: "client", siteId: "contoso.sharepoint.com,a,b", searchRegion: "NAM" });
  });

  it("needs only a client id for delegated sign-in", () => {
    const c = parseSharePointConfig({ SHAREPOINT_MODE: "graph", GRAPH_AUTH: "delegated", AZURE_CLIENT_ID: "client", SHAREPOINT_ALLOWED_FOLDERS: "YUCG Eli" });
    expect(c.graph).toMatchObject({ auth: "delegated", tenantId: "organizations", clientId: "client" });
    expect(() => parseSharePointConfig({ SHAREPOINT_MODE: "graph", GRAPH_AUTH: "delegated", SHAREPOINT_ALLOWED_FOLDERS: "X" })).toThrow(/AZURE_CLIENT_ID/);
  });

  it("rejects unsafe allowed folders", () => {
    expect(() => parseSharePointConfig({ SHAREPOINT_ALLOWED_FOLDERS: "/" })).toThrow();
    expect(() => parseSharePointConfig({ SHAREPOINT_ALLOWED_FOLDERS: "Past Projects/../Other" })).toThrow();
  });
});
