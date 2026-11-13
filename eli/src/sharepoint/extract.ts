import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";

/**
 * Raised when we cannot turn a file into text: unknown extension, or a file that is corrupt/encrypted/
 * otherwise unparseable. The message is always safe to show to the model (no stack traces, no parser
 * internals, no filesystem paths).
 */
export class UnsupportedFormatError extends Error {
  override name = "UnsupportedFormatError";
}

export interface ExtractResult {
  text: string;
  truncated: boolean;
  /** Length of the full extracted text before truncation. */
  totalChars: number;
  /** Lowercase extension without dot, e.g. "pptx". */
  format: string;
}

export interface ExtractOptions {
  /** Hard wall-clock limit for one office-format parse (default 20s). The worker is terminated when it expires. */
  timeoutMs?: number;
}

/** Plain-text formats: decoded as UTF-8 and returned as-is (HTML is deliberately NOT tag-stripped). */
const TEXT_FORMATS = new Set(["txt", "md", "csv", "json", "html"]);

/** Binary formats parsed by officeparser (PDF is handled by its bundled pdf.js; no extra dependency). */
const OFFICE_FORMATS = new Set<string>(["pptx", "docx", "xlsx", "pdf"]);

/**
 * Guard rails for hostile or accidental zip bombs and runaway parses.
 *
 * officeparser's parse is largely synchronous CPU/heap work, so it cannot be interrupted by an AbortSignal and a
 * pathological file can exhaust the heap. Office formats are therefore parsed in a short-lived worker thread
 * with its own heap cap: an out-of-memory or a runaway loop only ever takes down that worker, and the main
 * thread enforces a hard wall-clock timeout with worker.terminate().
 */
const PARSE_TIMEOUT_MS = 20_000;
const MAX_CONCURRENT_PARSES = 2;
/** Parses waiting for a worker slot beyond this are refused instead of queuing without bound. */
const MAX_QUEUED_PARSES = 64;
const DECOMPRESSION_LIMITS = {
  maxUncompressedBytes: 48 * 1024 * 1024,
  maxZipEntries: 5_000,
  maxTableCells: 500_000,
};
const WORKER_RESOURCE_LIMITS = { maxOldGenerationSizeMb: 512, maxYoungGenerationSizeMb: 64, stackSizeMb: 4 };

export function supportedExtensions(): string[] {
  return [...TEXT_FORMATS, ...OFFICE_FORMATS].sort();
}

/** Lowercase extension of the last path segment, without the dot ("" if none). */
export function fileExtension(fileName: string): string {
  return path.posix.extname(path.win32.basename(String(fileName))).slice(1).toLowerCase();
}

function displayName(fileName: string): string {
  // Only the base name, with control characters removed, so error messages can't carry paths or escape codes.
  return path.win32.basename(String(fileName)).replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 120) || "file";
}

/**
 * Extract plain text from a file's bytes.
 *
 * `maxChars` is measured in UTF-16 code units (JavaScript string length) and the cut never lands inside a
 * surrogate pair, so the returned text is at most `maxChars` long and always well-formed. `totalChars` is
 * the normalized length before truncation, in the same units.
 *
 * @throws UnsupportedFormatError for unknown extensions or files that cannot be parsed.
 */
export async function extractText(
  data: Buffer,
  fileName: string,
  maxChars: number,
  options: ExtractOptions = {},
): Promise<ExtractResult> {
  const format = fileExtension(fileName);
  const isText = TEXT_FORMATS.has(format);
  const isOffice = OFFICE_FORMATS.has(format);
  if (!isText && !isOffice) {
    throw new UnsupportedFormatError(`Unsupported file type: ${format ? "." + format : "(no extension)"}`);
  }

  let raw: string;
  if (isText) {
    raw = data.toString("utf8");
  } else {
    try {
      raw = await withParseSlot(() => parseInWorker(data, format, options.timeoutMs ?? PARSE_TIMEOUT_MS));
    } catch {
      // Deliberately drop the underlying error: parser messages can be noisy and are of no use to the model.
      throw new UnsupportedFormatError(`Could not extract text from ${displayName(fileName)}`);
    }
  }

  const text = normalizeText(raw);
  const limit = Number.isFinite(maxChars) ? Math.max(0, Math.floor(maxChars)) : text.length;
  if (text.length <= limit) return { text, truncated: false, totalChars: text.length, format };
  return { text: truncateAtCodePoint(text, limit), truncated: true, totalChars: text.length, format };
}

// ------------------------------------------------------------------ worker-based office parsing

/** Counters for tests and diagnostics. */
export interface ExtractorStats {
  /** Office parses currently running (each has one live worker). */
  active: number;
  /** Office parses waiting for a free slot. */
  queued: number;
  /** Highest value `active` has ever reached in this process. */
  peakActive: number;
  /** Workers started. */
  started: number;
  /** Parses killed by the hard timeout. */
  timedOut: number;
  /** Parses that failed for any other reason (corrupt input, out of memory, worker crash, queue full). */
  failed: number;
}

const stats: ExtractorStats = { active: 0, queued: 0, peakActive: 0, started: 0, timedOut: 0, failed: 0 };
const waiters: (() => void)[] = [];

export function extractorStats(): Readonly<ExtractorStats> {
  return { ...stats, queued: waiters.length };
}

/** Run `task` once fewer than MAX_CONCURRENT_PARSES others are running; excess callers queue in FIFO order. */
async function withParseSlot<T>(task: () => Promise<T>): Promise<T> {
  if (stats.active >= MAX_CONCURRENT_PARSES) {
    if (waiters.length >= MAX_QUEUED_PARSES) {
      stats.failed++;
      throw new Error("too many parses queued");
    }
    // The releasing task hands its slot straight to us, so `active` is not decremented in between.
    await new Promise<void>((resolve) => waiters.push(resolve));
  } else {
    stats.active++;
  }
  stats.peakActive = Math.max(stats.peakActive, stats.active);
  try {
    return await task();
  } finally {
    const next = waiters.shift();
    if (next) next();
    else stats.active--;
  }
}

/**
 * Source for the worker (plain CommonJS, run with `eval: true`). It is deliberately a string rather than a
 * separate file so it works unchanged under tsx, vitest and the compiled dist/ output, where Node cannot run a
 * TypeScript worker file. officeparser is loaded by absolute file URL, resolved on the main thread.
 */
const WORKER_SOURCE = `
const { parentPort, workerData } = require("node:worker_threads");
(async () => {
  try {
    const { moduleUrl, data, fileType, limits, timeoutMs } = workerData;
    const mod = await import(moduleUrl);
    const OfficeParser = mod.OfficeParser ?? (mod.default && mod.default.OfficeParser);
    if (!OfficeParser) throw new Error("officeparser export not found");
    const ast = await OfficeParser.parseOffice(Buffer.from(data.buffer, data.byteOffset, data.byteLength), {
      fileType,
      ocr: false,
      extractAttachments: false,
      decompressionLimits: limits,
      abortSignal: AbortSignal.timeout(timeoutMs),
      onWarning() {},
    });
    const out = await ast.to("text");
    if (typeof out.value !== "string") throw new Error("unexpected non-text output");
    parentPort.postMessage({ ok: true, text: out.value });
  } catch {
    parentPort.postMessage({ ok: false });
  }
})();
`;

let officeParserUrl: string | undefined;
function resolveOfficeParserUrl(): string {
  officeParserUrl ??= pathToFileURL(createRequire(import.meta.url).resolve("officeparser")).href;
  return officeParserUrl;
}

/**
 * Parse an office document in a worker thread. Resolves with the raw text; rejects on ANY failure (parse error,
 * out of memory, crash, timeout). The worker is always terminated, and awaited, before this settles, so the
 * caller's concurrency slot is only released once its memory is really gone.
 */
async function parseInWorker(data: Buffer, fileType: string, timeoutMs: number): Promise<string> {
  // Copy into a standalone ArrayBuffer and transfer it, rather than structured-cloning (or sharing a pooled Buffer).
  const copy = new Uint8Array(data.byteLength);
  copy.set(data);

  let timer: NodeJS.Timeout | undefined;
  const worker = new Worker(WORKER_SOURCE, {
    eval: true,
    workerData: { moduleUrl: resolveOfficeParserUrl(), data: copy, fileType, limits: DECOMPRESSION_LIMITS, timeoutMs },
    transferList: [copy.buffer],
    resourceLimits: WORKER_RESOURCE_LIMITS,
    // Keep anything the parser prints (pdf.js warnings, etc.) out of the host process's stdout/stderr.
    stdout: true,
    stderr: true,
  });
  worker.stdout.resume();
  worker.stderr.resume();
  stats.started++;

  try {
    return await new Promise<string>((resolve, reject) => {
      worker.once("message", (msg: unknown) => {
        const m = msg as { ok?: unknown; text?: unknown } | null;
        if (m && m.ok === true && typeof m.text === "string") resolve(m.text);
        else reject(new Error("parse failed"));
      });
      worker.on("error", (err) => reject(err)); // includes ERR_WORKER_OUT_OF_MEMORY; keep listening so a late error never goes unhandled
      worker.once("exit", () => reject(new Error("worker exited without a result")));
      timer = setTimeout(() => {
        stats.timedOut++;
        reject(new Error("parse timed out"));
      }, timeoutMs);
    });
  } catch (err) {
    if (!(err instanceof Error && err.message === "parse timed out")) stats.failed++;
    throw err;
  } finally {
    clearTimeout(timer);
    await worker.terminate().catch(() => undefined);
  }
}

// ------------------------------------------------------------------ text normalization

/** BOM/NUL/control-char cleanup, newline normalization, and collapsing runs of blank lines. */
export function normalizeText(input: string): string {
  return trimLineEnds(
    input
      .replace(/^\uFEFF/, "")
      .replace(/\r\n?/g, "\n")
      .replace(/[\u000B\u000C\u2028\u2029]/g, "\n")
      // NUL and the remaining C0 controls (keeping \t and \n), plus DEL.
      .replace(/[\u0000-\u0008\u000E-\u001F\u007F]/g, ""),
  )
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Strip trailing spaces/tabs from every line. Done by hand rather than with /[ \t]+$/gm, which backtracks
 * quadratically on a long run of spaces and would let a hostile text file stall the event loop.
 */
function trimLineEnds(text: string): string {
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    let end = line.length;
    while (end > 0) {
      const c = line.charCodeAt(end - 1);
      if (c !== 0x20 && c !== 0x09) break;
      end--;
    }
    if (end !== line.length) lines[i] = line.slice(0, end);
  }
  return lines.join("\n");
}

/** Cut to at most `limit` UTF-16 units without ever leaving half of a surrogate pair at the end. */
function truncateAtCodePoint(text: string, limit: number): string {
  let end = limit;
  if (end > 0 && end < text.length) {
    const last = text.charCodeAt(end - 1);
    const next = text.charCodeAt(end);
    const lastIsHigh = last >= 0xd800 && last <= 0xdbff;
    const nextIsLow = next >= 0xdc00 && next <= 0xdfff;
    if (lastIsHigh && nextIsLow) end -= 1;
  }
  return text.slice(0, end);
}
