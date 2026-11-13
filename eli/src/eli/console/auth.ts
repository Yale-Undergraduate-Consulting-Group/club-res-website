import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const LINK_TTL_MS = 10 * 60 * 1000;
export const SESSION_TTL_MS = 12 * 60 * 60 * 1000;
export const COOKIE_NAME = "eli_console";

type Kind = "link" | "session";

function sign(secret: string, payload: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

/** `kind.user.expiresMs.nonce.signature`. Only the signature needs to stay secret-backed; nothing here is private. */
export function makeToken(secret: string, kind: Kind, user: string, now: number, ttlMs: number): string {
  const payload = `${kind}.${user}.${now + ttlMs}.${randomBytes(12).toString("base64url")}`;
  return `${payload}.${sign(secret, payload)}`;
}

export function readToken(
  secret: string,
  kind: Kind,
  token: string | undefined,
  now: number,
): { user: string; expires: number; nonce: string } | undefined {
  const parts = (token ?? "").split(".");
  if (parts.length !== 5) return undefined;
  const [k, user, exp, nonce, sig] = parts as [string, string, string, string, string];
  const expected = Buffer.from(sign(secret, `${k}.${user}.${exp}.${nonce}`));
  const given = Buffer.from(sig);
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return undefined;
  const expires = Number(exp);
  if (k !== kind || !/^[UW][A-Z0-9]{2,}$/.test(user) || !Number.isFinite(expires) || expires <= now) return undefined;
  return { user, expires, nonce };
}

/** Sign-in links work once. Remembers used ones until they would have expired anyway. */
export class UsedLinks {
  private readonly used = new Map<string, number>();
  /** True the first time a nonce is seen. */
  use(nonce: string, expires: number, now: number): boolean {
    for (const [n, e] of this.used) if (e <= now) this.used.delete(n);
    if (this.used.has(nonce)) return false;
    this.used.set(nonce, expires);
    return true;
  }
}

export function readCookie(header: string | undefined, name: string): string | undefined {
  for (const part of (header ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return undefined;
}

export function sessionCookie(value: string, { secure, maxAgeMs }: { secure: boolean; maxAgeMs: number }): string {
  return `${COOKIE_NAME}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${Math.floor(maxAgeMs / 1000)}${secure ? "; Secure" : ""}`;
}
