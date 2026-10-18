/**
 * Login page - shown when user is NOT authenticated.
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, getBackendOriginForOAuth } from '../api';

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
      <div className="surface-card p-6 sm:p-8 w-full max-w-md">
        <div className="flex justify-center mb-6">
          <img
            src="/yucg-logo.png"
            alt="YUCG"
            className="h-12 w-auto block outline-none select-none"
            decoding="async"
          />
        </div>
        <h1 className="text-2xl font-bold text-center text-deep-navy mb-2">YUCG Outreach</h1>
        <p className="text-center text-slate-600 text-sm mb-8">Yale Undergraduate Consulting Group</p>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
            {error === 'invalid_callback' &&
              'Your sign-in session expired. Start again; if the problem continues, contact a club administrator.'}
            {error === 'token_exchange_failed' && 'Authentication failed. Please try again.'}
            {error === 'no_access_token' && 'Could not get access. Please try again.'}
            {error === 'userinfo_failed' && 'Could not load your profile. Please try again.'}
            {error === 'no_email' && 'No email from Google. Please use an account with email.'}
            {error === 'domain_not_allowed' && 'Only @yale.edu email addresses are allowed to sign in.'}
            {error === 'account_deactivated' && 'Your account has been deactivated. Contact an admin.'}
            {error === 'callback_failed' && (
              <>Sign-in could not be completed. Try again; if the problem continues, contact a club administrator.</>
            )}
            {error === 'oauth_not_configured' && (
              <>Club sign-in is temporarily unavailable. Contact a club administrator.</>
            )}
            {!['invalid_callback', 'token_exchange_failed', 'no_access_token', 'userinfo_failed', 'no_email', 'domain_not_allowed', 'oauth_not_configured', 'account_deactivated', 'callback_failed'].includes(error) && error}
          </div>
        )}

        {need2fa && (
          <div className="mb-6 space-y-3">
            <p className="text-sm text-slate-700">Enter the 6-digit code from your authenticator app.</p>
            {twoFaError && (
              <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{twoFaError}</div>
            )}
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
              className="w-full px-3 py-2 rounded-lg border border-pale-sky text-center tracking-widest"
            />
            <button
              type="button"
              disabled={busy || code.trim().length < 6}
              onClick={submit2fa}
              className="w-full px-4 py-3 bg-deep-navy text-white font-bold uppercase tracking-wide text-sm disabled:opacity-50"
            >
              {busy ? 'Checking…' : 'Continue'}
            </button>
          </div>
        )}

        <button
          onClick={handleGoogleLogin}
          className="w-full flex items-center justify-center gap-3 px-4 py-3 border-2 border-[var(--deep-navy)] bg-white hover:bg-pale-sky/30 font-bold uppercase tracking-wide text-sm text-deep-navy transition-colors"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          Sign in with Google
        </button>

        {/* Dev servers only. import.meta.env.DEV is false in every built
            bundle, so this button cannot exist in the deployed app, and the
            endpoint behind it 404s unless DEV_LOGIN_EMAIL is set on a
            backend answering a loopback host. Google will only redirect to
            the registered production callback, so without this the app
            cannot be reviewed locally at all. */}
        {import.meta.env.DEV && (
          <button
            type="button"
            onClick={async () => {
              await fetch('/api/auth/dev-login', { method: 'POST', credentials: 'include' });
              window.location.assign('/');
            }}
            className="mt-3 w-full rounded-xl border border-dashed border-slate-400 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Dev sign-in (local only)
          </button>
        )}

        <p className="mt-6 text-center text-xs text-slate-500">
          Secure login via Google. Only @yale.edu accounts.
        </p>

        <div className="mt-6 p-4 bg-slate-50 border-2 border-[var(--border)]">
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Yale Duo verification</h3>
          <p className="text-xs text-slate-600 mb-2">
            @yale.edu accounts require Duo two-factor authentication. When you sign in, you may be prompted to verify via the Duo Mobile app, a phone call, or passcode.
          </p>
          <a
            href="https://mfa.its.yale.edu"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-steel-blue hover:text-deep-navy hover:underline font-medium"
          >
            Manage Duo devices → mfa.its.yale.edu
          </a>
        </div>
      </div>
    </div>
  );
}
