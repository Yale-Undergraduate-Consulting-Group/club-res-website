import type { Stats } from "node:fs";
import fs, { type FileHandle } from "node:fs/promises";
import path from "node:path";
import { FileTooLargeError, InvalidPathError, NotAllowedError, UpstreamError } from "./errors.js";
import { extractText, fileExtension } from "./extract.js";
import { decodeUrlPath, parseLink } from "./links.js";
import { normalizePath, type Scope } from "./scope.js";
import type { DriveItemInfo, ItemKind, ItemRef, LinkResolver, SearchHit, SharePointClient } from "./types.js";

export interface MockClientOptions {
  /** Directory acting as the document-library root (contains e.g. "Past Projects/"). */
  rootDir: string;
  scope: Scope;
  maxDownloadBytes: number;
}

const MOCK_HOST = "mock.sharepoint.local";
const MOCK_ORIGIN = `https://${MOCK_HOST}/`;

/** Search guard rails: this is a dev/test backend, but keep a runaway fixture directory from hanging a request. */
const MAX_WALK_DEPTH = 16;
const MAX_WALK_ENTRIES = 10_000;
/** Largest file whose content we will read while searching (never larger than maxDownloadBytes either). */
const SEARCH_CONTENT_MAX_BYTES = 5 * 1024 * 1024;
const SEARCH_CONTENT_MAX_CHARS = 500_000;
/** Search reads content on a best-effort basis: a slow or hostile file must not stall a whole search for long. */
const SEARCH_PARSE_TIMEOUT_MS = 8_000;
const MAX_QUERY_TOKENS = 10;
const SNIPPET_BEFORE = 80;
const SNIPPET_AFTER = 160;

const STOPWORDS = new Set(["a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "with", "is", "are", "at", "by", "from", "as", "it"]);

const MIME_TYPES: Record<string, string> = {
  md: "text/markdown",
  txt: "text/plain",
  csv: "text/csv",
  json: "application/json",
  html: "text/html",
  pdf: "application/pdf",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

/** Opaque, stable item id: base64url of the library-relative path. */
function encodeId(libraryPath: string): string {
  return Buffer.from(libraryPath, "utf8").toString("base64url");
}

function decodeId(id: string): string {
  const bad = () => new InvalidPathError("Invalid item id.");
  if (typeof id !== "string" || id.length === 0 || id.length > 4096 || !/^[A-Za-z0-9_-]+$/.test(id)) throw bad();
  const decoded = Buffer.from(id, "base64url").toString("utf8");
  // Reject non-canonical encodings and anything that was not valid UTF-8 to begin with.
  if (decoded.includes("\uFFFD") || Buffer.from(decoded, "utf8").toString("base64url") !== id) throw bad();
  return decoded;
}

function webUrlFor(libraryPath: string): string {
  return MOCK_ORIGIN + libraryPath.split("/").map(encodeURIComponent).join("/");
}

function isHiddenName(name: string): boolean {
  return name.startsWith(".");
}

function compareNames(a: string, b: string): number {
  return a.localeCompare(b, "en", { sensitivity: "base", numeric: true }) || (a < b ? -1 : a > b ? 1 : 0);
}

interface Resolved {
  /** Normalized, scope-checked library-relative path (canonical casing where it can be established). */
  logical: string;
  /** Real filesystem path (symlinks resolved) that was verified to be inside the root and the allowed scope. */
  real: string;
  stat: Stats;
}

interface QueryToken {
  raw: string;
  re: RegExp;
}

function tokenize(query: string): QueryToken[] {
  if (typeof query !== "string") return [];
  const words = query.normalize("NFC").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  const seen = new Set<string>();
  const tokens: QueryToken[] = [];
  for (const w of words) {
    if (seen.has(w) || STOPWORDS.has(w) || (w.length < 2 && !/\d/.test(w))) continue;
    seen.add(w);
    // Match at the start of a word, so "pric" finds "pricing" but "go" does not light up the middle of "algorithm".
    tokens.push({ raw: w, re: new RegExp(`(?<![\\p{L}\\p{N}])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "giu") });
    if (tokens.length >= MAX_QUERY_TOKENS) break;
  }
  return tokens;
}

/** Up to `cap` match positions of `re` in `text`. */
function matchPositions(re: RegExp, text: string, cap: number): number[] {
  re.lastIndex = 0;
  const out: number[] = [];
  let m: RegExpExecArray | null;
  while (out.length < cap && (m = re.exec(text)) !== null) {
    out.push(m.index);
    if (m[0].length === 0) re.lastIndex++;
  }
  return out;
}

function cleanSnippet(s: string): string {
  return s
    .replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, "") // lone surrogates from slicing
    .replace(/[#*_`>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Pick the match position whose surrounding window contains the most distinct query tokens (earliest wins ties),
 * so a multi-word query lands on the passage where the words occur together.
 */
function bestSnippetPosition(positionsByToken: number[][]): number {
  const all = [...new Set(positionsByToken.flat())].sort((a, b) => a - b);
  let best = all[0] ?? 0;
  let bestCount = -1;
  for (const p of all) {
    const lo = Math.max(0, p - SNIPPET_BEFORE);
    const hi = p + SNIPPET_AFTER;
    const count = positionsByToken.filter((ps) => ps.some((q) => q >= lo && q <= hi)).length;
    if (count > bestCount) {
      best = p;
      bestCount = count;
    }
  }
  return best;
}

function makeSnippet(text: string, index: number): string {
  const start = Math.max(0, index - SNIPPET_BEFORE);
  const end = Math.min(text.length, index + SNIPPET_AFTER);
  let s = text.slice(start, end);
  // Snap to word boundaries so we don't start or end mid-word, and never split a surrogate pair.
  if (start > 0) s = s.replace(/^\S*\s/, "");
  if (end < text.length) s = s.replace(/\s\S*$/, "");
  return (start > 0 ? "… " : "") + cleanSnippet(s) + (end < text.length ? " …" : "");
}

export class MockSharePointClient implements SharePointClient, LinkResolver {
  private realRootCache?: string;
  private readonly contentCache = new Map<string, string>();

  constructor(readonly opts: MockClientOptions) {}

  // ---------------------------------------------------------------- public API

  async search(query: string, top: number): Promise<SearchHit[]> {
    const tokens = tokenize(query);
    const limit = Math.floor(top);
    if (tokens.length === 0 || !Number.isFinite(limit) || limit < 1) return [];
    const phrase = tokens.map((t) => t.raw).join(" ");

    const scored: { hit: SearchHit; score: number }[] = [];
    for await (const file of this.walkFiles()) {
      const name = path.posix.basename(file.logical);
      const folder = path.posix.dirname(file.logical);
      const content = await this.contentFor(file);

      let score = 0;
      let matched = 0;
      const positions: number[][] = [];
      for (const t of tokens) {
        let s = 0;
        if (matchPositions(t.re, name, 1).length > 0) s += 4;
        if (matchPositions(t.re, folder, 1).length > 0) s += 2;
        if (content) {
          const found = matchPositions(t.re, content, 5);
          s += found.length;
          if (found.length > 0) positions.push(found);
        }
        if (s > 0) {
          matched++;
          score += s;
        }
      }
      if (matched === 0) continue;
      score += matched * 3;
      if (tokens.length > 1) {
        if (matched === tokens.length) score += 5;
        const haystack = (content ?? "").toLowerCase();
        if (haystack.includes(phrase) || `${folder}/${name}`.toLowerCase().includes(phrase)) score += 4;
      }

      // Snippet around the best match; for name/path-only matches fall back to the start of the document.
      const snippet = content ? makeSnippet(content, positions.length > 0 ? bestSnippetPosition(positions) : 0) || undefined : undefined;
      const item = this.toItem(file.logical, file.stat, "file");
      scored.push({ hit: snippet ? { ...item, snippet } : item, score });
    }

    scored.sort((a, b) => b.score - a.score || compareNames(a.hit.name, b.hit.name) || compareNames(a.hit.path, b.hit.path));
    return scored.slice(0, limit).map((s) => s.hit);
  }

  async listFolder(folderPath?: string): Promise<DriveItemInfo[]> {
    const n = normalizePath(folderPath ?? "");
    if (n === "") return this.listRoots();

    const dir = await this.resolveExisting(n);
    if (!dir.stat.isDirectory()) throw new InvalidPathError("Not a folder.");

    const items: DriveItemInfo[] = [];
    for (const child of await this.readChildren(dir)) {
      items.push(this.toItem(child.logical, child.stat, child.stat.isDirectory() ? "folder" : "file"));
    }
    return items.sort(
      (a, b) => (a.kind === b.kind ? 0 : a.kind === "folder" ? -1 : 1) || compareNames(a.name, b.name),
    );
  }

  async getFile(ref: ItemRef): Promise<{ item: DriveItemInfo; data: Buffer }> {
    let requested: string;
    if (typeof ref?.id === "string") requested = decodeId(ref.id);
    else if (typeof ref?.path === "string") requested = ref.path;
    else throw new InvalidPathError("Provide an item id or a path.");

    const found = await this.resolveExisting(requested);
    if (!found.stat.isFile()) throw new InvalidPathError("Not a file.");
    const max = this.opts.maxDownloadBytes;
    this.assertSize(found.stat.size, max);

    // Stat and read through the same handle so a swap between the check and the read cannot sneak a bigger file in.
    let handle: FileHandle;
    try {
      handle = await fs.open(found.real, "r");
    } catch {
      throw new NotAllowedError();
    }
    try {
      const st = await handle.stat();
      this.assertSize(st.size, max);
      const data = await handle.readFile();
      this.assertSize(data.length, max);
      return { item: this.toItem(found.logical, st, "file"), data };
    } finally {
      await handle.close();
    }
  }

  /** Maps the mock library's own webUrls (https://mock.sharepoint.local/<path>) back to items. */
  async resolveLink(url: string): Promise<DriveItemInfo> {
    const u = parseLink(url);
    if (u.hostname !== MOCK_HOST) {
      throw new InvalidPathError(`Only links to the mock library (https://${MOCK_HOST}/...) work in mock mode.`);
    }
    const logical = decodeUrlPath(u.pathname);
    if (logical === "") throw new NotAllowedError();
    const found = await this.resolveExisting(logical);
    return this.toItem(found.logical, found.stat, found.stat.isDirectory() ? "folder" : "file");
  }

  // ---------------------------------------------------------------- resolution and scope enforcement

  private assertSize(size: number, max: number): void {
    if (size > max) {
      throw new FileTooLargeError(`File is too large to download (${size} bytes; the limit is ${max} bytes).`);
    }
  }

  private async realRoot(): Promise<string> {
    if (!this.realRootCache) {
      try {
        this.realRootCache = await fs.realpath(path.resolve(this.opts.rootDir));
      } catch {
        throw new UpstreamError("Mock data directory is not available.");
      }
    }
    return this.realRootCache;
  }

  /**
   * The single choke point for touching the filesystem. Normalizes, scope-checks the logical path, resolves
   * symlinks with realpath, then requires the REAL location to be inside the library root AND still inside the
   * allowed scope (a symlink from "Past Projects" into a sibling folder is refused). Every failure, including
   * "does not exist", is NotAllowedError so out-of-scope and missing items look identical.
   * Invalid input (traversal, illegal characters) is InvalidPathError, as in scope.ts.
   */
  private async resolveExisting(input: string): Promise<Resolved> {
    const normalized = this.opts.scope.assertAllowed(input);
    const segments = normalized.split("/");
    if (segments.some(isHiddenName)) throw new NotAllowedError();

    const root = await this.realRoot();
    const target = path.join(root, ...segments);
    if (!target.startsWith(root + path.sep)) throw new NotAllowedError();

    let real: string;
    let stat: Stats;
    try {
      real = await fs.realpath(target);
      const rel = path.relative(root, real);
      if (rel === "" || rel === ".." || rel.startsWith(".." + path.sep) || path.isAbsolute(rel)) throw new NotAllowedError();
      const relPosix = rel.split(path.sep).join("/").normalize("NFC");
      if (relPosix.split("/").some(isHiddenName) || !this.opts.scope.isAllowed(relPosix)) throw new NotAllowedError();
      stat = await fs.stat(real);
      // Prefer the on-disk spelling when the path has no symlinks in it (so ids don't depend on how the caller cased it).
      const logical = relPosix.toLowerCase() === normalized.toLowerCase() ? relPosix : normalized;
      return { logical, real, stat };
    } catch (err) {
      if (err instanceof NotAllowedError) throw err;
      throw new NotAllowedError();
    }
  }

  private async readChildren(dir: Resolved): Promise<Resolved[]> {
    let names: string[];
    try {
      names = await fs.readdir(dir.real);
    } catch {
      throw new NotAllowedError();
    }
    const out: Resolved[] = [];
    for (const name of names) {
      if (isHiddenName(name)) continue;
      try {
        // Re-resolve every child through the same checks; anything out of scope (e.g. a symlink escape) is skipped.
        out.push(await this.resolveExisting(`${dir.logical}/${name}`));
      } catch {
        continue;
      }
    }
    return out;
  }

  private async listRoots(): Promise<DriveItemInfo[]> {
    const items: DriveItemInfo[] = [];
    for (const root of this.opts.scope.roots) {
      try {
        const r = await this.resolveExisting(root);
        if (r.stat.isDirectory()) items.push(this.toItem(r.logical, r.stat, "folder"));
      } catch {
        continue; // allowed root not present in this library: nothing to show
      }
    }
    return items.sort((a, b) => compareNames(a.name, b.name));
  }

  /** Depth-first walk over the allowed roots only. Never looks outside them. */
  private async *walkFiles(): AsyncGenerator<Resolved> {
    const visitedDirs = new Set<string>();
    let seen = 0;
    const stack: { dir: Resolved; depth: number }[] = [];
    for (const rootPath of [...this.opts.scope.roots].reverse()) {
      try {
        const r = await this.resolveExisting(rootPath);
        if (r.stat.isDirectory()) stack.push({ dir: r, depth: 0 });
      } catch {
        continue;
      }
    }
    while (stack.length > 0) {
      const { dir, depth } = stack.pop()!;
      if (visitedDirs.has(dir.real)) continue;
      visitedDirs.add(dir.real);
      const children = (await this.readChildren(dir)).sort((a, b) => compareNames(b.logical, a.logical));
      for (const child of children) {
        if (++seen > MAX_WALK_ENTRIES) return;
        if (child.stat.isDirectory()) {
          if (depth + 1 <= MAX_WALK_DEPTH) stack.push({ dir: child, depth: depth + 1 });
        } else if (child.stat.isFile()) {
          yield child;
        }
      }
    }
  }

  /** Extracted text for a file already vetted by resolveExisting, or undefined if it has none. Cached. */
  private async contentFor(file: Resolved): Promise<string | undefined> {
    const ext = fileExtension(file.logical);
    if (!(ext in MIME_TYPES)) return undefined;
    if (file.stat.size > Math.min(SEARCH_CONTENT_MAX_BYTES, this.opts.maxDownloadBytes)) return undefined;
    const key = `${file.real}\u0000${file.stat.size}\u0000${file.stat.mtimeMs}`;
    const cached = this.contentCache.get(key);
    if (cached !== undefined) return cached || undefined;
    let text = "";
    try {
      const data = await fs.readFile(file.real);
      text = (await extractText(data, file.logical, SEARCH_CONTENT_MAX_CHARS, { timeoutMs: SEARCH_PARSE_TIMEOUT_MS })).text;
    } catch {
      text = ""; // unreadable or corrupt: still searchable by name and path
    }
    this.contentCache.set(key, text);
    return text || undefined;
  }

  private toItem(logical: string, stat: Stats, kind: ItemKind): DriveItemInfo {
    const name = path.posix.basename(logical);
    const item: DriveItemInfo = {
      id: encodeId(logical),
      name,
      kind,
      path: logical,
      webUrl: webUrlFor(logical),
      lastModified: stat.mtime.toISOString(),
    };
    if (kind === "file") {
      item.size = stat.size;
      const mime = MIME_TYPES[fileExtension(name)];
      if (mime) item.mimeType = mime;
    }
    return item;
  }
}
