import { InvalidPathError } from "./errors.js";

const MAX_LINK_LENGTH = 2048;

/**
 * Parse a pasted link. Accepts Slack's `<url>` / `<url|label>` wrapping. Only https URLs are accepted.
 * Throws InvalidPathError (safe to show to the model) for anything else.
 */
export function parseLink(raw: string): URL {
  if (typeof raw !== "string") throw new InvalidPathError("Link must be a string.");
  let s = raw.trim();
  const slack = /^<([^|>]+)(?:\|[^>]*)?>$/.exec(s);
  if (slack) s = slack[1]!;
  if (s.length === 0 || s.length > MAX_LINK_LENGTH) throw new InvalidPathError("Link is empty or too long.");
  let url: URL;
  try {
    url = new URL(s);
  } catch {
    throw new InvalidPathError("That is not a valid link.");
  }
  if (url.protocol !== "https:") throw new InvalidPathError("Only https links are supported.");
  if (url.username || url.password) throw new InvalidPathError("Links with embedded credentials are not supported.");
  return url;
}

/** Decode a URL path into library-relative segments ("" for the root). Throws InvalidPathError. */
export function decodeUrlPath(pathname: string): string {
  try {
    return pathname
      .split("/")
      .filter((seg) => seg.length > 0)
      .map((seg) => decodeURIComponent(seg))
      .join("/");
  } catch {
    throw new InvalidPathError("Link path is not valid.");
  }
}
