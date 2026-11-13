import { InvalidPathError, NotAllowedError } from "./errors.js";

// Characters SharePoint forbids in names. ":" additionally terminates Graph's `root:/path:` addressing.
const ILLEGAL_SEGMENT_CHARS = /[*:<>?|"]/;
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** True for ".", "..", "...", and percent-encoded (even double-encoded) variants. */
function isDotSegment(seg: string): boolean {
  const once = safeDecode(seg);
  const twice = safeDecode(once);
  return [seg, once, twice].some((c) => /^[.\s]+$/.test(c));
}

/**
 * Normalize a library-relative path. Returns "" for the library root.
 * Rejects (does not resolve) any traversal attempt, control chars and characters SharePoint forbids.
 * Accepts leading/trailing/duplicate slashes and backslashes as separators.
 */
export function normalizePath(input: string): string {
  if (typeof input !== "string") throw new InvalidPathError("Path must be a string.");
  if (CONTROL_CHARS.test(input)) throw new InvalidPathError("Path contains control characters.");
  // A single code point that NFC rewrites into a different one (U+212A KELVIN SIGN -> "K", U+212B ANGSTROM,
  // U+2126 OHM, ...) is canonically "equivalent" but is a DIFFERENT name to SharePoint. Normalizing it would let
  // a library folder named "WorK" pass for the allowed root "Work", so reject it instead. Legitimate
  // decomposed sequences ("E" + U+0301) are fine: each code point is stable on its own.
  for (const ch of input) {
    if (ch.normalize("NFC") !== ch) throw new InvalidPathError("Path contains non-normalizable characters.");
  }
  const segments = input
    .normalize("NFC")
    .replace(/\\/g, "/")
    .split("/")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  for (const seg of segments) {
    if (isDotSegment(seg)) throw new InvalidPathError("Path traversal is not allowed.");
    if (ILLEGAL_SEGMENT_CHARS.test(seg)) throw new InvalidPathError("Path contains illegal characters.");
  }
  return segments.join("/");
}

/**
 * Comparison key: ASCII letters are case-folded, everything else must match exactly (after NFC).
 * String#toLowerCase() applies full Unicode case mapping, under which different characters can collide
 * (e.g. U+212A KELVIN SIGN -> "k"; normalizePath rejects that one earlier, this is defence in depth).
 * Failing closed on non-ASCII case differences is the safe trade-off: a root with non-English letters
 * must be configured with the library's exact capitalization.
 */
function foldKey(path: string): string {
  return path.replace(/[A-Z]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 32));
}

/**
 * The single choke point for folder scoping. SharePoint paths are case-insensitive, so ASCII
 * comparisons are case-insensitive (see foldKey), and a root only matches on a folder boundary
 * ("Past Projects" does NOT admit "Past Projects 2").
 */
export class Scope {
  /** Normalized allowed roots, original casing, de-duplicated. Never contains "". */
  readonly roots: readonly string[];
  private readonly keys: readonly string[];

  constructor(folders: readonly string[]) {
    const seen = new Map<string, string>();
    for (const f of folders) {
      const n = normalizePath(f);
      if (n === "") {
        throw new Error(
          "Allowed folder resolves to the library root. Allowing the whole library is not supported; list specific folders.",
        );
      }
      seen.set(foldKey(n), n);
    }
    if (seen.size === 0) throw new Error("At least one allowed folder is required.");
    this.keys = [...seen.keys()];
    this.roots = [...seen.values()];
  }

  /** True if `path` is an allowed root or inside one. Never throws: invalid paths are not allowed. */
  isAllowed(path: string): boolean {
    let key: string;
    try {
      key = foldKey(normalizePath(path));
    } catch {
      return false;
    }
    return this.keys.some((r) => key === r || key.startsWith(r + "/"));
  }

  /**
   * Returns the normalized path or throws. Malformed/traversal input -> InvalidPathError
   * (safe to tell the model); well-formed but out of scope -> NotAllowedError.
   */
  assertAllowed(path: string): string {
    const n = normalizePath(path);
    if (!this.isAllowed(n)) throw new NotAllowedError();
    return n;
  }
}
