import { z } from "zod";
import { normalizePath } from "../sharepoint/scope.js";
import type { SharePointClient } from "../sharepoint/types.js";
import { ToolInputError } from "./result.js";

export const SEARCH_TOOL = "sharepoint_search";

/** Largest `top` the backends accept; also how many hits are fetched before a folder filter. */
const MAX_TOP = 25;

export const SEARCH_DESCRIPTION =
  "Full-text search over the club's approved past-project folders in SharePoint (proposals, decks, " +
  "final deliverables, write-ups). Use this first when someone asks whether the club has done a similar " +
  "project, wants examples for a client, industry or topic, or is looking for a particular document. " +
  "It matches file names and document contents and returns ranked hits, each with name, path, kind " +
  "(file or folder), webUrl, lastModified, size and a short snippet. Use short keyword queries (client, " +
  "industry, topic) rather than full sentences. Pass `folderPath` to search only inside one folder (for " +
  "example a folder someone linked). Results are limited to the approved past-project folders; " +
  "nothing else is visible. To read a hit, call sharepoint_get_document with its id; to browse around it, " +
  "call sharepoint_list_folder. When you tell the user about a project or document, cite the returned " +
  "webUrl exactly as given and never invent links.";

export const searchInputShape = {
  query: z.string().min(1).max(300).describe("Search keywords, 1 to 300 characters."),
  top: z
    .number()
    .int()
    .min(1)
    .max(MAX_TOP)
    .default(10)
    .describe("Maximum number of hits to return (1 to 25, default 10)."),
  folderPath: z
    .string()
    .max(1000)
    .optional()
    .describe("Optional library-relative folder path; only hits inside this folder are returned."),
};

export interface SearchInput {
  query: string;
  top: number;
  folderPath?: string;
}

function insideFolder(path: string, folder: string): boolean {
  const p = path.toLowerCase();
  const f = folder.toLowerCase();
  return p === f || p.startsWith(f + "/");
}

/** Throws SharePoint-layer errors or ToolInputError. */
export async function runSearch(client: SharePointClient, { query, top, folderPath }: SearchInput) {
  const q = query.trim();
  if (q.length === 0) throw new ToolInputError("query must not be blank.");
  const folder = folderPath?.trim() ? normalizePath(folderPath) : "";
  // The backend applies the allowed-folder scope; the folder filter only narrows it further, so over-fetch.
  let hits = await client.search(q, folder ? MAX_TOP : top);
  if (folder) hits = hits.filter((h) => insideFolder(h.path, folder)).slice(0, top);
  return {
    count: hits.length,
    results: hits.map((h) => ({
      id: h.id,
      name: h.name,
      path: h.path,
      kind: h.kind,
      webUrl: h.webUrl,
      lastModified: h.lastModified,
      size: h.size,
      snippet: h.snippet,
    })),
  };
}
