/**
 * The contract between the tools and a SharePoint backend (graph.ts, mock.ts).
 *
 * Invariants EVERY implementation must uphold (fail closed):
 *  - All paths are LIBRARY-RELATIVE: no leading/trailing slash, "" = library root,
 *    e.g. "Past Projects/2023/Acme Retail Pricing".
 *  - Nothing outside the configured allowed folders (see scope.ts) is ever returned from
 *    search/listFolder, and getFile never downloads bytes for an item outside them.
 *  - If an item's path cannot be determined, treat it as NOT allowed.
 *  - Out-of-scope and non-existent items are indistinguishable (NotAllowedError).
 */

export type ItemKind = "file" | "folder";

export interface DriveItemInfo {
  id: string;
  name: string;
  kind: ItemKind;
  /** Library-relative path of the item itself (parentPath + "/" + name). */
  path: string;
  webUrl: string;
  /** Bytes. Files only. */
  size?: number;
  /** ISO 8601. */
  lastModified?: string;
  mimeType?: string;
}

export interface SearchHit extends DriveItemInfo {
  /** Plain-text snippet (highlight markup stripped). */
  snippet?: string;
}

/** Address an item by opaque id OR by library-relative path. */
export type ItemRef = { id: string; path?: undefined } | { path: string; id?: undefined };

export interface SharePointClient {
  /** Full-text search, already restricted to allowed folders. `top` is pre-clamped by the caller (1..25). */
  search(query: string, top: number): Promise<SearchHit[]>;

  /**
   * List children of a folder. `path` undefined/"" => return the allowed root folders themselves.
   * Throws InvalidPathError / NotAllowedError.
   */
  listFolder(path?: string): Promise<DriveItemInfo[]>;

  /**
   * Resolve metadata, verify scope, THEN download. Throws InvalidPathError, NotAllowedError,
   * FileTooLargeError (checked against metadata size before downloading, and again on bytes read).
   */
  getFile(ref: ItemRef): Promise<{ item: DriveItemInfo; data: Buffer }>;
}

/**
 * Turns a link someone pasted (a SharePoint sharing link or item URL) into an item. Same fail-closed rules as
 * SharePointClient: a link to anything outside the allowed folders, or to nothing, throws NotAllowedError; a
 * string that is not a link to this kind of library throws InvalidPathError.
 */
export interface LinkResolver {
  resolveLink(url: string): Promise<DriveItemInfo>;
}
