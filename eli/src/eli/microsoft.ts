import path from "node:path";
import { parseSharePointConfig, type SharePointConfig } from "../config.js";
import { DelegatedAuth, explainSignInError } from "../sharepoint/delegatedAuth.js";
import { createClient } from "../sharepoint/factory.js";
import { GraphSharePointClient } from "../sharepoint/graph.js";

/**
 * Owner-only Microsoft commands (never reachable from Slack):
 *   npm run eli:microsoft -- login             sign in with your school account (device code) and check the allowed folders
 *   npm run eli:microsoft -- logout            forget the saved sign-in
 *   npm run eli:microsoft -- check-link URL    say where a SharePoint/OneDrive link points and whether Eli may read it
 */

function delegatedConfig(): { sp: SharePointConfig; auth: DelegatedAuth } {
  const sp = parseSharePointConfig();
  if (sp.mode !== "graph" || sp.graph?.auth !== "delegated") {
    throw new Error("Set SHAREPOINT_MODE=graph and GRAPH_AUTH=delegated in .env first.");
  }
  const g = sp.graph;
  return {
    sp,
    auth: new DelegatedAuth({
      tenantId: g.tenantId,
      clientId: g.clientId,
      cachePath: path.resolve(g.tokenCachePath ?? ".msal-cache.json"),
    }),
  };
}

async function login(): Promise<void> {
  const { sp, auth } = delegatedConfig();
  let account;
  try {
    account = await auth.login((message) => console.log(`\n${message}\n`));
  } catch (err) {
    console.error(`\nSign-in failed: ${explainSignInError(err)}`);
    process.exit(1);
  }
  console.log(`Signed in as ${account.username}. The sign-in is saved in ${sp.graph?.tokenCachePath} (keep it private).`);

  const client = createClient({ sharepoint: sp });
  const roots = await client.listFolder();
  const found = new Set(roots.map((r) => r.path.toLowerCase()));
  console.log("\nAllowed folders (the only places Eli will read):");
  for (const folder of sp.allowedFolders) {
    console.log(`  ${found.has(folder.toLowerCase()) ? "✓ found  " : "✗ missing"}  ${folder}`);
  }
  if (found.size < sp.allowedFolders.length) {
    console.log("\nCreate the missing folder(s) at the top level of your OneDrive (My files), or fix SHAREPOINT_ALLOWED_FOLDERS.");
  }
}

async function logout(): Promise<void> {
  const { auth } = delegatedConfig();
  await auth.logout();
  console.log("Signed out. Eli can no longer read your OneDrive until you run `npm run eli:microsoft -- login` again.");
}

async function checkLink(url: string | undefined): Promise<void> {
  if (!url) {
    console.error("Usage: npm run eli:microsoft -- check-link 'https://...sharepoint.com/...'");
    process.exit(2);
  }
  const sp = parseSharePointConfig();
  const client = createClient({ sharepoint: sp });
  if (!(client instanceof GraphSharePointClient)) {
    try {
      const item = await client.resolveLink(url);
      console.log(`✓ Eli can read this (mock data): ${item.path}`);
    } catch (err) {
      console.log(`✗ Eli cannot open this link: ${(err as Error).message}`);
    }
    return;
  }
  const d = await client.describeLink(url);
  if (!d.found) {
    console.log("✗ Microsoft could not find anything at this link that your account can open.");
  } else if (!d.inLibrary) {
    console.log(`✗ "${d.name ?? "This item"}" exists, but it is not in your own OneDrive (it may be someone else's or a team site's).`);
    console.log("  Copy it into your allowed folder, or ask the owner to move it there.");
  } else if (!d.allowed) {
    console.log(`✗ "${d.name}" is in your OneDrive at "${d.path}", which is outside the allowed folders (${sp.allowedFolders.join(", ")}).`);
    console.log("  Move it into an allowed folder; the link keeps working after a move.");
  } else {
    console.log(`✓ Eli can read "${d.name}" (at "${d.path}").`);
  }
}

async function main(): Promise<void> {
  const [command, arg] = process.argv.slice(2);
  if (command === "login") return login();
  if (command === "logout") return logout();
  if (command === "check-link") return checkLink(arg);
  console.error("Usage: npm run eli:microsoft -- login | logout | check-link <url>");
  process.exit(2);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
