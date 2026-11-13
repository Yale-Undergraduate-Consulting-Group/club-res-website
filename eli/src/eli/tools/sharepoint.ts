import { betaZodTool } from "@anthropic-ai/sdk/helpers/beta/zod";
import type { BetaRunnableTool } from "@anthropic-ai/sdk/lib/tools/BetaRunnableTool";
import { ToolError } from "@anthropic-ai/sdk/lib/tools/ToolError";
import { z } from "zod";
import type { LinkResolver, SharePointClient } from "../../sharepoint/types.js";
import { GET_DOCUMENT_DESCRIPTION, GET_DOCUMENT_TOOL, getDocumentInputShape, runGetDocument } from "../../tools/getDocument.js";
import { LIST_FOLDER_DESCRIPTION, LIST_FOLDER_TOOL, listFolderInputShape, runListFolder } from "../../tools/listFolder.js";
import { GENERIC_ERROR_MESSAGE, safeErrorMessage } from "../../tools/result.js";
import { SEARCH_DESCRIPTION, SEARCH_TOOL, runSearch, searchInputShape } from "../../tools/search.js";

export const OPEN_LINK_TOOL = "sharepoint_open_link";

/**
 * Run a tool body and return compact JSON. Failures become a ToolError carrying only a message that is safe for
 * the model (see safeErrorMessage); details of unexpected errors go to stderr.
 */
export function jsonTool<I>(
  name: string,
  body: (input: I) => Promise<unknown>,
  genericMessage = GENERIC_ERROR_MESSAGE,
): (input: I) => Promise<string> {
  return async (input) => {
    try {
      return JSON.stringify(await body(input));
    } catch (err) {
      const message = safeErrorMessage(err, name);
      throw new ToolError(message === GENERIC_ERROR_MESSAGE ? genericMessage : message);
    }
  };
}

// The SDK's runnable-tool type is invariant in its input; the runner only needs the shared shape.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyRunnableTool = BetaRunnableTool<any>;

/** The SharePoint search/list/read tools plus a tool that opens a pasted link. */
export function sharePointTools(
  client: SharePointClient & LinkResolver,
  limits: { maxDownloadBytes: number },
): AnyRunnableTool[] {
  return [
    betaZodTool({
      name: OPEN_LINK_TOOL,
      description:
        "Open a SharePoint or OneDrive link that someone pasted (https://<tenant>.sharepoint.com/... or " +
        "https://<tenant>-my.sharepoint.com/...). Returns the linked item (name, kind, path, webUrl). For a folder " +
        "it also lists what is directly inside; for a file, read it next with sharepoint_get_document using the " +
        "returned id. Only items inside the folders shared with Eli can be opened; anything else, and links that " +
        "point nowhere, are reported as not found.",
      inputSchema: z.object({ url: z.string().min(1).max(2048).describe("The link exactly as pasted.") }),
      run: jsonTool(OPEN_LINK_TOOL, async ({ url }: { url: string }) => {
        const item = await client.resolveLink(url);
        const summary = {
          id: item.id,
          name: item.name,
          kind: item.kind,
          path: item.path,
          webUrl: item.webUrl,
          size: item.size,
          lastModified: item.lastModified,
        };
        if (item.kind === "folder") {
          const listing = await runListFolder(client, { path: item.path });
          return { item: summary, children: listing.items, childCount: listing.count };
        }
        return { item: summary, next: "Call sharepoint_get_document with this id to read the file." };
      }),
    }),
    betaZodTool({
      name: SEARCH_TOOL,
      description: SEARCH_DESCRIPTION,
      inputSchema: z.object(searchInputShape),
      run: jsonTool(SEARCH_TOOL, (input) => runSearch(client, input)),
    }),
    betaZodTool({
      name: LIST_FOLDER_TOOL,
      description: LIST_FOLDER_DESCRIPTION,
      inputSchema: z.object(listFolderInputShape),
      run: jsonTool(LIST_FOLDER_TOOL, (input) => runListFolder(client, input)),
    }),
    betaZodTool({
      name: GET_DOCUMENT_TOOL,
      description: GET_DOCUMENT_DESCRIPTION,
      inputSchema: z.object(getDocumentInputShape),
      run: jsonTool(GET_DOCUMENT_TOOL, (input) => runGetDocument(client, input, limits)),
    }),
  ];
}
