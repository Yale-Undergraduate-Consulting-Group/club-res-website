import http from "node:http";
import { readUsageLog } from "../stats.js";
import { LINK_TTL_MS, SESSION_TTL_MS, UsedLinks, makeToken, readCookie, readToken, sessionCookie, COOKIE_NAME } from "./auth.js";
import { RANGES, buildDashboard } from "./data.js";
import { dashboardPage, signedOutPage } from "./page.js";

export interface ConsoleDeps {
  /** HMAC key for sign-in links and sessions (ELI_CONSOLE_SECRET). */
  secret: string;
  /** Where people open the console, e.g. https://eli-console.example.org (ELI_CONSOLE_URL). */
  publicUrl: string;
  /** Board members and admins. Checked on every request, so removing someone takes effect at once. */
  viewers: ReadonlySet<string>;
  usageLogPath: string;
  timeZone: string;
  limits: { userPerHour: number; workspacePerDay: number };
  /** Display names for Slack user ids; ids it can't resolve are left out. */
  names: (ids: readonly string[]) => Promise<Map<string, string>>;
  log?: (line: string) => void;
  now?: () => number;
}

const HEADERS = {
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
  "X-Content-Type-Options": "nosniff",
  // The sign-in token is in the URL: never send it on to another site.
  "Referrer-Policy": "no-referrer",
  "Cache-Control": "no-store",
  "X-Frame-Options": "DENY",
};

/** A one-time sign-in link for a viewer, or undefined for anyone else. */
export function signInLink(deps: Pick<ConsoleDeps, "secret" | "publicUrl" | "viewers" | "now">, user: string): string | undefined {
  if (!deps.viewers.has(user)) return undefined;
  const token = makeToken(deps.secret, "link", user, deps.now?.() ?? Date.now(), LINK_TTL_MS);
  return `${deps.publicUrl.replace(/\/+$/, "")}/login?t=${encodeURIComponent(token)}`;
}

export function parseDays(q: string | null): number {
  if (q === "all") return 0;
  const n = Number(q);
  return (RANGES as readonly number[]).includes(n) ? n : 30;
}

/** The request handler on its own, for tests. Never throws. */
export function consoleHandler(deps: ConsoleDeps): http.RequestListener {
  const used = new UsedLinks();
  const log = deps.log ?? ((line) => console.error(line));
  const secure = deps.publicUrl.startsWith("https://");

  const send = (res: http.ServerResponse, status: number, body: string, extra: Record<string, string> = {}) => {
    res.writeHead(status, { ...HEADERS, "Content-Type": "text/html; charset=utf-8", ...extra });
    res.end(body);
  };
  const redirect = (res: http.ServerResponse, to: string, cookie?: string) => {
    res.writeHead(303, { ...HEADERS, Location: to, ...(cookie ? { "Set-Cookie": cookie } : {}) });
    res.end();
  };

  return async (req, res) => {
    try {
      const now = deps.now?.() ?? Date.now();
      const url = new URL(req.url ?? "/", "http://console.local");
      const method = req.method ?? "GET";

      if (url.pathname === "/healthz") {
        res.writeHead(200, { "Content-Type": "text/plain", "Cache-Control": "no-store" });
        return res.end("ok");
      }

      if (url.pathname === "/login" && method === "GET") {
        const link = readToken(deps.secret, "link", url.searchParams.get("t") ?? undefined, now);
        if (!link || !deps.viewers.has(link.user) || !used.use(link.nonce, link.expires, now)) {
          return send(res, 401, signedOutPage("That sign-in link has expired or was already used. Ask Eli for a new one."));
        }
        const session = makeToken(deps.secret, "session", link.user, now, SESSION_TTL_MS);
        log(`[eli] console sign-in: ${link.user}`);
        return redirect(res, "/", sessionCookie(session, { secure, maxAgeMs: SESSION_TTL_MS }));
      }

      if (url.pathname === "/logout" && method === "POST") {
        return redirect(res, "/", `${COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secure ? "; Secure" : ""}`);
      }

      if (url.pathname === "/" && (method === "GET" || method === "HEAD")) {
        const session = readToken(deps.secret, "session", readCookie(req.headers.cookie, COOKIE_NAME), now);
        if (!session || !deps.viewers.has(session.user)) return send(res, 200, signedOutPage());
        const { records } = await readUsageLog(deps.usageLogPath);
        const dash = buildDashboard(records, { now: new Date(now), days: parseDays(url.searchParams.get("days")), timeZone: deps.timeZone });
        const names = await deps.names([session.user, ...dash.people.map((p) => p.user)]);
        return send(
          res,
          200,
          dashboardPage(dash, { viewerName: names.get(session.user) ?? session.user, names, timeZone: deps.timeZone, limits: deps.limits }),
        );
      }

      send(res, 404, signedOutPage("Page not found."));
    } catch (err) {
      log(`[eli] console error: ${(err as Error).message}`.slice(0, 500));
      if (!res.headersSent) send(res, 500, signedOutPage("Something went wrong loading the console. Check Eli's log."));
      else res.end();
    }
  };
}

export async function startConsole(deps: ConsoleDeps, { port, host }: { port: number; host: string }): Promise<http.Server> {
  const server = http.createServer(consoleHandler(deps));
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => resolve());
  });
  return server;
}

/** Display names from Slack, cached for an hour. */
export function slackNames(client: { users: { info: (a: { user: string }) => Promise<unknown> } }) {
  const cache = new Map<string, { name: string; at: number }>();
  return async (ids: readonly string[]): Promise<Map<string, string>> => {
    const out = new Map<string, string>();
    await Promise.all(
      [...new Set(ids)].map(async (id) => {
        const hit = cache.get(id);
        if (hit && Date.now() - hit.at < 60 * 60 * 1000) return out.set(id, hit.name);
        try {
          const res = (await client.users.info({ user: id })) as {
            user?: { name?: string; real_name?: string; profile?: { display_name?: string; real_name?: string } };
          };
          const p = res.user?.profile;
          const name = p?.display_name || p?.real_name || res.user?.real_name || res.user?.name;
          if (name) {
            cache.set(id, { name, at: Date.now() });
            out.set(id, name);
          }
        } catch {
          // Unknown or deactivated: the console shows the id.
        }
      }),
    );
    return out;
  };
}
