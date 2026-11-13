import fs from "node:fs/promises";
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
  type ICachePlugin,
  type TokenCacheContext,
} from "@azure/msal-node";
import { UpstreamError } from "./errors.js";

/**
 * Signs in as ONE person (device-code flow) and keeps a refresh token in a local file, so Eli can read that
 * person's OneDrive/SharePoint files without a tenant-wide app permission. Whatever Eli reads is limited
 * further by the allowed-folder Scope and the configured drive in graph.ts; the token alone is broader.
 */

/** Read files the signed-in person can open. offline_access (refresh token) is added by MSAL itself. */
export const DELEGATED_SCOPES = ["Files.Read.All", "User.Read"];

export interface DelegatedAuthOptions {
  /** "organizations", a tenant id, or a domain such as "yale.edu". */
  tenantId: string;
  clientId: string;
  /** Where the MSAL token cache (including the refresh token) is stored. Keep it out of git. */
  cachePath: string;
}

const RELOGIN = "Run `npm run eli:microsoft -- login` to sign in to Microsoft again.";

function fileCachePlugin(path: string): ICachePlugin {
  return {
    async beforeCacheAccess(ctx: TokenCacheContext) {
      try {
        ctx.tokenCache.deserialize(await fs.readFile(path, "utf8"));
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      }
    },
    async afterCacheAccess(ctx: TokenCacheContext) {
      if (!ctx.cacheHasChanged) return;
      await fs.writeFile(path, ctx.tokenCache.serialize(), { encoding: "utf8", mode: 0o600 });
    },
  };
}

/** Plain-English explanations for the sign-in errors people actually hit at universities. */
export function explainSignInError(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  const code = /AADSTS(\d+)/.exec(text)?.[1];
  switch (code) {
    case "90094":
    case "90095":
    case "65001":
      return (
        "Your organization requires an administrator to approve this app before you can sign in with it " +
        "(AADSTS" + code + "). Your IT department controls this setting, so only they can change it."
      );
    case "700016":
      return (
        "Microsoft could not find this app in your organization (AADSTS700016). Check AZURE_CLIENT_ID, and if the " +
        "app is registered in a different directory, make sure it allows accounts in any organizational directory."
      );
    case "7000218":
      return (
        "The app registration does not allow device-code sign-in (AADSTS7000218). In the app's Authentication " +
        "page, turn on 'Allow public client flows'."
      );
    case "50020":
      return "This account cannot sign in to the app's directory (AADSTS50020). Set AZURE_TENANT_ID=organizations.";
    default:
      return text.slice(0, 500);
  }
}

export class DelegatedAuth {
  private readonly pca: PublicClientApplication;

  constructor(private readonly opts: DelegatedAuthOptions) {
    this.pca = new PublicClientApplication({
      auth: {
        clientId: opts.clientId,
        authority: `https://login.microsoftonline.com/${encodeURIComponent(opts.tenantId)}`,
      },
      cache: { cachePlugin: fileCachePlugin(opts.cachePath) },
    });
  }

  /** Interactive device-code sign-in. `show` receives the "go to … and enter code …" instruction. */
  async login(show: (message: string) => void): Promise<AccountInfo> {
    const result = await this.pca.acquireTokenByDeviceCode({
      scopes: DELEGATED_SCOPES,
      deviceCodeCallback: (r) => show(r.message),
    });
    if (!result?.account) throw new Error("Sign-in finished without an account.");
    return result.account;
  }

  async account(): Promise<AccountInfo | undefined> {
    const accounts = await this.pca.getTokenCache().getAllAccounts();
    return accounts[0];
  }

  /** A fresh access token from the cached refresh token. Never interactive. */
  async getAccessToken(): Promise<string> {
    const account = await this.account();
    if (!account) throw new UpstreamError(`Not signed in to Microsoft. ${RELOGIN}`);
    try {
      const result = await this.pca.acquireTokenSilent({ account, scopes: DELEGATED_SCOPES });
      if (!result?.accessToken) throw new UpstreamError(`Microsoft did not return an access token. ${RELOGIN}`);
      return result.accessToken;
    } catch (err) {
      if (err instanceof UpstreamError) throw err;
      if (err instanceof InteractionRequiredAuthError) {
        throw new UpstreamError(`The Microsoft sign-in has expired or was revoked. ${RELOGIN}`);
      }
      throw new UpstreamError("Could not refresh the Microsoft sign-in.");
    }
  }

  async logout(): Promise<void> {
    const cache = this.pca.getTokenCache();
    for (const account of await cache.getAllAccounts()) await cache.removeAccount(account);
  }
}
