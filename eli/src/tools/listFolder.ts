import { z } from "zod";
import type { SharePointClient } from "../sharepoint/types.js";

export const LIST_FOLDER_TOOL = "sharepoint_list_folder";

export const LIST_FOLDER_DESCRIPTION =
  "Browse the club's approved past-project folders in SharePoint. With no `path`, lists the approved " +
  "top-level folders. With a `path`, lists the files and subfolders directly inside that folder. Paths " +
  "are library-relative, exactly as returned in the `path` field of search or list results (for example " +
  "\"Past Projects/2023\"). Use this to explore structure when keyword search is not enough, for example " +
  "to see every project from a given year or to find the other files next to a search hit. Folders " +
  "outside the approved set, and folders that do not exist, are both reported as not found. When you " +
  "mention an item to the user, cite its returned webUrl and never invent links.";

export const listFolderInputShape = {
  path: z
    .string()
    .max(1000)
    .optional()
    .describe("Library-relative folder path. Omit to list the approved top-level folders."),
};

/** Throws SharePoint-layer errors. */
export async function runListFolder(client: SharePointClient, { path }: { path?: string }) {
  const requested = path?.trim() ? path : undefined;
  const items = await client.listFolder(requested);
  return {
    path: requested ?? "",
    count: items.length,
    items: items.map((i) => ({
      id: i.id,
      name: i.name,
      kind: i.kind,
      path: i.path,
      webUrl: i.webUrl,
      size: i.size,
      lastModified: i.lastModified,
    })),
  };
}
