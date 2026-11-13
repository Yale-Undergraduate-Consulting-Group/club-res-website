import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FileTooLargeError,
  InvalidPathError,
  NotAllowedError,
  UpstreamError,
} from "../src/sharepoint/errors.js";
import { GraphSharePointClient } from "../src/sharepoint/graph.js";
import { Scope } from "../src/sharepoint/scope.js";

// ---------------------------------------------------------------------------------------------
// A small in-memory fake of the parts of Microsoft Graph that the client talks to.

const TENANT = "tenant-1";
const CLIENT_ID = "client-1";
const SECRET = "s3cr3t-client-secret-value";
const SITE = "contoso.sharepoint.com,site-guid,web-guid";
const DRIVE = "b!drive1";
const LIB_URL = "https://contoso.sharepoint.com/sites/Team/Shared%20Documents";
const TOKEN_URL = `https://login.microsoftonline.com/${TENANT}/oauth2/v2.0/token`;
const DOWNLOAD_HOST = "download.example.com";

interface Stored {
  id: string;
  /** Library-relative path. */
  path: string;
  kind: "file" | "folder";
  content?: Buffer;
  /** Metadata size; defaults to content length. Can lie, to test byte-level enforcement. */
  size?: number;
  driveId?: string;
  /** Omit parentReference.path from metadata/children responses. */
  hidePath?: boolean;
  remote?: boolean;
}

interface Call {
  method: string;
  url: string;
  u: URL;
  headers: Headers;
  body?: string;
  redirect?: string;
}

const idOf = (path: string): string => "id-" + path.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const file = (path: string, content = "hello", extra: Partial<Stored> = {}): Stored => ({
  id: idOf(path),
  path,
  kind: "file",
  content: Buffer.from(content),
  ...extra,
});
const folder = (path: string, extra: Partial<Stored> = {}): Stored => ({ id: idOf(path), path, kind: "folder", ...extra });

const parentOf = (p: string): string => p.split("/").slice(0, -1).join("/");
const encSegs = (p: string): string => p.split("/").map(encodeURIComponent).join("/");

interface RawOpts {
  omitPath?: boolean;
  omitFacet?: boolean;
  omitDrive?: boolean;
}

function rawItem(s: Stored, o: RawOpts = {}): Record<string, unknown> {
  const parent = parentOf(s.path);
  const drive = s.driveId ?? DRIVE;
  const out: Record<string, unknown> = {
    id: s.id,
    name: s.path.split("/").at(-1),
    webUrl: `${LIB_URL}/${encSegs(s.path)}`,
    lastModifiedDateTime: "2024-05-01T10:00:00Z",
    parentReference: {
      ...(o.omitDrive ? {} : { driveId: drive }),
      ...(o.omitPath || s.hidePath ? {} : { path: `/drives/${drive}/root:${parent ? "/" + encSegs(parent) : ""}` }),
    },
  };
  if (s.kind === "file") out.size = s.size ?? s.content?.length ?? 0;
  if (!o.omitFacet) {
    if (s.kind === "folder") out.folder = { childCount: 1 };
    else out.file = { mimeType: "text/plain" };
  }
  if (s.remote) out.remoteItem = { id: "remote-1", parentReference: { driveId: "b!elsewhere" } };
  return out;
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function searchResponse(hits: Array<{ resource: unknown; summary?: string }>): unknown {
  return {
    value: [
      {
        searchTerms: [],
        hitsContainers: [
          {
            total: hits.length,
            moreResultsAvailable: false,
            hits: hits.map((h, i) => ({ hitId: `h${i}`, rank: i + 1, summary: h.summary, resource: h.resource })),
          },
        ],
      },
    ],
  };
}

class FakeGraph {
  calls: Call[] = [];
  unrouted: string[] = [];
  tokenCalls = 0;
  tokenDelayMs = 0;
  items: Stored[] = [];
  /** Search response body; set per test. */
  search: () => unknown = () => searchResponse([]);
  /** Checked first; return a Response to short-circuit the normal routing. */
  overrides: Array<(c: Call) => Response | undefined> = [];
  contentMode: "redirect" | "direct" = "redirect";

  readonly fetch = (async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const call: Call = {
      method: init?.method ?? "GET",
      url,
      u: new URL(url),
      headers: new Headers(init?.headers),
      body: typeof init?.body === "string" ? init.body : undefined,
      redirect: init?.redirect,
    };
    this.calls.push(call);
    for (const o of this.overrides) {
      const r = o(call);
      if (r) return r;
    }
    return this.route(call);
  }) as typeof fetch;

  graphCalls(): Call[] {
    return this.calls.filter((c) => c.u.origin === "https://graph.microsoft.com");
  }
  contentCalls(): Call[] {
    return this.calls.filter((c) => c.u.pathname.endsWith("/content") || c.u.host === DOWNLOAD_HOST);
  }
  tokenRequests(): Call[] {
    return this.calls.filter((c) => c.url === TOKEN_URL);
  }

  private byPath(p: string): Stored | undefined {
    return this.items.find((i) => i.path.toLowerCase() === p.toLowerCase());
  }

  private async route(call: Call): Promise<Response> {
    const { u } = call;
    if (call.url === TOKEN_URL) {
      this.tokenCalls++;
      if (this.tokenDelayMs) await new Promise((r) => setTimeout(r, this.tokenDelayMs));
      return json({ token_type: "Bearer", expires_in: 3600, access_token: `token-${this.tokenCalls}` });
    }
    if (u.host === DOWNLOAD_HOST) {
      const s = this.items.find((i) => i.id === decodeURIComponent(u.pathname.split("/").at(-1) ?? ""));
      return s?.content ? new Response(new Uint8Array(s.content)) : new Response("nope", { status: 404 });
    }
    if (u.origin === "https://graph.microsoft.com") {
      const rest = u.pathname.replace(/^\/v1\.0/, "");
      let m: RegExpExecArray | null;
      if (call.method === "POST" && rest === "/search/query") return json(this.search());
      if ((m = /^\/sites\/([^/]+)\/drive$/.exec(rest))) return json({ id: DRIVE, webUrl: LIB_URL });
      if ((m = /^\/drives\/([^/]+)$/.exec(rest))) return json({ id: DRIVE, webUrl: LIB_URL });
      if ((m = /^\/drives\/([^/]+)\/items\/([^/]+)\/content$/.exec(rest))) {
        const s = this.items.find((i) => i.id === decodeURIComponent(m![2]!));
        if (!s?.content) return new Response(null, { status: 404 });
        return this.contentMode === "direct"
          ? new Response(new Uint8Array(s.content))
          : new Response(null, { status: 302, headers: { location: `https://${DOWNLOAD_HOST}/dl/${s.id}?sig=abc` } });
      }
      if ((m = /^\/drives\/([^/]+)\/items\/([^/]+)$/.exec(rest))) {
        const s = this.items.find((i) => i.id === decodeURIComponent(m![2]!));
        return s ? json(rawItem(s)) : json({ error: { code: "itemNotFound" } }, 404);
      }
      if ((m = /^\/drives\/([^/]+)\/root:\/(.+):\/children$/.exec(rest))) {
        const p = m[2]!.split("/").map(decodeURIComponent).join("/");
        const kids = this.items.filter((i) => parentOf(i.path).toLowerCase() === p.toLowerCase());
        return json({ value: kids.map((k) => rawItem(k)) });
      }
      if ((m = /^\/drives\/([^/]+)\/root:\/(.+)$/.exec(rest))) {
        const s = this.byPath(m[2]!.split("/").map(decodeURIComponent).join("/"));
        return s ? json(rawItem(s)) : json({ error: { code: "itemNotFound" } }, 404);
      }
    }
    this.unrouted.push(`${call.method} ${call.url}`);
    return new Response("unrouted", { status: 418 });
  }
}

// ---------------------------------------------------------------------------------------------

let fake: FakeGraph;

beforeEach(() => {
  fake = new FakeGraph();
  fake.items = [
    folder("Past Projects"),
    folder("Past Projects/2023"),
    file("Past Projects/2023/Acme Pricing.docx", "acme"),
    folder("Shared Templates"),
    file("Shared Templates/Proposal.docx", "template"),
    folder("Client Confidential"),
    file("Client Confidential/Secret.docx", "top secret"),
    folder("Past Projects 2"),
    file("Past Projects 2/Lookalike.docx", "lookalike"),
  ];
});

afterEach(() => {
  vi.useRealTimers();
  // Every request must hit a route the fake knows about.
  expect(fake.unrouted).toEqual([]);
});

interface ClientOverrides {
  roots?: string[];
  driveId?: string | undefined;
  max?: number;
}

function makeClient(over: ClientOverrides = {}): GraphSharePointClient {
  return new GraphSharePointClient({
    graph: {
      tenantId: TENANT,
      clientId: CLIENT_ID,
      clientSecret: SECRET,
      siteId: SITE,
      driveId: "driveId" in over ? over.driveId : DRIVE,
      searchRegion: "NAM",
    },
    scope: new Scope(over.roots ?? ["Past Projects", "Shared Templates"]),
    maxDownloadBytes: over.max ?? 1000,
    fetchImpl: fake.fetch,
  });
}

async function failure(p: Promise<unknown>): Promise<Error> {
  try {
    await p;
  } catch (e) {
    return e as Error;
  }
  throw new Error("expected promise to reject");
}

const find = (path: string): Stored => {
  const s = fake.items.find((i) => i.path === path);
  if (!s) throw new Error(`no fixture ${path}`);
  return s;
};

// ---------------------------------------------------------------------------------------------

describe("token handling", () => {
  it("uses client-credentials against the tenant endpoint with a form-encoded body", async () => {
    await makeClient().listFolder();
    const req = fake.tokenRequests()[0]!;
    expect(req.method).toBe("POST");
    expect(req.headers.get("content-type")).toBe("application/x-www-form-urlencoded");
    const form = new URLSearchParams(req.body);
    expect(Object.fromEntries(form)).toEqual({
      client_id: CLIENT_ID,
      client_secret: SECRET,
      grant_type: "client_credentials",
      scope: "https://graph.microsoft.com/.default",
    });
    expect(fake.graphCalls()[0]!.headers.get("authorization")).toBe("Bearer token-1");
  });

  it("caches the token across calls and refreshes it 60s before expiry", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    const client = makeClient();

    await client.listFolder();
    await client.listFolder();
    expect(fake.tokenCalls).toBe(1);

    vi.setSystemTime(Date.now() + 3_500_000); // 3500s < 3540s (3600 - 60)
    await client.listFolder();
    expect(fake.tokenCalls).toBe(1);

    vi.setSystemTime(Date.now() + 60_000); // 3560s > 3540s
    await client.listFolder();
    expect(fake.tokenCalls).toBe(2);
    expect(fake.graphCalls().at(-1)!.headers.get("authorization")).toBe("Bearer token-2");
  });

  it("coalesces concurrent first calls into ONE token request", async () => {
    fake.tokenDelayMs = 25;
    const client = makeClient();
    await Promise.all([client.listFolder(), client.listFolder(), client.listFolder("Past Projects")]);
    expect(fake.tokenRequests()).toHaveLength(1);
  });

  it("maps token endpoint failures to UpstreamError carrying only the status", async () => {
    fake.overrides.push((c) => (c.url === TOKEN_URL ? new Response(`bad ${SECRET}`, { status: 401 }) : undefined));
    const err = await failure(makeClient().listFolder());
    expect(err).toBeInstanceOf(UpstreamError);
    expect(err.message).toContain("401");
    expect(err.message).not.toContain(SECRET);
  });

  it("does not cache a failed token request", async () => {
    let fail = true;
    fake.overrides.push((c) => (c.url === TOKEN_URL && fail ? new Response("x", { status: 500 }) : undefined));
    const client = makeClient();
    await expect(client.listFolder()).rejects.toBeInstanceOf(UpstreamError);
    fail = false;
    await expect(client.listFolder()).resolves.toHaveLength(2);
  });
});

describe("drive resolution", () => {
  it("resolves the site's default library once when no driveId is configured", async () => {
    const client = makeClient({ driveId: undefined });
    await client.listFolder();
    await client.listFolder();
    const siteCalls = fake.graphCalls().filter((c) => c.u.pathname === `/v1.0/sites/${SITE}/drive`);
    expect(siteCalls).toHaveLength(1);
    expect(siteCalls[0]!.u.search).toBe("?$select=id,webUrl");
  });

  it("reuses the site drive's webUrl for search instead of a second lookup", async () => {
    await makeClient({ driveId: undefined }).search("acme", 5);
    expect(fake.graphCalls().filter((c) => c.u.pathname === `/v1.0/drives/${DRIVE}`)).toHaveLength(0);
    const body = JSON.parse(fake.calls.find((c) => c.method === "POST" && c.u.pathname.endsWith("/search/query"))!.body!);
    expect(body.requests[0].query.queryString).toContain('path:"https://contoso.sharepoint.com/sites/Team/Shared Documents/Past Projects"');
  });

  it("fetches the library webUrl lazily (only for search) and caches it", async () => {
    const client = makeClient();
    await client.listFolder();
    const libCalls = () => fake.graphCalls().filter((c) => c.u.pathname === `/v1.0/drives/${DRIVE}`);
    expect(libCalls()).toHaveLength(0);
    await client.search("acme", 5);
    await client.search("pricing", 5);
    expect(libCalls()).toHaveLength(1);
  });

  it("reports a missing configured site as an upstream/config problem, not a scope decision", async () => {
    fake.overrides.push((c) => (c.u.pathname.endsWith("/drive") ? json({}, 404) : undefined));
    await expect(makeClient({ driveId: undefined }).listFolder()).rejects.toBeInstanceOf(UpstreamError);
  });
});

describe("search", () => {
  it("sends region, entity type, over-fetch size and a path clause for every scope root", async () => {
    await makeClient().search("acme pricing", 3);
    const call = fake.calls.find((c) => c.method === "POST" && c.u.pathname === "/v1.0/search/query")!;
    expect(call.headers.get("authorization")).toBe("Bearer token-1");
    const req = JSON.parse(call.body!).requests[0];
    expect(req.entityTypes).toEqual(["driveItem"]);
    expect(req.region).toBe("NAM");
    expect(req.from).toBe(0);
    expect(req.size).toBe(9);
    const qs: string = req.query.queryString;
    expect(qs).toBe(
      '(acme pricing) AND (path:"https://contoso.sharepoint.com/sites/Team/Shared Documents/Past Projects"' +
        ' OR path:"https://contoso.sharepoint.com/sites/Team/Shared Documents/Shared Templates")',
    );
  });

  it("caps the over-fetch at 100", async () => {
    await makeClient().search("acme", 90);
    const req = JSON.parse(fake.calls.find((c) => c.u.pathname.endsWith("/search/query"))!.body!).requests[0];
    expect(req.size).toBe(100);
  });

  it("returns [] without any network call for empty / whitespace / operator-only queries", async () => {
    const client = makeClient();
    for (const q of ["", "   ", '"()"', "AND OR NOT"]) expect(await client.search(q, 5)).toEqual([]);
    expect(fake.calls).toEqual([]);
  });

  it("keeps only in-scope, same-drive hits and drops everything it cannot positively place", async () => {
    const inScope = find("Past Projects/2023/Acme Pricing.docx");
    const template = find("Shared Templates/Proposal.docx");
    const secret = find("Client Confidential/Secret.docx");
    const lookalike = find("Past Projects 2/Lookalike.docx");
    const wrongDrive = file("Past Projects/2023/Wrong Drive.docx", "x", { driveId: "b!other" });
    // Items whose search resource lacks parentReference.path and must be resolved via an item fetch:
    const noPathOk = file("Past Projects/2023/Resolved.docx", "r");
    const noPathSecret = file("Client Confidential/Resolved Secret.docx", "r");
    const noFacet = file("Shared Templates/No Facet.docx", "f");
    fake.items.push(noPathOk, noPathSecret, noFacet);

    fake.search = () =>
      searchResponse([
        { resource: rawItem(secret) },
        { resource: rawItem(wrongDrive) },
        { resource: rawItem(lookalike) },
        { resource: rawItem(noPathSecret, { omitPath: true }) },
        { resource: rawItem(noPathOk, { omitPath: true }), summary: "<c0>Resolved</c0> hit" },
        { resource: { ...rawItem(file("Past Projects/Ghost.docx"), { omitPath: true }) } }, // unknown id -> 404 -> drop
        { resource: rawItem(inScope), summary: "<c0>Acme</c0> retail <ddd/> pricing <c0>model</c0>" },
        { resource: rawItem(noFacet, { omitFacet: true }) },
        { resource: rawItem(template, { omitDrive: true }) }, // no driveId on hit -> resolved via our drive
        { resource: { name: "No Id.docx", parentReference: {} } }, // nothing to resolve with -> drop
      ]);

    const hits = await makeClient().search("acme", 25);
    expect(hits.map((h) => h.path)).toEqual([
      "Past Projects/2023/Resolved.docx",
      "Past Projects/2023/Acme Pricing.docx",
      "Shared Templates/No Facet.docx",
      "Shared Templates/Proposal.docx",
    ]);
    expect(hits.every((h) => h.kind === "file")).toBe(true);
    expect(hits[0]!.snippet).toBe("Resolved hit");
    expect(hits[1]).toMatchObject({
      id: inScope.id,
      name: "Acme Pricing.docx",
      kind: "file",
      size: 4,
      mimeType: "text/plain",
      lastModified: "2024-05-01T10:00:00Z",
    });

    // The hits without a path were resolved against OUR drive; the out-of-drive hit triggered no lookup.
    const itemFetches = fake.graphCalls().filter((c) => /\/items\/[^/]+$/.test(c.u.pathname)).map((c) => c.u.pathname);
    expect(itemFetches).toContain(`/v1.0/drives/${DRIVE}/items/${noPathOk.id}`);
    expect(itemFetches).toContain(`/v1.0/drives/${DRIVE}/items/${noPathSecret.id}`);
    expect(itemFetches.some((p) => p.includes(wrongDrive.id))).toBe(false);
    // No content is ever requested during search.
    expect(fake.contentCalls()).toEqual([]);
  });

  it("strips highlight markup into a plain-text snippet", async () => {
    const f = find("Past Projects/2023/Acme Pricing.docx");
    fake.search = () =>
      searchResponse([{ resource: rawItem(f), summary: "<c0>Acme</c0> retail<ddd/>  <c1>pricing</c1>  model</c0> " }]);
    const [hit] = await makeClient().search("acme", 5);
    expect(hit!.snippet).toBe("Acme retail… pricing model");
  });

  it("returns at most `top` hits", async () => {
    fake.search = () =>
      searchResponse([
        { resource: rawItem(find("Past Projects/2023/Acme Pricing.docx")) },
        { resource: rawItem(find("Shared Templates/Proposal.docx")) },
        { resource: rawItem(find("Past Projects/2023")) },
      ]);
    const hits = await makeClient().search("x", 2);
    expect(hits).toHaveLength(2);
    expect(hits[0]!.kind).toBe("file");
  });

  it("reports folders as kind=folder without a size", async () => {
    fake.search = () => searchResponse([{ resource: rawItem(find("Past Projects/2023")) }]);
    const [hit] = await makeClient().search("2023", 5);
    expect(hit).toMatchObject({ kind: "folder", path: "Past Projects/2023", name: "2023" });
    expect(hit!.size).toBeUndefined();
  });

  it("drops shortcut (remoteItem) hits", async () => {
    const s = file("Past Projects/Shortcut.docx", "x", { remote: true });
    fake.items.push(s);
    fake.search = () => searchResponse([{ resource: rawItem(s) }]);
    expect(await makeClient().search("x", 5)).toEqual([]);
  });

  it("surfaces hits whose parentReference.path is URL-encoded, decoding it", async () => {
    const s = file("Past Projects/Q1 #Plan 100%/Deck.pptx");
    fake.search = () => searchResponse([{ resource: rawItem(s) }]);
    const [hit] = await makeClient().search("deck", 5);
    expect(hit!.path).toBe("Past Projects/Q1 #Plan 100%/Deck.pptx");
  });

  it("neutralises user input that tries to inject KQL (quotes, parens, operators, property filters)", async () => {
    const client = makeClient();
    const userPartOf = (): string => {
      const call = fake.calls.filter((c) => c.u.pathname.endsWith("/search/query")).at(-1)!;
      const qs: string = JSON.parse(call.body!).requests[0].query.queryString;
      const idx = qs.indexOf(') AND (path:"https://contoso');
      expect(idx).toBeGreaterThan(0);
      // Exactly the two scope roots are present as path clauses, nothing else.
      expect(qs.match(/path:"/g)).toHaveLength(2);
      expect(qs.match(/path:"[^"]*"/g)).toEqual([
        'path:"https://contoso.sharepoint.com/sites/Team/Shared Documents/Past Projects"',
        'path:"https://contoso.sharepoint.com/sites/Team/Shared Documents/Shared Templates"',
      ]);
      return qs.slice(0, idx + 1); // "(<user part>)"
    };

    await client.search('x" OR path:"https://evil"', 5);
    const sent = JSON.stringify(JSON.parse(fake.calls.find((c) => c.u.pathname.endsWith("/search/query"))!.body!));
    expect(sent).not.toContain('path:\\"https://evil');
    expect(sent).not.toContain('x\\" OR');
    expect(userPartOf()).toBe("(x path https evil)");

    for (const q of [
      "a) OR (b",
      'foo" ) OR path:"https://evil" OR ("',
      "secret AND NOT public",
      "-excluded +required",
      "title:confidential",
      "acme* OR filetype:docx",
      "a NEAR(2) b",
      "x\u0000y\n z",
    ]) {
      await client.search(q, 5);
      const part = userPartOf();
      expect(part).toMatch(/^\([^()":*+<>=]*\)$/);
      expect(part).not.toMatch(/\b(?:AND|OR|NOT|NEAR|XRANK)\b/i);
      expect(part).not.toMatch(/(?:^|[\s(])-/);
    }
  });

  describe("hit whose parentReference has an in-scope path but NO driveId", () => {
    // The payload claims Past Projects/2023/Payload.docx; only Graph's metadata for the id (fetched from OUR
    // drive) decides what happens. A payload we can't tie to our drive must never be trusted on its own.
    const payloadFor = (id: string): Record<string, unknown> => ({
      ...rawItem(file("Past Projects/2023/Payload.docx"), { omitDrive: true }),
      id,
    });
    const itemFetches = (): string[] =>
      fake.graphCalls().filter((c) => /\/items\/[^/]+$/.test(c.u.pathname)).map((c) => c.u.pathname);

    it("is returned from the re-resolved metadata (not the payload) when that metadata is in scope", async () => {
      const real = file("Shared Templates/FromMetadata.docx", "m");
      fake.items.push(real);
      fake.search = () => searchResponse([{ resource: payloadFor(real.id) }]);
      const hits = await makeClient().search("x", 5);
      expect(itemFetches()).toEqual([`/v1.0/drives/${DRIVE}/items/${real.id}`]);
      expect(hits.map((h) => [h.id, h.path, h.name])).toEqual([[real.id, "Shared Templates/FromMetadata.docx", "FromMetadata.docx"]]);
    });

    it.each([
      ["metadata is out of scope", file("Client Confidential/Moved.docx", "m")],
      ["metadata is in another drive", file("Past Projects/Elsewhere.docx", "m", { driveId: "b!other" })],
      ["metadata has no determinable path", file("Past Projects/Hidden.docx", "m", { hidePath: true })],
      ["the item does not exist (404)", undefined],
    ])("is dropped when %s", async (_name, stored) => {
      if (stored) fake.items.push(stored);
      const id = stored?.id ?? "ghost-id";
      fake.search = () => searchResponse([{ resource: payloadFor(id) }]);
      expect(await makeClient().search("x", 5)).toEqual([]);
      expect(itemFetches()).toEqual([`/v1.0/drives/${DRIVE}/items/${id}`]);
    });
  });

  it("keeps ordinary punctuation and non-ASCII words", async () => {
    await makeClient().search("Zürich R&D year-end 2023 acme_corp v1.2", 5);
    const req = JSON.parse(fake.calls.find((c) => c.u.pathname.endsWith("/search/query"))!.body!).requests[0];
    expect(req.query.queryString).toMatch(/^\(Zürich R&D year-end 2023 acme_corp v1\.2\) AND \(/);
  });
});

describe("listFolder", () => {
  it("returns the allowed root folders when called with no path", async () => {
    const client = makeClient();
    for (const arg of [undefined, "", "/"]) {
      const roots = await client.listFolder(arg);
      expect(roots.map((r) => [r.kind, r.path, r.name])).toEqual([
        ["folder", "Past Projects", "Past Projects"],
        ["folder", "Shared Templates", "Shared Templates"],
      ]);
    }
    // Never touched the out-of-scope folder.
    expect(fake.calls.some((c) => /Client/.test(c.url))).toBe(false);
  });

  it("skips roots that do not exist", async () => {
    const roots = await makeClient({ roots: ["Past Projects", "Missing Folder"] }).listFolder();
    expect(roots.map((r) => r.path)).toEqual(["Past Projects"]);
  });

  it("rejects out-of-scope paths with NotAllowedError and makes no Graph request at all", async () => {
    const client = makeClient();
    for (const p of ["Client Confidential", "Client Confidential/Secret.docx", "Past Projects 2", "Past"]) {
      await expect(client.listFolder(p)).rejects.toBeInstanceOf(NotAllowedError);
    }
    expect(fake.calls).toEqual([]);
  });

  it("rejects traversal with InvalidPathError before any request", async () => {
    await expect(makeClient().listFolder("Past Projects/../Client Confidential")).rejects.toBeInstanceOf(InvalidPathError);
    expect(fake.calls).toEqual([]);
  });

  it("lists children of an allowed folder", async () => {
    const items = await makeClient().listFolder("Past Projects/2023");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: idOf("Past Projects/2023/Acme Pricing.docx"),
      name: "Acme Pricing.docx",
      kind: "file",
      path: "Past Projects/2023/Acme Pricing.docx",
      size: 4,
    });
    const children = fake.graphCalls().find((c) => c.u.pathname.endsWith(":/children"))!;
    expect(children.u.search).toContain("$top=200");
    expect(children.u.search).toContain("$select=id,name,size,webUrl,lastModifiedDateTime,file,folder");
  });

  it("returns an allowed root's own children (case-insensitive path match)", async () => {
    const items = await makeClient().listFolder("past projects");
    expect(items.map((i) => i.name)).toEqual(["2023"]);
  });

  it("percent-encodes every path segment (spaces, #, %, &)", async () => {
    fake.items.push(folder("Past Projects/Q1 #Plan 100%"), file("Past Projects/Q1 #Plan 100%/a&b.docx"));
    const items = await makeClient().listFolder("Past Projects/Q1 #Plan 100%");
    expect(items.map((i) => i.path)).toEqual(["Past Projects/Q1 #Plan 100%/a&b.docx"]);

    const calls = fake.graphCalls().map((c) => c.u.pathname);
    expect(calls).toContain(`/v1.0/drives/${DRIVE}/root:/Past%20Projects/Q1%20%23Plan%20100%25`);
    expect(calls).toContain(`/v1.0/drives/${DRIVE}/root:/Past%20Projects/Q1%20%23Plan%20100%25:/children`);
    // Nothing unencoded leaked into a request URL: '#' would start a fragment, '%' must be %25.
    for (const c of fake.graphCalls()) {
      expect(c.url).not.toContain("#");
      expect(c.url).not.toMatch(/ |%(?![0-9A-Fa-f]{2})/);
    }
  });

  it("raises InvalidPathError('Not a folder.') for a file", async () => {
    const err = await failure(makeClient().listFolder("Past Projects/2023/Acme Pricing.docx"));
    expect(err).toBeInstanceOf(InvalidPathError);
    expect(err.message).toBe("Not a folder.");
    expect(fake.calls.some((c) => c.u.pathname.endsWith(":/children"))).toBe(false);
  });

  it("treats a nonexistent in-scope folder as NotAllowedError", async () => {
    await expect(makeClient().listFolder("Past Projects/Nope")).rejects.toBeInstanceOf(NotAllowedError);
  });

  it("filters children whose computed path is out of scope or whose drive differs (defence in depth)", async () => {
    fake.overrides.push((c) => {
      if (!c.u.pathname.endsWith("Past%20Projects/2023:/children")) return undefined;
      const ok = file("Past Projects/2023/Good.docx");
      const moved = { ...rawItem(file("Client Confidential/Moved.docx")) }; // parentReference says: outside scope
      const otherDrive = rawItem(file("Past Projects/2023/Other.docx", "x", { driveId: "b!other" }));
      const shortcut = rawItem(file("Past Projects/2023/Link.docx", "x", { remote: true }));
      const noFacet = rawItem(file("Past Projects/2023/Unknown.docx"), { omitFacet: true });
      return json({ value: [rawItem(ok), moved, otherDrive, shortcut, noFacet] });
    });
    const items = await makeClient().listFolder("Past Projects/2023");
    expect(items.map((i) => i.name)).toEqual(["Good.docx"]);
  });

  it("falls back to the verified folder path when a listed child omits parentReference", async () => {
    fake.overrides.push((c) => {
      if (!c.u.pathname.endsWith("Past%20Projects/2023:/children")) return undefined;
      const noRef = { ...rawItem(file("Past Projects/2023/Bare.docx")), parentReference: undefined };
      const wrongDrive = { ...rawItem(file("Past Projects/2023/Foreign.docx")), parentReference: { driveId: "b!other" } };
      return json({ value: [noRef, wrongDrive] });
    });
    const items = await makeClient().listFolder("Past Projects/2023");
    expect(items.map((i) => i.path)).toEqual(["Past Projects/2023/Bare.docx"]);
  });

  it("follows @odata.nextLink but caps at 5 pages", async () => {
    let n = 0;
    fake.overrides.push((c) => {
      if (!c.u.pathname.includes(":/children")) return undefined;
      n++;
      return json({
        value: [rawItem(file(`Past Projects/2023/f${n}.docx`))],
        "@odata.nextLink": `https://graph.microsoft.com/v1.0/drives/${DRIVE}/root:/Past%20Projects/2023:/children?$skiptoken=p${n}`,
      });
    });
    const items = await makeClient().listFolder("Past Projects/2023");
    expect(n).toBe(5);
    expect(items.map((i) => i.name)).toEqual(["f1.docx", "f2.docx", "f3.docx", "f4.docx", "f5.docx"]);
  });

  it("rejects when Graph's resolved metadata puts an allowed-looking path outside scope; never lists children", async () => {
    // Caller path is allowed by input, but the canonical location Graph reports is Client Confidential/2023.
    const elsewhere = folder("Client Confidential/2023");
    fake.overrides.push((c) =>
      c.u.pathname.endsWith("/root:/Past%20Projects/2023") ? json(rawItem(elsewhere)) : undefined,
    );
    const err = await failure(makeClient().listFolder("Past Projects/2023"));
    expect(err).toBeInstanceOf(NotAllowedError);
    expect(err.message).toBe("Item not found or outside the allowed folders.");
    expect(fake.calls.some((c) => c.u.pathname.includes("/children"))).toBe(false);
  });

  it("rejects when the resolved folder metadata is in another drive or has no determinable path; never lists children", async () => {
    for (const variant of [
      rawItem(folder("Past Projects/2023", { driveId: "b!other" })),
      rawItem(folder("Past Projects/2023"), { omitPath: true }),
      rawItem(folder("Past Projects/2023"), { omitDrive: true }),
    ]) {
      fake.overrides = [(c) => (c.u.pathname.endsWith("/root:/Past%20Projects/2023") ? json(variant) : undefined)];
      await expect(makeClient().listFolder("Past Projects/2023")).rejects.toBeInstanceOf(NotAllowedError);
    }
    expect(fake.calls.some((c) => c.u.pathname.includes("/children"))).toBe(false);
  });

  it("refuses to follow a pagination link to another host (and never sends it the bearer token)", async () => {
    fake.overrides.push((c) =>
      c.u.pathname.includes(":/children") && c.u.origin === "https://graph.microsoft.com"
        ? json({ value: [], "@odata.nextLink": "https://evil.example.com/next" })
        : undefined,
    );
    await expect(makeClient().listFolder("Past Projects/2023")).rejects.toBeInstanceOf(UpstreamError);
    expect(fake.calls.some((c) => c.u.host === "evil.example.com")).toBe(false);
  });
});

describe("getFile", () => {
  it("downloads by path: metadata first, then content via redirect", async () => {
    const { item, data } = await makeClient().getFile({ path: "/Past Projects/2023/Acme Pricing.docx" });
    expect(data.toString()).toBe("acme");
    expect(item).toMatchObject({ kind: "file", name: "Acme Pricing.docx", path: "Past Projects/2023/Acme Pricing.docx", size: 4 });

    const order = fake.graphCalls().map((c) => c.u.pathname);
    const metaIdx = order.indexOf(`/v1.0/drives/${DRIVE}/root:/Past%20Projects/2023/Acme%20Pricing.docx`);
    const contentIdx = order.indexOf(`/v1.0/drives/${DRIVE}/items/${item.id}/content`);
    expect(metaIdx).toBeGreaterThanOrEqual(0);
    expect(contentIdx).toBeGreaterThan(metaIdx);
  });

  it("downloads by id", async () => {
    const f = find("Shared Templates/Proposal.docx");
    const { item, data } = await makeClient().getFile({ id: f.id });
    expect(item.path).toBe("Shared Templates/Proposal.docx");
    expect(data.toString()).toBe("template");
  });

  it("handles a direct 200 content response (no redirect)", async () => {
    fake.contentMode = "direct";
    const { data } = await makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id });
    expect(data.toString()).toBe("template");
  });

  it("by id: out-of-scope item -> NotAllowedError and /content is NEVER requested", async () => {
    const secret = find("Client Confidential/Secret.docx");
    const err = await failure(makeClient().getFile({ id: secret.id }));
    expect(err).toBeInstanceOf(NotAllowedError);
    expect(err.message).toBe("Item not found or outside the allowed folders.");
    expect(fake.contentCalls()).toEqual([]);
  });

  it("by path: resolved metadata reporting an out-of-scope canonical path -> NotAllowedError, no content request", async () => {
    const elsewhere = file("Client Confidential/Secret.docx", "top secret");
    fake.overrides.push((c) =>
      c.u.pathname.endsWith("/root:/Past%20Projects/2023/Acme%20Pricing.docx") ? json(rawItem(elsewhere)) : undefined,
    );
    await expect(makeClient().getFile({ path: "Past Projects/2023/Acme Pricing.docx" })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("by id: lookalike sibling folder is out of scope", async () => {
    await expect(makeClient().getFile({ id: find("Past Projects 2/Lookalike.docx").id })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("by id: item from another drive -> NotAllowedError, no content request", async () => {
    const foreign = file("Past Projects/Foreign.docx", "x", { driveId: "b!other" });
    fake.items.push(foreign);
    await expect(makeClient().getFile({ id: foreign.id })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("by id: metadata without a determinable path fails closed", async () => {
    const hidden = file("Past Projects/Hidden.docx", "x", { hidePath: true });
    fake.items.push(hidden);
    await expect(makeClient().getFile({ id: hidden.id })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("by id: shortcut items fail closed", async () => {
    const s = file("Past Projects/Shortcut.docx", "x", { remote: true });
    fake.items.push(s);
    await expect(makeClient().getFile({ id: s.id })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("nonexistent id is indistinguishable from out-of-scope", async () => {
    const err = await failure(makeClient().getFile({ id: "does-not-exist" }));
    expect(err).toBeInstanceOf(NotAllowedError);
    expect(err.message).toBe("Item not found or outside the allowed folders.");
  });

  it.each(["", "a/b", "id with space", "x?$expand=y", "../x", "a".repeat(201), "ok\n"])(
    "rejects malformed id %j without any request",
    async (id) => {
      await expect(makeClient().getFile({ id })).rejects.toBeInstanceOf(InvalidPathError);
      expect(fake.calls).toEqual([]);
    },
  );

  it("by path: out-of-scope and traversal are rejected before any request", async () => {
    const client = makeClient();
    await expect(client.getFile({ path: "Client Confidential/Secret.docx" })).rejects.toBeInstanceOf(NotAllowedError);
    await expect(client.getFile({ path: "Past Projects/../Client Confidential/Secret.docx" })).rejects.toBeInstanceOf(InvalidPathError);
    expect(fake.calls).toEqual([]);
  });

  it("by path: nonexistent in-scope file -> NotAllowedError", async () => {
    await expect(makeClient().getFile({ path: "Past Projects/nope.docx" })).rejects.toBeInstanceOf(NotAllowedError);
  });

  it("rejects folders with InvalidPathError (after the scope check)", async () => {
    const client = makeClient();
    await expect(client.getFile({ path: "Past Projects/2023" })).rejects.toBeInstanceOf(InvalidPathError);
    await expect(client.getFile({ id: find("Past Projects/2023").id })).rejects.toBeInstanceOf(InvalidPathError);
    // An out-of-scope folder is still just "not allowed".
    await expect(client.getFile({ id: find("Client Confidential").id })).rejects.toBeInstanceOf(NotAllowedError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("oversized by metadata -> FileTooLargeError without any content request", async () => {
    const big = file("Past Projects/Big.bin", "x", { size: 5000 });
    fake.items.push(big);
    await expect(makeClient({ max: 1000 }).getFile({ id: big.id })).rejects.toBeInstanceOf(FileTooLargeError);
    expect(fake.contentCalls()).toEqual([]);
  });

  it("oversized by actual bytes (metadata lies) -> FileTooLargeError while streaming", async () => {
    const liar = file("Past Projects/Liar.bin", "y".repeat(1500), { size: 10 });
    fake.items.push(liar);
    await expect(makeClient({ max: 1000 }).getFile({ id: liar.id })).rejects.toBeInstanceOf(FileTooLargeError);
  });

  it("oversized by Content-Length header -> FileTooLargeError", async () => {
    const f = file("Past Projects/Declared.bin", "z", { size: 1 });
    fake.items.push(f);
    fake.overrides.push((c) =>
      c.u.host === DOWNLOAD_HOST ? new Response("z", { headers: { "content-length": "999999" } }) : undefined,
    );
    await expect(makeClient({ max: 1000 }).getFile({ id: f.id })).rejects.toBeInstanceOf(FileTooLargeError);
  });

  it("allows a file exactly at the limit", async () => {
    const f = file("Past Projects/Exact.bin", "e".repeat(1000));
    fake.items.push(f);
    const { data } = await makeClient({ max: 1000 }).getFile({ id: f.id });
    expect(data.length).toBe(1000);
  });

  it("follows the pre-authenticated redirect WITHOUT the Authorization header", async () => {
    await makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id });

    const content = fake.calls.find((c) => c.u.pathname.endsWith("/content"))!;
    expect(content.redirect).toBe("manual");
    expect(content.headers.get("authorization")).toBe("Bearer token-1");

    const download = fake.calls.filter((c) => c.u.host === DOWNLOAD_HOST);
    expect(download).toHaveLength(1);
    expect(download[0]!.headers.has("authorization")).toBe(false);
    // The bearer token only ever goes to Graph.
    for (const c of fake.calls) {
      if (c.u.origin !== "https://graph.microsoft.com") expect(c.headers.has("authorization")).toBe(false);
    }
  });

  it("refuses non-HTTPS download redirects and never forwards the token to them", async () => {
    fake.overrides.push((c) =>
      c.u.pathname.endsWith("/content") ? new Response(null, { status: 302, headers: { location: "http://insecure.example.com/x" } }) : undefined,
    );
    await expect(makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id })).rejects.toBeInstanceOf(UpstreamError);
    expect(fake.calls.some((c) => c.u.host === "insecure.example.com")).toBe(false);
  });

  it("gives up on redirect loops", async () => {
    fake.overrides.push((c) =>
      c.u.host === DOWNLOAD_HOST ? new Response(null, { status: 302, headers: { location: `https://${DOWNLOAD_HOST}/again` } }) : undefined,
    );
    await expect(makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id })).rejects.toBeInstanceOf(UpstreamError);
  });

  it("maps a failing download host to UpstreamError with the status only", async () => {
    fake.overrides.push((c) => (c.u.host === DOWNLOAD_HOST ? new Response(`denied ${SECRET}`, { status: 403 }) : undefined));
    const err = await failure(makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id }));
    expect(err).toBeInstanceOf(UpstreamError);
    expect(err.message).toContain("403");
    expect(err.message).not.toContain(SECRET);
  });
});

describe("HTTP error handling", () => {
  it("401/403 -> UpstreamError with the standard access-denied message", async () => {
    for (const status of [401, 403]) {
      fake.overrides = [(c) => (c.u.pathname.includes("/root:/") ? new Response("nope", { status }) : undefined)];
      const err = await failure(makeClient().listFolder("Past Projects"));
      expect(err).toBeInstanceOf(UpstreamError);
      expect(err.message).toBe("SharePoint access denied by Graph (check app permissions/consent)");
    }
  });

  it("drops the cached token after a 401 so the next call re-authenticates", async () => {
    let denied = true;
    fake.overrides.push((c) =>
      denied && c.u.pathname.includes("/root:/") ? new Response("nope", { status: 401 }) : undefined,
    );
    const client = makeClient();
    await expect(client.listFolder("Past Projects")).rejects.toBeInstanceOf(UpstreamError);
    denied = false;
    await client.listFolder("Past Projects");
    expect(fake.tokenCalls).toBe(2);
  });

  it("retries a 429 once, honouring Retry-After", async () => {
    let n = 0;
    fake.overrides.push((c) => {
      if (!c.u.pathname.includes("/root:/Past%20Projects")) return undefined;
      return ++n === 1 ? new Response("slow down", { status: 429, headers: { "retry-after": "0" } }) : undefined;
    });
    const items = await makeClient().listFolder("Past Projects");
    expect(items.map((i) => i.name)).toEqual(["2023"]);
    expect(fake.graphCalls().filter((c) => c.u.pathname.endsWith("/root:/Past%20Projects"))).toHaveLength(2);
  });

  it("retries a 503 once, then gives up with UpstreamError", async () => {
    fake.overrides.push((c) =>
      c.u.pathname.includes("/root:/") ? new Response("busy", { status: 503, headers: { "retry-after": "0" } }) : undefined,
    );
    const err = await failure(makeClient().listFolder("Past Projects"));
    expect(err).toBeInstanceOf(UpstreamError);
    expect(err.message).toContain("503");
    expect(fake.graphCalls().filter((c) => c.u.pathname.includes("/root:/"))).toHaveLength(2);
  });

  it("caps the Retry-After wait at 5 seconds", async () => {
    vi.useFakeTimers();
    let n = 0;
    fake.overrides.push((c) => {
      if (!c.u.pathname.includes("/root:/Past%20Projects")) return undefined;
      return ++n === 1 ? new Response("slow", { status: 429, headers: { "retry-after": "3600" } }) : undefined;
    });
    const p = makeClient().listFolder("Past Projects");
    await vi.advanceTimersByTimeAsync(5000);
    await expect(p).resolves.toHaveLength(1);
  });

  it("other non-2xx -> UpstreamError with the status code only (response body is never echoed)", async () => {
    fake.overrides.push((c) => (c.u.pathname.includes("/root:/") ? new Response(`<html>${SECRET} token-1</html>`, { status: 500 }) : undefined));
    const err = await failure(makeClient().listFolder("Past Projects"));
    expect(err).toBeInstanceOf(UpstreamError);
    expect(err.message).toBe("Graph request failed (HTTP 500).");
  });

  it("wraps network failures and timeouts in UpstreamError", async () => {
    const client = new GraphSharePointClient({
      graph: { tenantId: TENANT, clientId: CLIENT_ID, clientSecret: SECRET, siteId: SITE, driveId: DRIVE, searchRegion: "NAM" },
      scope: new Scope(["Past Projects"]),
      maxDownloadBytes: 1000,
      fetchImpl: (async () => {
        throw new TypeError(`connect ECONNREFUSED while sending client_secret=${SECRET}`);
      }) as typeof fetch,
    });
    const err = await failure(client.listFolder());
    expect(err).toBeInstanceOf(UpstreamError);
    expect(err.message).not.toContain(SECRET);
    expect(err.message).not.toContain("ECONNREFUSED");

    const timeout = new GraphSharePointClient({
      graph: { tenantId: TENANT, clientId: CLIENT_ID, clientSecret: SECRET, siteId: SITE, driveId: DRIVE, searchRegion: "NAM" },
      scope: new Scope(["Past Projects"]),
      maxDownloadBytes: 1000,
      fetchImpl: (async () => {
        throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
      }) as typeof fetch,
    });
    expect((await failure(timeout.listFolder())).message).toMatch(/timed out/);
  });

  describe("redirects on non-download calls", () => {
    const TRAP_HOST = "trap.example.com";
    const redirecting = (status: number, when: (c: Call) => boolean): void => {
      fake.overrides.push((c) =>
        when(c) ? new Response(null, { status, headers: { location: `https://${TRAP_HOST}/stolen?x=1` } }) : undefined,
      );
    };
    const operations: Array<[string, (c: Call) => boolean, (client: GraphSharePointClient) => Promise<unknown>]> = [
      ["metadata by path (listFolder)", (c) => c.u.pathname.endsWith("/root:/Past%20Projects"), (cl) => cl.listFolder("Past Projects")],
      ["children listing", (c) => c.u.pathname.endsWith(":/children"), (cl) => cl.listFolder("Past Projects/2023")],
      ["metadata by id (getFile)", (c) => /\/items\/[^/]+$/.test(c.u.pathname), (cl) => cl.getFile({ id: find("Shared Templates/Proposal.docx").id })],
      ["search POST", (c) => c.u.pathname.endsWith("/search/query"), (cl) => cl.search("acme", 5)],
      ["library lookup (search)", (c) => c.u.pathname === `/v1.0/drives/${DRIVE}`, (cl) => cl.search("acme", 5)],
    ];

    it.each([301, 302, 303, 307, 308])("a %i on any Graph JSON call -> UpstreamError, and the Location host is never contacted", async (status) => {
      for (const [, when, run] of operations) {
        fake.calls = [];
        fake.overrides = [];
        redirecting(status, when);
        const err = await failure(run(makeClient()));
        expect(err).toBeInstanceOf(UpstreamError);
        expect(err.message).toBe("Unexpected redirect from Graph.");
        expect(fake.calls.some((c) => c.u.host === TRAP_HOST)).toBe(false);
      }
    });

    it("a redirect from the token endpoint fails as UpstreamError and is not followed", async () => {
      redirecting(302, (c) => c.url === TOKEN_URL);
      const err = await failure(makeClient().listFolder());
      expect(err).toBeInstanceOf(UpstreamError);
      expect(err.message).toContain("302");
      expect(fake.calls.some((c) => c.u.host === TRAP_HOST)).toBe(false);
      expect(fake.graphCalls()).toEqual([]);
    });

    it("every request the client makes (token, JSON, content, download) is sent with redirect: 'manual'", async () => {
      const client = makeClient();
      await client.listFolder();
      await client.listFolder("Past Projects");
      await client.search("acme", 5);
      await client.getFile({ id: find("Shared Templates/Proposal.docx").id });
      expect(fake.calls.length).toBeGreaterThanOrEqual(8);
      expect(fake.calls.filter((c) => c.redirect !== "manual").map((c) => c.url)).toEqual([]);
    });
  });

  it("passes an AbortSignal timeout on every request", async () => {
    const signals: Array<AbortSignal | null | undefined> = [];
    const inner = fake.fetch;
    const client = new GraphSharePointClient({
      graph: { tenantId: TENANT, clientId: CLIENT_ID, clientSecret: SECRET, siteId: SITE, driveId: DRIVE, searchRegion: "NAM" },
      scope: new Scope(["Past Projects"]),
      maxDownloadBytes: 1000,
      fetchImpl: ((input: string | URL | Request, init?: RequestInit) => {
        signals.push(init?.signal);
        return inner(input, init);
      }) as typeof fetch,
    });
    await client.getFile({ id: find("Past Projects/2023/Acme Pricing.docx").id });
    expect(signals.length).toBeGreaterThanOrEqual(4); // token, metadata, content, download
    expect(signals.every((s) => s instanceof AbortSignal)).toBe(true);
  });
});

describe("secrets never leak into errors", () => {
  const ACCESS_TOKEN = "token-1";

  const scenarios: Array<[string, () => Promise<unknown>]> = [
    ["token endpoint rejects", async () => {
      fake.overrides.push((c) => (c.url === TOKEN_URL ? json({ error: "invalid_client", error_description: `bad ${SECRET}` }, 401) : undefined));
      return makeClient().listFolder();
    }],
    ["graph returns 403 echoing the token", async () => {
      fake.overrides.push((c) => (c.u.origin === "https://graph.microsoft.com" ? new Response(`Bearer ${ACCESS_TOKEN} ${SECRET}`, { status: 403 }) : undefined));
      return makeClient().listFolder();
    }],
    ["graph returns 500 echoing the token", async () => {
      fake.overrides.push((c) => (c.u.origin === "https://graph.microsoft.com" ? new Response(`Bearer ${ACCESS_TOKEN} ${SECRET}`, { status: 500 }) : undefined));
      return makeClient().search("acme", 5);
    }],
    ["graph returns 429 twice", async () => {
      fake.overrides.push((c) => (c.u.origin === "https://graph.microsoft.com" ? new Response(SECRET, { status: 429, headers: { "retry-after": "0" } }) : undefined));
      return makeClient().getFile({ path: "Past Projects/2023/Acme Pricing.docx" });
    }],
    ["graph returns invalid JSON", async () => {
      fake.overrides.push((c) => (c.u.origin === "https://graph.microsoft.com" ? new Response(`{${SECRET}`, { status: 200 }) : undefined));
      return makeClient().listFolder("Past Projects");
    }],
    ["download host fails", async () => {
      fake.overrides.push((c) => (c.u.host === DOWNLOAD_HOST ? new Response(SECRET, { status: 500 }) : undefined));
      return makeClient().getFile({ id: find("Shared Templates/Proposal.docx").id });
    }],
    ["scope violation", async () => makeClient().getFile({ id: find("Client Confidential/Secret.docx").id })],
    ["fetch throws with the secret in its message", async () => {
      const client = new GraphSharePointClient({
        graph: { tenantId: TENANT, clientId: CLIENT_ID, clientSecret: SECRET, siteId: SITE, driveId: DRIVE, searchRegion: "NAM" },
        scope: new Scope(["Past Projects"]),
        maxDownloadBytes: 1000,
        fetchImpl: (async () => {
          throw new Error(`${SECRET} ${ACCESS_TOKEN}`);
        }) as typeof fetch,
      });
      return client.search("acme", 5);
    }],
  ];

  it.each(scenarios)("%s", async (_name, run) => {
    const err = await failure(run());
    const rendered = `${err.name}: ${err.message}\n${err.stack ?? ""}`;
    expect(rendered).not.toContain(SECRET);
    expect(rendered).not.toContain(ACCESS_TOKEN);
    expect(rendered).not.toMatch(/bearer/i);
    expect((err as { cause?: unknown }).cause).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------------------------
// Links (Eli) and delegated sign-in

describe("resolveLink", () => {
  const sharesRoute = (resolve: (encoded: string) => Response | undefined) => (c: Call) => {
    const m = /^\/v1\.0\/shares\/([^/]+)\/driveItem$/.exec(c.u.pathname);
    return m ? resolve(m[1]!) : undefined;
  };
  const decodeShare = (encoded: string) => Buffer.from(encoded.replace(/^u!/, ""), "base64url").toString("utf8");

  it("resolves a sharing link through /shares and returns the in-scope item", async () => {
    const link = "https://contoso.sharepoint.com/:w:/s/Team/EabcDEF?e=xyz";
    let seen = "";
    fake.overrides.push(sharesRoute((enc) => ((seen = decodeShare(enc)), json(rawItem(find("Past Projects/2023/Acme Pricing.docx"))))));
    const item = await makeClient().resolveLink(link);
    expect(seen).toBe(link);
    expect(item).toMatchObject({ path: "Past Projects/2023/Acme Pricing.docx", kind: "file" });
  });

  it("re-reads by id when the shared item omits its path", async () => {
    fake.overrides.push(sharesRoute(() => json(rawItem(find("Past Projects/2023"), { omitPath: true }))));
    expect(await makeClient().resolveLink("https://contoso.sharepoint.com/:f:/s/Team/x")).toMatchObject({ path: "Past Projects/2023", kind: "folder" });
  });

  it("treats out-of-scope, other-drive and missing links as not found", async () => {
    fake.overrides.push(sharesRoute(() => json(rawItem(find("Client Confidential/Secret.docx")))));
    expect(await failure(makeClient().resolveLink("https://contoso.sharepoint.com/a"))).toBeInstanceOf(NotAllowedError);

    fake.overrides.length = 0;
    fake.overrides.push(sharesRoute(() => json(rawItem({ ...find("Past Projects/2023/Acme Pricing.docx"), driveId: "b!other" }, { omitPath: true }))));
    expect(await failure(makeClient().resolveLink("https://contoso.sharepoint.com/b"))).toBeInstanceOf(NotAllowedError);

    fake.overrides.length = 0;
    fake.overrides.push(sharesRoute(() => json({ error: { code: "itemNotFound" } }, 404)));
    expect(await failure(makeClient().resolveLink("https://contoso.sharepoint.com/c"))).toBeInstanceOf(NotAllowedError);
  });

  it("rejects non-SharePoint links before any network call", async () => {
    expect(await failure(makeClient().resolveLink("https://evil.example.com/x"))).toBeInstanceOf(InvalidPathError);
    expect(await failure(makeClient().resolveLink("http://contoso.sharepoint.com/x"))).toBeInstanceOf(InvalidPathError);
    expect(fake.graphCalls()).toEqual([]);
  });

  it("falls back to matching SharePoint's unique id for browser Doc.aspx links", async () => {
    const guid = "F2C2A739-13FE-48EF-B20E-17225A9A7124";
    const target = find("Past Projects/2023/Acme Pricing.docx");
    fake.overrides.push(sharesRoute(() => json({ error: { code: "invalidRequest" } }, 400)));
    fake.overrides.push((c) => {
      if (!/\/root\/search\(q=/.test(c.u.pathname)) return undefined;
      return json({
        value: [
          { ...rawItem(find("Shared Templates/Proposal.docx")), sharepointIds: { listItemUniqueId: "00000000-0000-0000-0000-000000000000" } },
          { ...rawItem(target), sharepointIds: { listItemUniqueId: guid.toLowerCase() } },
        ],
      });
    });
    const link =
      `https://contoso-my.sharepoint.com/:w:/r/personal/me/_layouts/15/Doc.aspx?sourcedoc=%7B${guid}%7D&file=Acme%20Pricing.docx&action=default`;
    expect(await makeClient().resolveLink(link)).toMatchObject({ path: target.path });
  });

  it("describeLink explains where a link points without the not-found masking", async () => {
    fake.overrides.push(sharesRoute(() => json(rawItem(find("Client Confidential/Secret.docx")))));
    expect(await makeClient().describeLink("https://contoso.sharepoint.com/a")).toEqual({
      found: true,
      name: "Secret.docx",
      path: "Client Confidential/Secret.docx",
      inLibrary: true,
      allowed: false,
    });
  });
});

describe("delegated mode", () => {
  function delegatedClient(over: { siteId?: string; driveId?: string } = {}) {
    return new GraphSharePointClient({
      graph: { auth: "delegated", tenantId: "organizations", clientId: CLIENT_ID, searchRegion: "NAM", ...over },
      scope: new Scope(["Past Projects"]),
      maxDownloadBytes: 1000,
      fetchImpl: fake.fetch,
      getAccessToken: async () => "user-token",
    });
  }

  it("uses the signed-in person's token and OneDrive, never the client-credentials endpoint", async () => {
    fake.overrides.push((c) => (c.u.pathname === "/v1.0/me/drive" ? json({ id: DRIVE, webUrl: LIB_URL }) : undefined));
    const items = await delegatedClient().listFolder("Past Projects/2023");
    expect(items.map((i) => i.name)).toEqual(["Acme Pricing.docx"]);
    expect(fake.tokenRequests()).toEqual([]);
    expect(fake.graphCalls().every((c) => c.headers.get("authorization") === "Bearer user-token")).toBe(true);
  });

  it("searches with drive search inside each allowed root and post-filters the hits", async () => {
    fake.overrides.push((c) => {
      if (!/\/root:\/Past%20Projects:\/search\(q=/.test(c.u.pathname)) return undefined;
      return json({ value: [rawItem(find("Past Projects/2023/Acme Pricing.docx")), rawItem(find("Client Confidential/Secret.docx"))] });
    });
    const hits = await delegatedClient({ driveId: DRIVE }).search("acme", 10);
    expect(hits.map((h) => h.path)).toEqual(["Past Projects/2023/Acme Pricing.docx"]);
    expect(fake.graphCalls().some((c) => c.u.pathname.endsWith("/search/query"))).toBe(false);
  });
});
