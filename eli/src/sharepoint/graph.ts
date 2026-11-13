import type { GraphConfig } from "../config.js";
import { FileTooLargeError, InvalidPathError, NotAllowedError, UpstreamError } from "./errors.js";
import { parseLink } from "./links.js";
import { normalizePath, type Scope } from "./scope.js";
import type { DriveItemInfo, ItemRef, LinkResolver, SearchHit, SharePointClient } from "./types.js";

/**
 * Microsoft Graph backend (app-only, read-only).
 *
 * SECURITY MODEL: the Entra app has tenant-wide read (Graph /search/query cannot be scoped with
 * Sites.Selected), so THIS FILE is the only scope enforcement. Rules:
 *  - Every item returned, and every item whose bytes are fetched, must have (a) a driveId equal to the
 *    configured library and (b) a library-relative path that passes `scope.isAllowed`.
 *  - If either cannot be determined, the item is treated as not allowed.
 *  - The KQL path clause sent to search is only a ranking/recall optimisation; the post-filter is authoritative.
 *  - Never put tokens/secrets in error messages. Upstream response bodies are never echoed.
 */

export interface GraphClientOptions {
  graph: GraphConfig;
  scope: Scope;
  maxDownloadBytes: number;
  /** Injectable for tests. Defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /**
   * Delegated mode: returns an access token for the signed-in person (see delegatedAuth.ts). When set, the
   * client-credentials token request is never made.
   */
  getAccessToken?: () => Promise<string>;
}

const GRAPH = "https://graph.microsoft.com/v1.0";
const GRAPH_ORIGIN = "https://graph.microsoft.com";
const REQUEST_TIMEOUT_MS = 30_000;
const DOWNLOAD_TIMEOUT_MS = 60_000;
const MAX_RETRY_WAIT_MS = 5_000;
const TOKEN_EXPIRY_SKEW_MS = 60_000;
const MAX_PAGES = 5;
const MAX_REDIRECTS = 3;
const MAX_SEARCH_FETCH = 100;
const HIT_RESOLVE_CONCURRENCY = 5;
const ID_RE = /^[A-Za-z0-9!_\-=]{1,200}$/;

const ITEM_SELECT = "id,name,size,webUrl,lastModifiedDateTime,file,folder,remoteItem,parentReference";
const SEARCH_FIELDS = ["id", "name", "size", "webUrl", "lastModifiedDateTime", "file", "folder", "parentReference"];

// ---------------------------------------------------------------------------------------------
// small helpers

const graphUrl = (p: string): string => GRAPH + p;
const enc = encodeURIComponent;
const encodePath = (p: string): string => p.split("/").map(enc).join("/");
/** Site ids look like "host,guid,guid"; Graph documents them with literal commas. */
const encodeSiteId = (id: string): string => enc(id).replace(/%2C/gi, ",");

function asObj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
const isObj = (v: unknown): boolean => typeof v === "object" && v !== null;

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Release the connection without reading a body we don't need. */
async function drain(res: Response): Promise<void> {
  try {
    await res.body?.cancel();
  } catch {
    /* ignore */
  }
}

function retryDelayMs(res: Response): number {
  const header = res.headers.get("retry-after");
  let ms = 1000;
  if (header) {
    const secs = Number(header);
    if (Number.isFinite(secs)) ms = secs * 1000;
    else {
      const when = Date.parse(header);
      if (!Number.isNaN(when)) ms = when - Date.now();
    }
  }
  return Math.min(Math.max(ms, 0), MAX_RETRY_WAIT_MS);
}

/**
 * Make free text safe to embed inside `( ... )` in a KQL query. Everything that could be a KQL operator
 * or could close the group (quotes, parentheses, `prop:value`, wildcards, +/- prefixes, AND/OR/NOT...)
 * is removed, leaving plain keywords. Returns "" if nothing searchable is left.
 */
function sanitizeQuery(q: string): string {
  const tokens = q
    .normalize("NFC")
    .replace(/[^\p{L}\p{M}\p{N}\s_.'@&-]/gu, " ")
    .split(/\s+/)
    .map((t) => t.replace(/^-+/, ""))
    .filter((t) => t.length > 0 && !/^(?:AND|OR|NOT)$/i.test(t) && !/^(?:NEAR|ONEAR|XRANK|WORDS)$/.test(t));
  return tokens.join(" ").slice(0, 256).trim();
}

/** "<c0>foo</c0> bar<ddd/>" -> "foo bar…" */
function stripHighlight(s: string): string {
  return s
    .replace(/<\/?c\d+>/g, "")
    .replace(/<ddd\s*\/>/g, "…")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Graph's parentReference.path looks like "/drives/{id}/root:/A/B" (percent-encoded; "/drives/{id}/root:"
 * for items directly in the library root). Returns the decoded library-relative parent path, or undefined
 * if it is in any other shape (caller then fails closed or resolves the item another way).
 */
function parentPathOf(p: string | undefined): string | undefined {
  if (p === undefined) return undefined;
  const m = /^\/(?:drives\/[^/]+|drive)\/root:(?:\/(.*))?$/.exec(p);
  if (!m) return undefined;
  try {
    return decodeURIComponent(m[1] ?? "");
  } catch {
    return undefined;
  }
}

/**
 * Convert a raw Graph driveItem to DriveItemInfo, or undefined if we cannot positively establish
 * that it lives in `driveId`, what kind it is, and its library-relative path.
 *
 * `listedUnder` is only passed when the item came from `/children` of a folder we already verified: in
 * that case a missing parentReference.path/driveId is implied by the request itself.
 */
function toItem(raw: unknown, driveId: string, listedUnder?: string): DriveItemInfo | undefined {
  const r = asObj(raw);
  // Shortcuts ("remoteItem") point at another drive/site; never surface them.
  if (r.remoteItem !== undefined && r.remoteItem !== null) return undefined;
  const id = str(r.id);
  const name = str(r.name);
  const webUrl = str(r.webUrl);
  if (!id || !name || webUrl === undefined) return undefined;
  const isFolder = isObj(r.folder);
  const isFile = isObj(r.file);
  if (isFolder === isFile) return undefined; // neither (unknown) or both (nonsense)

  const pr = asObj(r.parentReference);
  const itemDrive = str(pr.driveId);
  if (itemDrive !== undefined ? itemDrive !== driveId : listedUnder === undefined) return undefined;
  const parent = parentPathOf(str(pr.path)) ?? listedUnder;
  if (parent === undefined) return undefined;
  let path: string;
  try {
    path = normalizePath(parent === "" ? name : `${parent}/${name}`);
  } catch {
    return undefined;
  }
  if (path === "") return undefined;

  const info: DriveItemInfo = { id, name, kind: isFolder ? "folder" : "file", path, webUrl };
  if (isFile) {
    if (typeof r.size === "number") info.size = r.size;
    const mime = str(asObj(r.file).mimeType);
    if (mime) info.mimeType = mime;
  }
  const modified = str(r.lastModifiedDateTime);
  if (modified) info.lastModified = modified;
  return info;
}

/** Read a response body, aborting as soon as it exceeds `max` bytes. */
async function readLimited(res: Response, max: number): Promise<Buffer> {
  const tooLarge = () => new FileTooLargeError(`File exceeds the ${max} byte download limit.`);
  const declared = Number(res.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > max) {
    await drain(res);
    throw tooLarge();
  }
  if (!res.body) return Buffer.alloc(0);
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > max) {
        await reader.cancel().catch(() => {});
        throw tooLarge();
      }
      chunks.push(value);
    }
  } catch (e) {
    if (e instanceof FileTooLargeError) throw e;
    throw new UpstreamError("Download from SharePoint was interrupted.");
  }
  return Buffer.concat(chunks, total);
}

interface RequestInitLite {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  /** Only the /content download may be answered with a 3xx (handled by the caller, without the bearer token). */
  allowRedirect?: boolean;
  timeoutMs?: number;
}

interface SiteDrive {
  id: string;
  webUrl?: string;
}

// ---------------------------------------------------------------------------------------------

export class GraphSharePointClient implements SharePointClient, LinkResolver {
  private readonly fetchImpl: typeof fetch;
  private token?: { value: string; expiresAt: number };
  private tokenInflight?: Promise<string>;
  private siteDrive?: Promise<SiteDrive>;
  private libraryUrl?: Promise<string>;

  constructor(readonly opts: GraphClientOptions) {
    this.fetchImpl = opts.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  // ---- public API -----------------------------------------------------------------------------

  async search(query: string, top: number): Promise<SearchHit[]> {
    const text = typeof query === "string" ? sanitizeQuery(query) : "";
    if (!text) return [];
    const want = Math.min(Math.max(Math.trunc(top) || 1, 1), MAX_SEARCH_FETCH);
    const driveId = await this.getDriveId();
    if (this.opts.graph.auth === "delegated") return this.searchDrive(text, want, driveId);
    const libraryUrl = await this.getLibraryUrl();

    // Server-side narrowing only; the post-filter below is what actually enforces scope. The user's text is
    // sanitized and parenthesised so it cannot widen or escape the path clause.
    const pathClause = this.opts.scope.roots
      .map((root) => `path:"${`${libraryUrl}/${root}`.replace(/"/g, "")}"`)
      .join(" OR ");
    const json = asObj(
      await this.getJson(graphUrl("/search/query"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          requests: [
            {
              entityTypes: ["driveItem"],
              query: { queryString: `(${text}) AND (${pathClause})` },
              from: 0,
              // Over-fetch: hits are post-filtered and many may be out of scope.
              size: Math.min(want * 3, MAX_SEARCH_FETCH),
              region: this.opts.graph.searchRegion,
              fields: SEARCH_FIELDS,
            },
          ],
        }),
      }),
    );

    const hits = asArr(json.value).flatMap((v) => asArr(asObj(v).hitsContainers).flatMap((c) => asArr(asObj(c).hits)));
    const out: SearchHit[] = [];
    for (let i = 0; i < hits.length && out.length < want; i += HIT_RESOLVE_CONCURRENCY) {
      const batch = await Promise.all(
        hits.slice(i, i + HIT_RESOLVE_CONCURRENCY).map((h) => this.resolveHit(asObj(h), driveId)),
      );
      for (const hit of batch) if (hit && out.length < want) out.push(hit);
    }
    return out;
  }

  async listFolder(path?: string): Promise<DriveItemInfo[]> {
    const { scope } = this.opts;
    const folder = path === undefined ? "" : normalizePath(path);
    if (folder !== "") scope.assertAllowed(folder); // reject before any network call
    const driveId = await this.getDriveId();

    if (folder === "") {
      const roots = await Promise.all(
        scope.roots.map(async (root) => {
          const item = await this.getItemByPath(driveId, root);
          return item && item.kind === "folder" && scope.isAllowed(item.path) ? item : undefined;
        }),
      );
      return roots.filter((r): r is DriveItemInfo => r !== undefined);
    }

    const self = await this.getItemByPath(driveId, folder);
    if (!self || !scope.isAllowed(self.path)) throw new NotAllowedError();
    if (self.kind !== "folder") throw new InvalidPathError("Not a folder.");

    const out: DriveItemInfo[] = [];
    let url: string | undefined = graphUrl(`/drives/${enc(driveId)}/root:/${encodePath(folder)}:/children?$select=${ITEM_SELECT}&$top=200`);
    for (let page = 0; url !== undefined && page < MAX_PAGES; page++) {
      const json = asObj(await this.getJson(url));
      for (const raw of asArr(json.value)) {
        const item = toItem(raw, driveId, self.path);
        if (item && scope.isAllowed(item.path)) out.push(item); // defence in depth
      }
      url = this.nextLink(json);
    }
    return out;
  }

  async getFile(ref: ItemRef): Promise<{ item: DriveItemInfo; data: Buffer }> {
    const { scope, maxDownloadBytes } = this.opts;
    // Validate input before any network call.
    let lookup: (driveId: string) => Promise<DriveItemInfo | undefined>;
    if (ref.path !== undefined) {
      const path = scope.assertAllowed(ref.path);
      lookup = (d) => this.getItemByPath(d, path);
    } else if (typeof ref.id === "string" && ID_RE.test(ref.id)) {
      const id = ref.id;
      lookup = (d) => this.getItemById(d, id);
    } else {
      throw new InvalidPathError("Invalid item id.");
    }

    const driveId = await this.getDriveId();
    const meta = await lookup(driveId);

    // Scope check on the *resolved* metadata, before a single content byte is requested.
    if (!meta || !scope.isAllowed(meta.path)) throw new NotAllowedError();
    if (meta.kind !== "file") throw new InvalidPathError("Not a file.");
    if (meta.size !== undefined && meta.size > maxDownloadBytes) {
      throw new FileTooLargeError(`File is ${meta.size} bytes, which exceeds the ${maxDownloadBytes} byte download limit.`);
    }
    const data = await this.download(driveId, meta.id);
    return { item: meta, data };
  }

  /**
   * Resolve a pasted SharePoint/OneDrive link, then apply the same rules as every other call: the item must be in
   * the configured library and inside the allowed folders. Links to other sites, other people's drives or
   * out-of-scope folders are indistinguishable from links to nothing.
   */
  async resolveLink(url: string): Promise<DriveItemInfo> {
    const found = await this.lookupLink(url);
    if (!found.item || !this.opts.scope.isAllowed(found.item.path)) throw new NotAllowedError();
    return found.item;
  }

  /**
   * Owner diagnostics only (`eli:microsoft -- check-link`; never exposed to the model or to Slack): says whether a
   * link resolves at all, whether it is in the configured library, and whether it is inside the allowed folders.
   */
  async describeLink(url: string): Promise<{ found: boolean; name?: string; path?: string; inLibrary: boolean; allowed: boolean }> {
    let found: Awaited<ReturnType<GraphSharePointClient["lookupLink"]>>;
    try {
      found = await this.lookupLink(url);
    } catch (e) {
      if (e instanceof NotAllowedError) return { found: false, inLibrary: false, allowed: false };
      throw e;
    }
    const { item, name } = found;
    return {
      found: true,
      name: item?.name ?? name,
      path: item?.path,
      inLibrary: item !== undefined,
      allowed: item !== undefined && this.opts.scope.isAllowed(item.path),
    };
  }

  /**
   * Find the item a link points at. `item` is set only when it is in OUR drive (scope not yet checked); `name` is
   * whatever Graph reported, for diagnostics. Throws NotAllowedError when the link resolves to nothing visible.
   */
  private async lookupLink(url: string): Promise<{ item?: DriveItemInfo; name?: string }> {
    const link = parseLink(url);
    if (!link.hostname.toLowerCase().endsWith(".sharepoint.com")) {
      throw new InvalidPathError("Only SharePoint/OneDrive links (https://<tenant>.sharepoint.com/...) are supported.");
    }
    const driveId = await this.getDriveId();

    let raw: Record<string, unknown> | undefined;
    let sharesError: unknown;
    try {
      const shareId = "u!" + Buffer.from(link.href, "utf8").toString("base64url");
      raw = asObj(await this.getJson(graphUrl(`/shares/${shareId}/driveItem?$select=${ITEM_SELECT}`)));
    } catch (e) {
      if (!(e instanceof NotAllowedError || e instanceof UpstreamError)) throw e;
      sharesError = e;
    }

    if (raw) {
      if (raw.remoteItem !== undefined && raw.remoteItem !== null) return { name: str(raw.name) };
      let item = toItem(raw, driveId);
      if (!item) {
        // Shared-item responses often omit parentReference.path. Re-read the item from OUR drive by id; an item
        // from another drive is never looked up there.
        const id = str(raw.id);
        const itemDrive = str(asObj(raw.parentReference).driveId);
        if (id && ID_RE.test(id) && (itemDrive === undefined || itemDrive === driveId)) {
          item = await this.getItemById(driveId, id);
        }
      }
      return { item, name: str(raw.name) };
    }

    // Browser address-bar links (".../_layouts/15/Doc.aspx?sourcedoc={GUID}&file=Name.xlsx") are not always
    // accepted by /shares. Find the file by name in OUR drive and match SharePoint's unique id exactly.
    const item = await this.findBySourceDoc(link, driveId);
    if (item) return { item };
    if (sharesError instanceof UpstreamError) throw sharesError;
    throw new NotAllowedError();
  }

  private async findBySourceDoc(link: URL, driveId: string): Promise<DriveItemInfo | undefined> {
    const guid = /^\{?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}?$/i.exec(
      link.searchParams.get("sourcedoc") ?? "",
    )?.[1];
    const fileName = link.searchParams.get("file")?.trim();
    if (!guid || !fileName || fileName.length > 400) return undefined;
    const q = fileName.replace(/'/g, "''");
    const json = asObj(
      await this.getJson(
        graphUrl(`/drives/${enc(driveId)}/root/search(q='${enc(q)}')?$select=${ITEM_SELECT},sharepointIds&$top=50`),
      ),
    );
    for (const raw of asArr(json.value)) {
      const r = asObj(raw);
      const unique = str(asObj(r.sharepointIds).listItemUniqueId)?.replace(/[{}]/g, "").toLowerCase();
      if (unique !== guid.toLowerCase()) continue;
      const id = str(r.id);
      return toItem(r, driveId) ?? (id && ID_RE.test(id) ? await this.getItemById(driveId, id) : undefined);
    }
    return undefined;
  }

  // ---- search helpers -------------------------------------------------------------------------

  /**
   * Delegated mode: search each allowed root with the drive search API. Microsoft's tenant search
   * (/search/query) wants a region for app tokens and a broader permission for user tokens; drive search needs
   * neither. The post-filter in resolveHit stays authoritative.
   */
  private async searchDrive(text: string, want: number, driveId: string): Promise<SearchHit[]> {
    const q = text.replace(/'/g, "''");
    const out: SearchHit[] = [];
    const seen = new Set<string>();
    for (const root of this.opts.scope.roots) {
      if (out.length >= want) break;
      const url = graphUrl(
        `/drives/${enc(driveId)}/root:/${encodePath(root)}:/search(q='${enc(q)}')?$select=${ITEM_SELECT}&$top=${Math.min(want * 3, MAX_SEARCH_FETCH)}`,
      );
      let json: Record<string, unknown>;
      try {
        json = asObj(await this.getJson(url));
      } catch (e) {
        if (e instanceof NotAllowedError) continue; // allowed root missing from this drive
        throw e;
      }
      const raws = asArr(json.value);
      for (let i = 0; i < raws.length && out.length < want; i += HIT_RESOLVE_CONCURRENCY) {
        const batch = await Promise.all(
          raws.slice(i, i + HIT_RESOLVE_CONCURRENCY).map((r) => this.resolveHit({ resource: r }, driveId)),
        );
        for (const hit of batch) {
          if (hit && !seen.has(hit.id) && out.length < want) {
            seen.add(hit.id);
            out.push(hit);
          }
        }
      }
    }
    return out;
  }


  private async resolveHit(hit: Record<string, unknown>, driveId: string): Promise<SearchHit | undefined> {
    const resource = asObj(hit.resource);
    const hitDrive = str(asObj(resource.parentReference).driveId);
    if (hitDrive !== undefined && hitDrive !== driveId) return undefined;

    let item = toItem(resource, driveId);
    if (!item) {
      // Search results often omit parentReference.path (or the file/folder facet). Ask Graph for the
      // authoritative metadata from OUR drive; if that can't pin the item down, drop it.
      const id = str(resource.id);
      if (!id || !ID_RE.test(id)) return undefined;
      item = await this.getItemById(driveId, id);
    }
    if (!item || !this.opts.scope.isAllowed(item.path)) return undefined;

    const summary = str(hit.hitHighlightedSummary) ?? str(hit.summary) ?? str(resource.hitHighlightedSummary);
    const snippet = summary ? stripHighlight(summary) : "";
    return snippet ? { ...item, snippet } : { ...item };
  }

  // ---- metadata helpers (404 -> undefined; caller decides) ------------------------------------

  private async getItemByPath(driveId: string, path: string): Promise<DriveItemInfo | undefined> {
    return this.getItem(graphUrl(`/drives/${enc(driveId)}/root:/${encodePath(path)}?$select=${ITEM_SELECT}`), driveId);
  }

  private async getItemById(driveId: string, id: string): Promise<DriveItemInfo | undefined> {
    return this.getItem(graphUrl(`/drives/${enc(driveId)}/items/${enc(id)}?$select=${ITEM_SELECT}`), driveId);
  }

  private async getItem(url: string, driveId: string): Promise<DriveItemInfo | undefined> {
    try {
      return toItem(await this.getJson(url), driveId);
    } catch (e) {
      if (e instanceof NotAllowedError) return undefined;
      throw e;
    }
  }

  /** Only follow pagination links on Graph itself: we attach the bearer token to them. */
  private nextLink(json: Record<string, unknown>): string | undefined {
    const next = str(json["@odata.nextLink"]);
    if (next === undefined) return undefined;
    let ok = false;
    try {
      ok = new URL(next).origin === GRAPH_ORIGIN;
    } catch {
      /* fallthrough */
    }
    if (!ok) throw new UpstreamError("Graph returned an unexpected pagination link.");
    return next;
  }

  // ---- drive resolution (lazy, cached) --------------------------------------------------------

  private getDriveId(): Promise<string> {
    const configured = this.opts.graph.driveId;
    if (configured) return Promise.resolve(configured);
    return this.getSiteDrive().then((d) => d.id);
  }

  private getSiteDrive(): Promise<SiteDrive> {
    const { siteId } = this.opts.graph;
    // Without a site (delegated mode only; config enforces this) the library is the signed-in person's OneDrive.
    const driveUrl = siteId ? `/sites/${encodeSiteId(siteId)}/drive?$select=id,webUrl` : "/me/drive?$select=id,webUrl";
    this.siteDrive ??= this.configJson(graphUrl(driveUrl))
      .then((raw) => {
        const id = str(raw.id);
        if (!id) throw new UpstreamError("Graph did not return a document library id for the configured site.");
        return { id, webUrl: str(raw.webUrl) };
      })
      .catch((e) => {
        this.siteDrive = undefined; // don't cache failures
        throw e;
      });
    return this.siteDrive;
  }

  /** Library URL, decoded, without trailing slash (used only for the KQL path clause). */
  private getLibraryUrl(): Promise<string> {
    this.libraryUrl ??= (async () => {
      let webUrl: string | undefined;
      if (!this.opts.graph.driveId) webUrl = (await this.getSiteDrive()).webUrl;
      if (!webUrl) {
        const driveId = await this.getDriveId();
        webUrl = str((await this.configJson(graphUrl(`/drives/${enc(driveId)}?$select=id,webUrl`))).webUrl);
      }
      if (!webUrl) throw new UpstreamError("Graph did not return a URL for the document library.");
      return safeDecode(webUrl).replace(/\/+$/, "");
    })().catch((e) => {
      this.libraryUrl = undefined;
      throw e;
    });
    return this.libraryUrl;
  }

  /** GET for site/drive configuration: a 404 here is a misconfiguration, not a scope decision. */
  private async configJson(url: string): Promise<Record<string, unknown>> {
    try {
      return asObj(await this.getJson(url));
    } catch (e) {
      if (e instanceof NotAllowedError) throw new UpstreamError("Configured SharePoint site or library was not found by Graph.");
      throw e;
    }
  }

  // ---- download -------------------------------------------------------------------------------

  private async download(driveId: string, itemId: string): Promise<Buffer> {
    let url = graphUrl(`/drives/${enc(driveId)}/items/${enc(itemId)}/content`);
    let res = await this.graphFetch(url, { allowRedirect: true });
    for (let hops = 0; res.status >= 300 && res.status < 400; hops++) {
      const location = res.headers.get("location");
      await drain(res);
      if (!location || hops >= MAX_REDIRECTS) throw new UpstreamError("Unexpected redirect while downloading file.");
      let next: URL;
      try {
        next = new URL(location, url);
      } catch {
        throw new UpstreamError("Unexpected redirect while downloading file.");
      }
      if (next.protocol !== "https:") throw new UpstreamError("Refusing to follow a non-HTTPS download redirect.");
      url = next.toString();
      // Graph answers with a pre-authenticated URL (often on another host). It must NOT receive our bearer token.
      res = await this.rawFetch(url, { timeoutMs: DOWNLOAD_TIMEOUT_MS });
      if (!res.ok && !(res.status >= 300 && res.status < 400)) {
        await drain(res);
        throw new UpstreamError(`File download failed (HTTP ${res.status}).`);
      }
    }
    return readLimited(res, this.opts.maxDownloadBytes);
  }

  // ---- HTTP plumbing --------------------------------------------------------------------------

  private getToken(): Promise<string> {
    if (this.opts.getAccessToken) return this.opts.getAccessToken();
    if (this.token && Date.now() < this.token.expiresAt) return Promise.resolve(this.token.value);
    // Coalesce concurrent refreshes into a single request.
    this.tokenInflight ??= this.requestToken().finally(() => {
      this.tokenInflight = undefined;
    });
    return this.tokenInflight;
  }

  private async requestToken(): Promise<string> {
    const { tenantId, clientId, clientSecret } = this.opts.graph;
    if (!clientSecret) throw new UpstreamError("No client secret is configured for app-only Graph access.");
    const res = await this.rawFetch(`https://login.microsoftonline.com/${enc(tenantId)}/oauth2/v2.0/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        grant_type: "client_credentials",
        scope: "https://graph.microsoft.com/.default",
      }).toString(),
    });
    if (!res.ok) {
      await drain(res); // the error body can echo request details; status only
      throw new UpstreamError(`Could not obtain a Graph access token (HTTP ${res.status}).`);
    }
    let body: Record<string, unknown>;
    try {
      body = asObj(await res.json());
    } catch {
      throw new UpstreamError("Token endpoint returned an invalid response.");
    }
    const value = str(body.access_token);
    const expiresIn = typeof body.expires_in === "number" ? body.expires_in : Number(body.expires_in);
    if (!value || !Number.isFinite(expiresIn)) throw new UpstreamError("Token endpoint returned an invalid response.");
    this.token = { value, expiresAt: Date.now() + expiresIn * 1000 - TOKEN_EXPIRY_SKEW_MS };
    return value;
  }

  private async getJson(url: string, init: RequestInitLite = {}): Promise<unknown> {
    const res = await this.graphFetch(url, init);
    try {
      return await res.json();
    } catch {
      throw new UpstreamError("Graph returned an invalid JSON response.");
    }
  }

  /** Authenticated Graph call with one Retry-After-aware retry and uniform error mapping. */
  private async graphFetch(url: string, init: RequestInitLite = {}): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
      const token = await this.getToken();
      const res = await this.rawFetch(url, { ...init, headers: { ...init.headers, Authorization: `Bearer ${token}` } });
      if ((res.status === 429 || res.status === 503) && attempt === 0) {
        const delay = retryDelayMs(res);
        await drain(res);
        await sleep(delay);
        continue;
      }
      if (res.ok) return res;

      await drain(res);
      if (res.status >= 300 && res.status < 400) {
        if (init.allowRedirect) return res;
        throw new UpstreamError("Unexpected redirect from Graph.");
      }
      if (res.status === 404) throw new NotAllowedError();
      if (res.status === 401 || res.status === 403) {
        if (res.status === 401) this.token = undefined; // force a fresh token next time
        throw new UpstreamError("SharePoint access denied by Graph (check app permissions/consent)");
      }
      if (res.status === 429 || res.status === 503) {
        throw new UpstreamError(`Graph is throttling or temporarily unavailable (HTTP ${res.status}).`);
      }
      throw new UpstreamError(`Graph request failed (HTTP ${res.status}).`);
    }
  }

  /** Plain fetch with timeout; failures are replaced by generic errors (never echo the underlying message). */
  private async rawFetch(url: string, init: RequestInitLite = {}): Promise<Response> {
    try {
      return await this.fetchImpl(url, {
        method: init.method ?? "GET",
        headers: init.headers,
        body: init.body,
        // Never auto-follow: redirects are handled explicitly so the bearer token can't travel with them.
        redirect: "manual",
        signal: AbortSignal.timeout(init.timeoutMs ?? REQUEST_TIMEOUT_MS),
      });
    } catch (e) {
      const timedOut = e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError");
      throw new UpstreamError(timedOut ? "Request to Microsoft services timed out." : "Network error contacting Microsoft services.");
    }
  }
}
