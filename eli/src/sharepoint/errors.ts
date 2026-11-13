/** Caller supplied a path that is malformed or attempts traversal. Safe to show to the model. */
export class InvalidPathError extends Error {
  override name = "InvalidPathError";
}

/**
 * Item is outside the allowed folders OR does not exist. These two cases are deliberately
 * indistinguishable so the model cannot probe for the existence of out-of-scope files.
 */
export class NotAllowedError extends Error {
  override name = "NotAllowedError";
  constructor(message = "Item not found or outside the allowed folders.") {
    super(message);
  }
}

/** File exceeds the configured download cap. */
export class FileTooLargeError extends Error {
  override name = "FileTooLargeError";
}

/** Upstream (Graph) failure. Message must not contain tokens or secrets. */
export class UpstreamError extends Error {
  override name = "UpstreamError";
}
