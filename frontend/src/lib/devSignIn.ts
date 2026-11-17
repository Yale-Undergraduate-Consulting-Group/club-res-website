/** A dev server, or the dev box reached through its SSM tunnel at localhost. */
const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];

/**
 * Whether the login page offers the local-only dev sign-ins (member and alumni).
 * They exist so a local or dev-box build can be reviewed: Google only returns
 * to the registered production callback. The buttons are a convenience; the
 * endpoints behind them 404 unless their DEV_ variable is set, the app is not
 * production and the request's own host is loopback (backend routers/auth.py,
 * routers/alumni.py).
 */
export function showsDevSignIn(hostname: string, devBuild: boolean): boolean {
  return devBuild || LOOPBACK.includes(hostname);
}
