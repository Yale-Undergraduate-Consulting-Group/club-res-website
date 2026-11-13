import fs from "node:fs/promises";
import { auth as googleAuth, drive as googleDrive, type drive_v3 } from "@googleapis/drive";
import { FileTooLargeError, InvalidPathError, NotAllowedError, UpstreamError } from "../../sharepoint/errors.js";
import { extractText } from "../../sharepoint/extract.js";
import { parseLink } from "../../sharepoint/links.js";

/**
 * Google Drive, read-only, through a service account. SECURITY MODEL: the service account can only see files and
 * folders that someone explicitly shared with its email address, so sharing IS the allow-list. Not-found and
 * not-shared look identical to the model.
 */

export const NOT_SHARED = "File or folder not found, or not shared with Eli's Google account.";
const FOLDER_MIME = "application/vnd.google-apps.folder";
const EXPORTS: Record<string, { mime: string; format: string }> = {
  "application/vnd.google-apps.document": { mime: "text/plain", format: "google-doc" },
  "application/vnd.google-apps.presentation": { mime: "text/plain", format: "google-slides" },
  "application/vnd.google-apps.spreadsheet": { mime: "text/csv", format: "google-sheet (first sheet, CSV)" },
};
const EXTENSION_FOR_MIME: Record<string, string> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "text/plain": "txt",
  "text/csv": "csv",
  "text/markdown": "md",
};
/** Google's export endpoint refuses files whose export is larger than 10 MB. */
const EXPORT_MAX_BYTES = 10 * 1024 * 1024;
const FILE_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,trashed";
const ID_RE = /^[A-Za-z0-9_-]{10,200}$/;

export interface DriveFile {
  id: string;
  name: string;
  mimeType: string;
  size?: number;
  modifiedTime?: string;
  webViewLink?: string;
  /** Path inside the linked folder, e.g. "2023/Acme/Final Deck.pptx". Only set by listTree. */
  path?: string;
}

/** The handful of Drive calls Eli makes. A seam for tests. */
export interface DriveApi {
  get(fileId: string): Promise<DriveFile | undefined>;
  list(query: string, pageToken?: string): Promise<{ files: DriveFile[]; nextPageToken?: string }>;
  download(fileId: string, maxBytes: number): Promise<Buffer>;
  exportAs(fileId: string, mimeType: string, maxBytes: number): Promise<Buffer>;
}

export type LinkTarget = { kind: "folder" | "file" | "unknown"; id: string };

/** Recognise Drive/Docs links (and bare ids). Throws InvalidPathError for anything else. */
export function parseDriveLink(input: string): LinkTarget {
  const raw = input.trim();
  if (ID_RE.test(raw)) return { kind: "unknown", id: raw };
  const url = parseLink(raw);
  const host = url.hostname.toLowerCase();
  if (host !== "drive.google.com" && host !== "docs.google.com") {
    throw new InvalidPathError("Only Google Drive links (drive.google.com or docs.google.com) are supported here.");
  }
  const path = url.pathname;
  let m: RegExpExecArray | null;
  if ((m = /\/folders\/([A-Za-z0-9_-]+)/.exec(path))) return check({ kind: "folder", id: m[1]! });
  if ((m = /\/(?:file|document|presentation|spreadsheets|forms)\/d\/([A-Za-z0-9_-]+)/.exec(path))) {
    return check({ kind: "file", id: m[1]! });
  }
  const id = url.searchParams.get("id");
  if (id) return check({ kind: "unknown", id });
  throw new InvalidPathError("Could not find a file or folder id in that Google Drive link.");
}

function check(t: LinkTarget): LinkTarget {
  if (!ID_RE.test(t.id)) throw new InvalidPathError("That Google Drive link has an invalid id.");
  return t;
}

const quote = (s: string): string => `'${s.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;

export interface DriveReaderOptions {
  maxDownloadBytes: number;
  maxFiles?: number;
  maxDepth?: number;
  cacheMs?: number;
  now?: () => number;
}

export class DriveReader {
  private readonly treeCache = new Map<string, { at: number; files: DriveFile[]; truncated: boolean }>();

  constructor(
    private readonly api: DriveApi,
    private readonly opts: DriveReaderOptions,
  ) {}

  private get now(): number {
    return (this.opts.now ?? Date.now)();
  }

  async resolve(link: string): Promise<DriveFile> {
    const target = parseDriveLink(link);
    const file = await this.api.get(target.id);
    if (!file) throw new NotAllowedError(NOT_SHARED);
    return file;
  }

  /** Every file under a folder (breadth-first), with paths. Bounded by depth and count; cached briefly. */
  async listTree(folder: DriveFile): Promise<{ files: DriveFile[]; truncated: boolean }> {
    if (folder.mimeType !== FOLDER_MIME) throw new InvalidPathError("That link is a file, not a folder.");
    const cached = this.treeCache.get(folder.id);
    if (cached && this.now - cached.at < (this.opts.cacheMs ?? 5 * 60_000)) return cached;

    const maxFiles = this.opts.maxFiles ?? 500;
    const maxDepth = this.opts.maxDepth ?? 4;
    const files: DriveFile[] = [];
    let truncated = false;
    let level: Array<{ id: string; prefix: string }> = [{ id: folder.id, prefix: "" }];
    for (let depth = 0; level.length && !truncated; depth++) {
      const next: typeof level = [];
      for (const dir of level) {
        let pageToken: string | undefined;
        do {
          const page = await this.api.list(`${quote(dir.id)} in parents and trashed = false`, pageToken);
          for (const f of page.files) {
            const path = dir.prefix + f.name;
            if (f.mimeType === FOLDER_MIME) {
              if (depth + 1 < maxDepth) next.push({ id: f.id, prefix: path + "/" });
              else truncated = true;
            } else if (files.length < maxFiles) {
              files.push({ ...f, path });
            } else {
              truncated = true;
            }
          }
          pageToken = page.nextPageToken;
        } while (pageToken && !truncated);
        if (truncated) break;
      }
      level = next;
    }
    const result = { at: this.now, files, truncated };
    this.treeCache.set(folder.id, result);
    return result;
  }

  /** Name matches plus Drive full-text matches, limited to files inside the folder tree. */
  async search(folder: DriveFile, query: string, top: number): Promise<DriveFile[]> {
    const q = query.trim();
    if (!q) throw new InvalidPathError("query must not be blank.");
    const { files } = await this.listTree(folder);
    const inTree = new Map(files.map((f) => [f.id, f]));
    const words = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1);

    const scored = new Map<string, number>();
    for (const f of files) {
      const hay = (f.path ?? f.name).toLowerCase();
      const hits = words.filter((w) => hay.includes(w)).length;
      if (hits) scored.set(f.id, hits * 2);
    }
    // Full-text search over the whole service-account view, then keep only files from this tree.
    let pageToken: string | undefined;
    for (let page = 0; page < 3; page++) {
      const res = await this.api.list(`fullText contains ${quote(q.slice(0, 200))} and trashed = false`, pageToken);
      for (const f of res.files) if (inTree.has(f.id)) scored.set(f.id, (scored.get(f.id) ?? 0) + 3);
      pageToken = res.nextPageToken;
      if (!pageToken) break;
    }
    return [...scored.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, top)
      .map(([id]) => inTree.get(id)!);
  }

  async read(fileIdOrLink: string, maxChars: number) {
    const file = await this.resolve(fileIdOrLink);
    if (file.mimeType === FOLDER_MIME) throw new InvalidPathError("That is a folder; use gdrive_open_link to list it.");
    const exp = EXPORTS[file.mimeType];
    if (exp) {
      const data = await this.api.exportAs(file.id, exp.mime, EXPORT_MAX_BYTES);
      const text = data.toString("utf8");
      return { file, format: exp.format, totalChars: text.length, truncated: text.length > maxChars, text: text.slice(0, maxChars) };
    }
    if (file.mimeType.startsWith("application/vnd.google-apps.")) {
      throw new InvalidPathError("Eli can read Google Docs, Slides and Sheets, but not this kind of Google file.");
    }
    if (file.size !== undefined && file.size > this.opts.maxDownloadBytes) {
      throw new FileTooLargeError(`File is too large to read (limit is ${Math.floor(this.opts.maxDownloadBytes / (1024 * 1024))} MB).`);
    }
    const data = await this.api.download(file.id, this.opts.maxDownloadBytes);
    const hasExt = /\.[A-Za-z0-9]{1,5}$/.test(file.name);
    const ext = EXTENSION_FOR_MIME[file.mimeType];
    const name = hasExt || !ext ? file.name : `${file.name}.${ext}`;
    const extracted = await extractText(data, name, maxChars);
    return { file, format: extracted.format, totalChars: extracted.totalChars, truncated: extracted.truncated, text: extracted.text };
  }
}

// ---------------------------------------------------------------------------------------------- real API

function toDriveFile(f: drive_v3.Schema$File): DriveFile | undefined {
  if (!f.id || !f.name || !f.mimeType || f.trashed) return undefined;
  const out: DriveFile = { id: f.id, name: f.name, mimeType: f.mimeType };
  if (f.size) out.size = Number(f.size);
  if (f.modifiedTime) out.modifiedTime = f.modifiedTime;
  if (f.webViewLink) out.webViewLink = f.webViewLink;
  return out;
}

function status(err: unknown): number | undefined {
  const e = err as { code?: unknown; status?: unknown; response?: { status?: unknown } };
  const s = e?.response?.status ?? e?.status ?? e?.code;
  return typeof s === "number" ? s : undefined;
}

function mapError(err: unknown): Error {
  const s = status(err);
  if (s === 404 || s === 403) return new NotAllowedError(NOT_SHARED);
  return new UpstreamError(`Google Drive request failed${s ? ` (HTTP ${s})` : ""}.`);
}

function limited(data: unknown, maxBytes: number): Buffer {
  const buf = Buffer.from(data as ArrayBuffer);
  if (buf.byteLength > maxBytes) throw new FileTooLargeError(`File exceeds the ${maxBytes} byte download limit.`);
  return buf;
}

export class GoogleDriveApi implements DriveApi {
  private readonly drive: drive_v3.Drive;

  constructor(keyFile: string) {
    const auth = new googleAuth.GoogleAuth({ keyFile, scopes: ["https://www.googleapis.com/auth/drive.readonly"] });
    this.drive = googleDrive({ version: "v3", auth });
  }

  async get(fileId: string): Promise<DriveFile | undefined> {
    try {
      const res = await this.drive.files.get({ fileId, fields: FILE_FIELDS, supportsAllDrives: true });
      return toDriveFile(res.data);
    } catch (err) {
      const mapped = mapError(err);
      if (mapped instanceof NotAllowedError) return undefined;
      throw mapped;
    }
  }

  async list(query: string, pageToken?: string) {
    try {
      const res = await this.drive.files.list({
        q: query,
        pageToken,
        pageSize: 200,
        fields: `nextPageToken, files(${FILE_FIELDS})`,
        supportsAllDrives: true,
        includeItemsFromAllDrives: true,
      });
      const files = (res.data.files ?? []).map(toDriveFile).filter((f): f is DriveFile => f !== undefined);
      return { files, nextPageToken: res.data.nextPageToken ?? undefined };
    } catch (err) {
      throw mapError(err);
    }
  }

  async download(fileId: string, maxBytes: number): Promise<Buffer> {
    try {
      const res = await this.drive.files.get(
        { fileId, alt: "media", supportsAllDrives: true },
        { responseType: "arraybuffer", maxContentLength: maxBytes },
      );
      return limited(res.data, maxBytes);
    } catch (err) {
      if (err instanceof FileTooLargeError) throw err;
      throw mapError(err);
    }
  }

  async exportAs(fileId: string, mimeType: string, maxBytes: number): Promise<Buffer> {
    try {
      const res = await this.drive.files.export({ fileId, mimeType }, { responseType: "arraybuffer", maxContentLength: maxBytes });
      return limited(res.data, maxBytes);
    } catch (err) {
      if (err instanceof FileTooLargeError) throw err;
      throw mapError(err);
    }
  }
}

/** The service account's email, shown at startup so people know whom to share folders with. */
export async function serviceAccountEmail(keyFile: string): Promise<string | undefined> {
  try {
    const parsed = JSON.parse(await fs.readFile(keyFile, "utf8")) as { client_email?: unknown };
    return typeof parsed.client_email === "string" ? parsed.client_email : undefined;
  } catch {
    return undefined;
  }
}
