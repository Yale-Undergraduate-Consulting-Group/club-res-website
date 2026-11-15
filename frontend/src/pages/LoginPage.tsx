/**
 * Login page - shown when user is NOT authenticated.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, getBackendOriginForOAuth } from '../api';
import { Button } from '../components/ui/Primitives';

export default function LoginPage() {
  const [searchParams] = useSearchParams();
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
        <h1 className="text-2xl font-bold text-center text-deep-navy mb-8">YUCG Resources</h1>

        {error && (
          <div className="mb-4 ui-notice ui-notice--danger">
            {error === 'invalid_callback' &&
              'Your sign-in session expired. Start again; if the problem continues, contact a club administrator.'}
            {error === 'token_exchange_failed' && 'Authentication failed. Please try again.'}
            {error === 'no_access_token' && 'Could not get access. Please try again.'}
            {error === 'userinfo_failed' && 'Could not load your profile. Please try again.'}
            {error === 'no_email' && 'No email from Google. Please use an account with email.'}
            {error === 'domain_not_allowed' && 'Only @yale.edu email addresses are allowed to sign in.'}
            {error === 'join_closed' && 'This join QR code is closed, expired or full. Ask a club admin for a new one.'}
            {error === 'account_deactivated' && 'Your account has been deactivated. Contact an admin.'}
            {error === 'callback_failed' && (
              <>Sign-in could not be completed. Try again; if the problem continues, contact a club administrator.</>
            )}
            {error === 'oauth_not_configured' && (
              <>Club sign-in is temporarily unavailable. Contact a club administrator.</>
            )}
            {!['invalid_callback', 'token_exchange_failed', 'no_access_token', 'userinfo_failed', 'no_email', 'domain_not_allowed', 'oauth_not_configured', 'account_deactivated', 'callback_failed', 'join_closed'].includes(error) && error}
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

        {/* Local review only: a dev server, or the dev box reached through its
            SSM tunnel at localhost. The endpoint behind it 404s unless
            DEV_LOGIN_EMAIL is set and the request's own host is loopback, so
            the public site (its CloudFront address, no setting) cannot sign
            anyone in this way. Google will only redirect to the registered
            production callback, so without this dev cannot be reviewed. */}
        {(import.meta.env.DEV || ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) && (
          <Button
            variant="secondary"
            className="mt-3 w-full"
            onClick={async () => {
              await fetch('/api/auth/dev-login', { method: 'POST', credentials: 'include' });
              window.location.assign('/');
            }}
          >
            Dev sign-in (local only)
          </Button>
        )}

        <p className="mt-6 text-center text-xs text-slate-500">@yale.edu accounts only.</p>
      </div>
    </div>
  );
}
