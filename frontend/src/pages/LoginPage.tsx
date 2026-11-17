/**
 * YUCG Connect: the one sign-in page, shown when no member session exists.
 * One Google sign-in; the server decides where it leads (backend
 * routers/auth.py): members to the club site (with the alumni session too when
 * they are also on the alumni lists), alumni to the alumni portal.
 */
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, getBackendOriginForOAuth } from '../api';
import { showsDevSignIn } from '../lib/devSignIn';
import { Button } from '../components/ui/Primitives';

const ERRORS: Record<string, string> = {
  invalid_callback: 'Your sign-in session expired. Start again; if the problem continues, contact a club administrator.',
  token_exchange_failed: 'Authentication failed. Please try again.',
  no_access_token: 'Could not get access. Please try again.',
  userinfo_failed: 'Could not load your profile. Please try again.',
  no_email: 'No email from Google. Please use an account with email.',
  email_not_verified: 'Google has not verified this email address. Verify it with Google, then try again.',
  domain_not_allowed: 'This Google account cannot sign in. Members use their @yale.edu account; alumni use the address the club added for them, such as an @aya.yale.edu YaleMail address.',
  invitation_required: 'This Google account has no access yet. Members join through an invitation or a join QR code from a club admin; alumni ask a club admin to add their address.',
  invitation_invalid: 'This invitation is for another address, or it expired, was revoked or was already used. Ask a club admin for a new one.',
  join_closed: 'This join QR code is closed, expired or full. Ask a club admin for a new one.',
  account_deactivated: 'Your account has been deactivated. Contact an admin.',
  identity_mismatch: 'This address signed in before with a different Google account. Use that account, or ask a club admin.',
  // From links to the former alumni sign-in page (/alumni/login redirects here).
  not_eligible: 'This Google account is not on the club’s alumni list. Sign in with your alumni address (for example your @aya.yale.edu YaleMail), or ask a club admin to add the address you use.',
  revoked: 'Alumni access for this address has been turned off. Contact a club admin.',
  callback_failed: 'Sign-in could not be completed. Try again; if the problem continues, contact a club administrator.',
  oauth_not_configured: 'Club sign-in is temporarily unavailable. Contact a club administrator.',
};

export default function LoginPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const error = searchParams.get('error');
  const need2fa = searchParams.get('need_2fa') === '1';
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [twoFaError, setTwoFaError] = useState<string | null>(null);

  const handleGoogleLogin = () => {
    window.location.href = `${getBackendOriginForOAuth()}/api/auth/google`;
  };

  const submit2fa = async () => {
    setBusy(true);
    setTwoFaError(null);
    try {
      await api.auth.complete2fa(code.trim());
      window.location.href = '/';
    } catch (e) {
      setTwoFaError(e instanceof Error ? e.message : 'Invalid code');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-auth-shell">
      <div className="surface-card w-full max-w-md">
        <div className="flex justify-center mb-6">
          <img
            src="/yucg-logo.png"
            alt="YUCG"
            className="h-12 w-auto block outline-none select-none"
            decoding="async"
          />
        </div>
        <h1 className="text-2xl font-bold text-center text-deep-navy mb-2">YUCG Connect</h1>
        <p className="text-sm text-center text-slate-600 mb-8">One sign-in for members and alumni.</p>

        {error && (
          <div className="mb-4 ui-notice ui-notice--danger">
            {ERRORS[error] ?? 'Sign-in could not be completed. Please try again.'}
          </div>
        )}

        {need2fa && (
          <div className="mb-6 space-y-3">
            <p className="text-sm text-slate-700">Enter the 6-digit code from your authenticator app.</p>
            {twoFaError && (
              <div className="ui-notice ui-notice--danger">{twoFaError}</div>
            )}
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
              className="w-full px-3 py-2 border border-pale-sky text-center tracking-widest"
            />
            <Button className="w-full" disabled={busy || code.trim().length < 6} onClick={submit2fa}>
              {busy ? 'Checking…' : 'Continue'}
            </Button>
          </div>
        )}

        <Button variant="secondary" className="w-full" onClick={handleGoogleLogin}>
          <svg className="w-5 h-5" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          Sign in with Google
        </Button>

        {/* Local review only (lib/devSignIn.ts): the endpoints 404 unless their
            DEV_LOGIN_EMAIL / DEV_ALUMNI_EMAIL is set, the app is not production
            and the request's own host is loopback, so the public site cannot
            sign anyone in this way; the alumni address must still be on the
            lists. Google only returns to the production callback, so without
            these a local or dev-box build cannot be reviewed. */}
        {showsDevSignIn(window.location.hostname, import.meta.env.DEV) && (
          <div className="mt-3 grid gap-3">
            <Button
              variant="secondary"
              className="w-full"
              onClick={async () => {
                await fetch('/api/auth/dev-login', { method: 'POST', credentials: 'include' });
                window.location.assign('/');
              }}
            >
              Dev sign-in: member (local only)
            </Button>
            <Button
              variant="secondary"
              className="w-full"
              onClick={async () => {
                const response = await fetch('/api/alumni/dev-login', { method: 'POST', credentials: 'include' });
                if (response.ok) window.location.assign('/alumni');
                else navigate('/login?error=not_eligible', { replace: true });
              }}
            >
              Dev sign-in: alumni (local only)
            </Button>
          </div>
        )}

        <p className="mt-6 text-center text-xs text-slate-500">
          Members: your @yale.edu Google account. Alumni: the address the club added for you.
        </p>
      </div>
    </div>
  );
}
