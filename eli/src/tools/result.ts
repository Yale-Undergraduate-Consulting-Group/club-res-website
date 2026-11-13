import { FileTooLargeError, InvalidPathError, NotAllowedError, UpstreamError } from "../sharepoint/errors.js";
import { UnsupportedFormatError } from "../sharepoint/extract.js";

/** Shown to the model for upstream/unknown failures. Deliberately vague: details go to stderr only. */
export const GENERIC_ERROR_MESSAGE = "SharePoint is temporarily unavailable or returned an unexpected error.";

function describeError(err: unknown): string {
  const name = err instanceof Error ? err.name : typeof err;
  const message = err instanceof Error ? err.message : String(err);
  // Name and message only, never the stack. Capped so a pathological message cannot flood the log.
  return `${name}: ${message}`.slice(0, 500);
}

/** Bad tool input. The message is written to be shown to the model. */
export class ToolInputError extends Error {
  override name = "ToolInputError";
}

/**
 * The text the model may see for an error from a tool body.
 *  - Errors whose messages are written to be shown to the model pass through.
 *  - Everything else (UpstreamError, bugs, non-Error throws) becomes the generic message and the
 *    real name/message is logged to stderr.
 */
export function safeErrorMessage(err: unknown, tool: string): string {
  if (
    err instanceof InvalidPathError ||
    err instanceof NotAllowedError ||
    err instanceof FileTooLargeError ||
    err instanceof UnsupportedFormatError ||
    err instanceof ToolInputError
  ) {
    return err.message;
  }
  const kind = err instanceof UpstreamError ? "upstream error" : "unexpected error";
  console.error(`[tool ${tool}] ${kind} - ${describeError(err)}`);
  return GENERIC_ERROR_MESSAGE;
}
