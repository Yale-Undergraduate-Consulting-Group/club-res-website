import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { FileTooLargeError, InvalidPathError, NotAllowedError, UpstreamError } from "../src/sharepoint/errors.js";
import { MockSharePointClient } from "../src/sharepoint/mock.js";
import { Scope } from "../src/sharepoint/scope.js";
import type { SearchHit } from "../src/sharepoint/types.js";
import { makeBombDocx } from "./helpers/zip.js";

const SAMPLE_DIR = path.resolve(import.meta.dirname, "../sample-data");
const CANARY = /SECRET-CANARY/i;
const ACME_GTM = "Past Projects/2023/Acme Chat - Go-To-Market Strategy";
const ID = (p: string) => Buffer.from(p, "utf8").toString("base64url");

function makeClient(opts: { rootDir?: string; allowed?: string[]; maxDownloadBytes?: number } = {}) {
  return new MockSharePointClient({
    rootDir: opts.rootDir ?? SAMPLE_DIR,
    scope: new Scope(opts.allowed ?? ["Past Projects"]),
    maxDownloadBytes: opts.maxDownloadBytes ?? 25 * 1024 * 1024,
  });
}

/** Everything a caller could ever see, flattened to a string, for canary sweeps. */
function dump(value: unknown): string {
  return JSON.stringify(value);
}

describe("MockSharePointClient: listing", () => {
  const client = makeClient();

  it("lists only the allowed root at the library root", async () => {
    const items = await client.listFolder();
    expect(items.map((i) => i.name)).toEqual(["Past Projects"]);
    expect(items[0]).toMatchObject({ kind: "folder", path: "Past Projects" });
    expect(await client.listFolder("")).toEqual(items);
    expect(await client.listFolder("/")).toEqual(items);
  });

  it("lists children alphabetically with stable opaque ids and encoded web URLs", async () => {
    const years = await client.listFolder("Past Projects");
    expect(years.map((i) => i.name)).toEqual(["2022", "2023", "2024", "2025"]);
    expect(years.every((i) => i.kind === "folder")).toBe(true);

    const projects = await client.listFolder("Past Projects/2022");
    expect(projects.map((i) => i.name)).toEqual([
      "Harborview Health System - Outpatient Ops Redesign",
      "Northwind Grocers - Loyalty Pricing Review",
    ]);
    const northwind = projects[1]!;
    expect(northwind.id).toBe(ID("Past Projects/2022/Northwind Grocers - Loyalty Pricing Review"));
    expect(northwind.webUrl).toBe(
      "https://mock.sharepoint.local/Past%20Projects/2022/Northwind%20Grocers%20-%20Loyalty%20Pricing%20Review",
    );

    const files = await client.listFolder(northwind.path);
    expect(files.map((i) => i.name)).toEqual(["Executive Summary.md", "Price Elasticity Model.xlsx"]);
    expect(files[0]).toMatchObject({
      kind: "file",
      path: `${northwind.path}/Executive Summary.md`,
      mimeType: "text/markdown",
    });
    expect(files[0]!.size).toBeGreaterThan(100);
    expect(files[0]!.lastModified).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("is case-insensitive about the requested path but reports on-disk casing", async () => {
    const items = await client.listFolder("past PROJECTS/2023");
    expect(items.length).toBe(3);
    expect(items.every((i) => i.path.startsWith("Past Projects/2023/"))).toBe(true);
  });

  it("rejects a file path with InvalidPathError('Not a folder.')", async () => {
    await expect(client.listFolder(`${ACME_GTM}/Executive Summary.md`)).rejects.toThrow(InvalidPathError);
    await expect(client.listFolder(`${ACME_GTM}/Executive Summary.md`)).rejects.toThrow("Not a folder.");
  });

  it("treats nonexistent in-scope paths exactly like out-of-scope ones", async () => {
    const missing = await client.listFolder("Past Projects/1999/Nope").catch((e) => e);
    const outside = await client.listFolder("Client Confidential").catch((e) => e);
    expect(missing).toBeInstanceOf(NotAllowedError);
    expect(outside).toBeInstanceOf(NotAllowedError);
    expect(missing.message).toBe(outside.message);
  });

  it.each([
    "Client Confidential",
    "client confidential",
    "Past Projects Confidential", // shares the "Past Projects" prefix but is a different folder
    "Past Projects Confidential/Draft Proposal.md",
    "Client Confidential/Strategy Notes.md",
    "Elsewhere",
  ])("rejects out-of-scope folder: %s", async (p) => {
    await expect(client.listFolder(p)).rejects.toThrow(NotAllowedError);
  });

  it.each([
    "..",
    "../",
    "../Client Confidential",
    "Past Projects/../Client Confidential",
    "Past Projects/./2023",
    "%2e%2e/Client Confidential",
    "Past Projects/%2e%2e/Client Confidential",
    "Past Projects/%2E%2E/Client Confidential",
    "Past Projects/%252e%252e/Client Confidential",
    "Past Projects\\..\\Client Confidential",
    "Past Projects/2023/..",
  ])("rejects traversal input: %s", async (p) => {
    await expect(client.listFolder(p)).rejects.toThrow(InvalidPathError);
  });

  it("never lists hidden (dot-prefixed) files", async () => {
    const items = await client.listFolder("Past Projects/2023/Acme Chat - Go-To-Market Strategy");
    expect(items.some((i) => i.name.startsWith("."))).toBe(false);
  });
});

describe("MockSharePointClient: getFile", () => {
  const client = makeClient();
  const exec = `${ACME_GTM}/Executive Summary.md`;

  it("reads by path and by id, with identical item metadata", async () => {
    const byPath = await client.getFile({ path: exec });
    expect(byPath.item).toMatchObject({ kind: "file", name: "Executive Summary.md", path: exec, mimeType: "text/markdown" });
    expect(byPath.item.id).toBe(ID(exec));
    expect(byPath.item.size).toBe(byPath.data.length);
    expect(byPath.data.toString("utf8")).toContain("Slack-like team messaging platform");

    const byId = await client.getFile({ id: byPath.item.id });
    expect(byId.item.path).toBe(exec);
    expect(byId.data.equals(byPath.data)).toBe(true);
  });

  it("returns binary formats byte-for-byte", async () => {
    const { item, data } = await client.getFile({ path: `${ACME_GTM}/Final Presentation.pptx` });
    expect(item.mimeType).toBe("application/vnd.openxmlformats-officedocument.presentationml.presentation");
    expect(data.subarray(0, 2).toString("latin1")).toBe("PK");
  });

  it("uses the same id in listings, search hits and getFile", async () => {
    const listed = (await client.listFolder(ACME_GTM)).find((i) => i.name === "Executive Summary.md")!;
    const hit = (await client.search("alumni mentor", 5)).find((h) => h.path === exec)!;
    expect(hit).toBeDefined();
    expect(listed.id).toBe(hit.id);
    expect((await client.getFile({ id: hit.id })).item.id).toBe(listed.id);
  });

  it("refuses the out-of-scope canary documents by path and by constructed id", async () => {
    for (const p of ["Client Confidential/Strategy Notes.md", "Past Projects Confidential/Draft Proposal.md"]) {
      await expect(client.getFile({ path: p })).rejects.toThrow(NotAllowedError);
      await expect(client.getFile({ id: ID(p) })).rejects.toThrow(NotAllowedError);
      await expect(client.getFile({ path: p.toLowerCase() })).rejects.toThrow(NotAllowedError);
    }
  });

  it("makes nonexistent in-scope files indistinguishable from out-of-scope ones", async () => {
    const missing = await client.getFile({ path: "Past Projects/2023/nope.md" }).catch((e) => e);
    const outside = await client.getFile({ path: "Client Confidential/Strategy Notes.md" }).catch((e) => e);
    expect(missing).toBeInstanceOf(NotAllowedError);
    expect(outside).toBeInstanceOf(NotAllowedError);
    expect(missing.message).toBe(outside.message);
  });

  it("rejects malformed ids with InvalidPathError", async () => {
    const encodedTraversal = ID("Past Projects/../Client Confidential/Strategy Notes.md");
    for (const id of ["", "!!!not base64!!!", "a", "UGFzdCBQcm9qZWN0cw=", "Zm9v YmFy", encodedTraversal, "////", "../x"]) {
      await expect(client.getFile({ id })).rejects.toThrow(InvalidPathError);
    }
    // Not valid UTF-8 once decoded.
    await expect(client.getFile({ id: Buffer.from([0xff, 0xfe, 0xfd]).toString("base64url") })).rejects.toThrow(InvalidPathError);
  });

  it("rejects traversal and bad paths", async () => {
    for (const p of ["../sample-data/Client Confidential/Strategy Notes.md", "Past Projects/%2e%2e/Client Confidential/Strategy Notes.md", "Past Projects/a\u0000b"]) {
      await expect(client.getFile({ path: p })).rejects.toThrow(InvalidPathError);
    }
  });

  it("rejects folders and the root", async () => {
    await expect(client.getFile({ path: ACME_GTM })).rejects.toThrow(InvalidPathError);
    await expect(client.getFile({ path: "" })).rejects.toThrow(NotAllowedError);
    await expect(client.getFile({ path: "Past Projects" })).rejects.toThrow(InvalidPathError);
  });

  it("throws FileTooLargeError from metadata, before reading any bytes", async () => {
    const small = makeClient({ maxDownloadBytes: 100 });
    const open = vi.spyOn(fs, "open");
    try {
      await expect(small.getFile({ path: exec })).rejects.toThrow(FileTooLargeError);
      await expect(small.getFile({ id: ID(exec) })).rejects.toThrow(FileTooLargeError);
      expect(open).not.toHaveBeenCalled();
      await client.getFile({ path: exec }); // sanity check that the spy would have seen a read
      expect(open).toHaveBeenCalledTimes(1);
    } finally {
      open.mockRestore();
    }
  });

  it("allows a file exactly at the limit", async () => {
    const size = (await client.getFile({ path: exec })).data.length;
    await expect(makeClient({ maxDownloadBytes: size }).getFile({ path: exec })).resolves.toBeDefined();
    await expect(makeClient({ maxDownloadBytes: size - 1 }).getFile({ path: exec })).rejects.toThrow(FileTooLargeError);
  });

  it("honours a different scope configuration", async () => {
    const other = makeClient({ allowed: ["Client Confidential"] });
    const { data } = await other.getFile({ path: "Client Confidential/Strategy Notes.md" });
    expect(data.toString("utf8")).toMatch(CANARY);
    await expect(other.getFile({ path: exec })).rejects.toThrow(NotAllowedError);
    expect((await other.listFolder()).map((i) => i.name)).toEqual(["Client Confidential"]);
  });

  it("surfaces a missing data directory as UpstreamError", async () => {
    const broken = makeClient({ rootDir: path.join(SAMPLE_DIR, "does-not-exist") });
    await expect(broken.listFolder("Past Projects")).rejects.toThrow(UpstreamError);
  });
});

describe("MockSharePointClient: search", () => {
  const client = makeClient();

  it("finds pricing work and ranks name/path matches first", async () => {
    const hits = await client.search("pricing", 10);
    const paths = hits.map((h) => h.path);
    expect(paths).toContain("Past Projects/2022/Northwind Grocers - Loyalty Pricing Review/Executive Summary.md");
    expect(paths).toContain("Past Projects/2025/Acme Chat - Enterprise Pricing and Packaging/Recommendations.md");
    // Documents with "Pricing" in their folder name outrank documents that merely mention it.
    const mentionOnly = paths.indexOf(`${ACME_GTM}/Executive Summary.md`);
    if (mentionOnly >= 0) expect(mentionOnly).toBeGreaterThan(paths.indexOf("Past Projects/2022/Northwind Grocers - Loyalty Pricing Review/Executive Summary.md"));
    for (const h of hits) {
      expect(h.kind).toBe("file");
      expect(h.id).toBe(ID(h.path));
      expect(h.webUrl.startsWith("https://mock.sharepoint.local/Past%20Projects/")).toBe(true);
    }
  });

  it("finds the chat platform go-to-market engagement first", async () => {
    const hits = await client.search("chat platform go-to-market", 5);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0]!.path.startsWith(ACME_GTM + "/")).toBe(true);
    expect(hits.slice(0, 3).every((h) => h.path.startsWith(ACME_GTM + "/"))).toBe(true);
  });

  it("is case-insensitive and returns plain-text snippets around the match", async () => {
    const hits = await client.search("SLACK-LIKE", 5);
    const hit = hits.find((h) => h.path === `${ACME_GTM}/Executive Summary.md`)!;
    expect(hit).toBeDefined();
    expect(hit.snippet).toMatch(/slack-like/i);
    expect(hit.snippet).not.toMatch(/[#*`>|]/); // markdown stripped
    expect(hit.snippet!.length).toBeLessThan(400);
    expect(hit.snippet).not.toMatch(/\n/);
  });

  it("matches text that lives only inside a pptx", async () => {
    const hits = await client.search("champion enablement kit", 5);
    expect(hits.map((h) => h.path)).toEqual([`${ACME_GTM}/Final Presentation.pptx`]);
    expect(hits[0]!.snippet).toMatch(/enablement kit/i);
  });

  it("matches text that lives only inside a docx", async () => {
    const hits = await client.search("selfie-verification fallback", 5);
    expect(hits[0]!.path).toBe("Past Projects/2023/Lumen Pay - Onboarding Funnel Optimization/Onboarding Recommendations.docx");
    expect(hits[0]!.snippet).toMatch(/selfie/i);
  });

  it("matches text that lives only inside a pdf", async () => {
    const hits = await client.search("interconnection", 5);
    expect(hits.map((h) => h.path)).toEqual(["Past Projects/2024/Verdant Grid - Climate-Tech Market Entry/Market Entry Assessment.pdf"]);
    expect(hits[0]!.snippet).toMatch(/interconnection/i);
  }, 30_000);

  it("matches text that lives only inside an xlsx", async () => {
    const hits = await client.search("frozen entrees", 5);
    expect(hits[0]!.path).toBe("Past Projects/2022/Northwind Grocers - Loyalty Pricing Review/Price Elasticity Model.xlsx");
  });

  it("respects top and sorts deterministically", async () => {
    const all = await client.search("strategy", 25);
    expect(all.length).toBeGreaterThan(2);
    expect((await client.search("strategy", 2)).map((h) => h.id)).toEqual(all.slice(0, 2).map((h) => h.id));
    expect(await client.search("strategy", 25)).toEqual(all);
    expect(await client.search("strategy", 0)).toEqual([]);
    expect(await client.search("strategy", Number.NaN)).toEqual([]);
  });

  it("returns [] for empty, blank, punctuation-only, stopword-only and nonsense queries", async () => {
    for (const q of ["", "   ", "---", "the of and", "zzzzqqqq-xxyyzz"]) {
      expect(await client.search(q, 10)).toEqual([]);
    }
  });

  it("never returns folders", async () => {
    const hits = await client.search("Acme Chat", 25);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.every((h) => h.kind === "file")).toBe(true);
  });
});

describe("MockSharePointClient: out-of-scope content never leaks", () => {
  const client = makeClient();
  const inScopeOnly = (hits: SearchHit[]) =>
    hits.every((h) => h.path === "Past Projects" || h.path.startsWith("Past Projects/"));

  it.each([
    "SECRET-CANARY-7f3a91",
    "SECRET-CANARY-prefix-trap",
    "SECRET CANARY",
    "canary",
    "7f3a91",
    "prefix-trap",
    "confidential",
    "Client Confidential",
    "Past Projects Confidential",
    "strategy notes",
    "draft proposal",
    "OUT OF SCOPE",
  ])("search %j surfaces nothing from outside Past Projects", async (q) => {
    const hits = await client.search(q, 25);
    expect(dump(hits)).not.toMatch(CANARY);
    expect(inScopeOnly(hits)).toBe(true);
    expect(hits.some((h) => /confidential/i.test(h.path))).toBe(false);
  });

  it("returns nothing at all for the canary strings and 'confidential'", async () => {
    expect(await client.search("SECRET-CANARY-7f3a91", 25)).toEqual([]);
    expect(await client.search("SECRET-CANARY-prefix-trap", 25)).toEqual([]);
    expect(await client.search("confidential", 25)).toEqual([]);
  });

  it("keeps overlapping topics (pricing, go-to-market) free of out-of-scope hits", async () => {
    for (const q of ["pricing", "chat platform go-to-market", "retail pricing strategy", "enterprise packaging", "loyalty pricing review"]) {
      const hits = await client.search(q, 25);
      expect(hits.length).toBeGreaterThan(0);
      expect(inScopeOnly(hits)).toBe(true);
      expect(dump(hits)).not.toMatch(CANARY);
    }
  });

  it("sweeps every listing and every readable file without ever seeing a canary", async () => {
    const seen: string[] = [];
    const walk = async (folder?: string): Promise<void> => {
      const items = await client.listFolder(folder);
      expect(dump(items)).not.toMatch(CANARY);
      for (const item of items) {
        expect(item.path === "Past Projects" || item.path.startsWith("Past Projects/")).toBe(true);
        if (item.kind === "folder") await walk(item.path);
        else {
          seen.push(item.path);
          const { data } = await client.getFile({ id: item.id });
          expect(data.toString("latin1")).not.toMatch(CANARY);
        }
      }
    };
    await walk();
    expect(seen.length).toBeGreaterThanOrEqual(12);
  });
});

describe("MockSharePointClient: filesystem hardening (symlinks, hidden files)", () => {
  let tmp: string;
  let outsideDir: string;
  let client: MockSharePointClient;

  beforeAll(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "yucg-mock-"));
    const lib = path.join(tmp, "library");
    outsideDir = path.join(tmp, "outside-library");
    await fs.cp(SAMPLE_DIR, lib, { recursive: true });
    await fs.mkdir(outsideDir);
    await fs.writeFile(path.join(outsideDir, "secret.md"), "SECRET-CANARY-outside-library pricing chat platform go-to-market");

    const pp = path.join(lib, "Past Projects");
    // 1. directory symlink to a sibling folder inside the library but outside the allowed scope
    await fs.symlink("../../Client Confidential", path.join(pp, "2023", "escape-link"));
    // 2. file symlink to an out-of-scope file
    await fs.symlink("../Client Confidential/Strategy Notes.md", path.join(pp, "leak.md"));
    // 3. the prefix-trap sibling, also via symlink
    await fs.symlink("../Past Projects Confidential", path.join(pp, "prefix-link"));
    // 4. symlinks out of the library entirely (relative and absolute)
    await fs.symlink("../../outside-library", path.join(pp, "outside-rel"));
    await fs.symlink(outsideDir, path.join(pp, "outside-abs"));
    await fs.symlink(path.join(outsideDir, "secret.md"), path.join(pp, "outside-file.md"));
    // 5. a symlink loop back into the allowed tree, and a dangling link
    await fs.symlink(".", path.join(pp, "2022", "loop"));
    await fs.symlink("nowhere", path.join(pp, "dangling"));
    // 6. hidden files and a file named like a folder-sorting trap
    await fs.writeFile(path.join(pp, ".hidden.txt"), "zzhiddenneedle pricing");
    await fs.mkdir(path.join(pp, ".hidden-dir"));
    await fs.writeFile(path.join(pp, ".hidden-dir", "x.md"), "zzhiddendirneedle");
    await fs.writeFile(path.join(pp, "0 first alphabetically.txt"), "plain file at top level");
    // 7. a small zip that inflates to ~60 MB: search parses every office file it finds, so this must not hurt
    await fs.writeFile(path.join(pp, "2025", "bomb.docx"), makeBombDocx(60));

    client = makeClient({ rootDir: lib });
  });

  afterAll(async () => {
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("does not list symlinks that resolve outside the allowed scope", async () => {
    const top = await client.listFolder("Past Projects");
    const names = top.map((i) => i.name);
    for (const bad of ["leak.md", "prefix-link", "outside-rel", "outside-abs", "outside-file.md", "dangling"]) {
      expect(names).not.toContain(bad);
    }
    const y2023 = await client.listFolder("Past Projects/2023");
    expect(y2023.map((i) => i.name)).not.toContain("escape-link");
  });

  it("refuses to follow symlinks out of scope for listFolder and getFile", async () => {
    await expect(client.listFolder("Past Projects/2023/escape-link")).rejects.toThrow(NotAllowedError);
    await expect(client.listFolder("Past Projects/prefix-link")).rejects.toThrow(NotAllowedError);
    await expect(client.listFolder("Past Projects/outside-rel")).rejects.toThrow(NotAllowedError);
    await expect(client.listFolder("Past Projects/outside-abs")).rejects.toThrow(NotAllowedError);
    for (const p of [
      "Past Projects/2023/escape-link/Strategy Notes.md",
      "Past Projects/leak.md",
      "Past Projects/prefix-link/Draft Proposal.md",
      "Past Projects/outside-rel/secret.md",
      "Past Projects/outside-abs/secret.md",
      "Past Projects/outside-file.md",
      "Past Projects/dangling",
    ]) {
      await expect(client.getFile({ path: p })).rejects.toThrow(NotAllowedError);
      await expect(client.getFile({ id: ID(p) })).rejects.toThrow(NotAllowedError);
    }
  });

  it("never reads through a symlink during search", async () => {
    for (const q of ["SECRET-CANARY", "secret canary outside library", "pricing", "chat platform go-to-market", "confidential"]) {
      const hits = await client.search(q, 25);
      expect(dump(hits)).not.toMatch(CANARY);
      expect(hits.some((h) => /escape-link|leak\.md|prefix-link|outside|dangling/.test(h.path))).toBe(false);
    }
  });

  it("terminates on symlink loops without duplicating results", async () => {
    const baseline = await makeClient().search("pricing", 25);
    const hits = await client.search("pricing", 25);
    expect(hits.map((h) => h.path)).toEqual(baseline.map((h) => h.path));
    expect(new Set(hits.map((h) => h.path)).size).toBe(hits.length);
  });

  it("hides dot-prefixed files and folders everywhere", async () => {
    const top = await client.listFolder("Past Projects");
    expect(top.some((i) => i.name.startsWith("."))).toBe(false);
    expect(await client.search("zzhiddenneedle", 10)).toEqual([]);
    expect(await client.search("zzhiddendirneedle", 10)).toEqual([]);
    await expect(client.getFile({ path: "Past Projects/.hidden.txt" })).rejects.toThrow(NotAllowedError);
    await expect(client.getFile({ path: "Past Projects/.hidden-dir/x.md" })).rejects.toThrow(NotAllowedError);
    await expect(client.listFolder("Past Projects/.hidden-dir")).rejects.toThrow(NotAllowedError);
  });

  it("survives a decompression bomb in the library: search stays fast and correct, reads still work", async () => {
    const started = performance.now();
    const hits = await client.search("pricing", 25);
    expect(performance.now() - started).toBeLessThan(10_000);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.some((h) => h.name === "bomb.docx")).toBe(false); // its content could not be read, so it is not a content match

    const byName = await client.search("bomb", 5); // still findable by file name
    expect(byName.map((h) => h.name)).toContain("bomb.docx");
    const { data } = await client.getFile({ path: "Past Projects/2025/bomb.docx" }); // raw bytes are fine to download
    expect(data.length).toBeLessThan(1024 * 1024);
  }, 30_000);

  it("lists folders before files, each alphabetical", async () => {
    const top = await client.listFolder("Past Projects");
    const kinds = top.map((i) => i.kind);
    const firstFile = kinds.indexOf("file");
    expect(firstFile).toBeGreaterThan(0);
    expect(kinds.slice(0, firstFile).every((k) => k === "folder")).toBe(true);
    expect(kinds.slice(firstFile).every((k) => k === "file")).toBe(true);
    expect(top.filter((i) => i.kind === "file").map((i) => i.name)).toEqual(["0 first alphabetically.txt"]);
  });
});
