/**
 * Alumni sign-in, separate from the members' page. Google decides who the
 * person is; the server lets them in only when an admin allowed their address
 * or its domain (backend/app/routers/alumni.py). No password is ever stored.
 */
import { useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, getBackendOriginForOAuth } from '../api';
import { Button, Notice } from '../components/ui/Primitives';

const ERRORS: Record<string, string> = {
  not_eligible: 'This Google account is not on the club’s alumni list. Sign in with your alumni address (for example your @aya.yale.edu YaleMail), or ask a club admin to add the address you use.',
  revoked: 'Alumni access for this address has been turned off. Contact a club admin.',
  identity_mismatch: 'This address signed in before with a different Google account. Use that account, or ask a club admin.',
  email_not_verified: 'Google has not verified this email address. Verify it with Google, then try again.',
  invalid_callback: 'Your sign-in session expired. Please start again.',
  callback_failed: 'Sign-in could not be completed. Please try again.',
};

export default function AlumniLogin() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const error = searchParams.get('error');

  useEffect(() => {
    // Already signed in as an alumnus: straight to the alumni area.
    api.alumni.me().then(() => navigate('/alumni', { replace: true })).catch(() => {});
  }, [navigate]);

  return (
    <div className="app-auth-shell">
      <div className="surface-card w-full max-w-md">
        <div className="flex justify-center mb-6">
          <img src="/yucg-logo.png" alt="YUCG" className="h-12 w-auto block outline-none select-none" decoding="async" />
        </div>
        <h1 className="text-2xl font-bold text-center text-deep-navy mb-2">YUCG Alumni</h1>
        <p className="text-sm text-center text-slate-600 mb-6">News, events and the alumni directory from the YUCG alumni board.</p>

        {error && <Notice tone="danger" className="mb-4">{ERRORS[error] ?? 'Sign-in could not be completed. Please try again.'}</Notice>}

        <Button variant="secondary" className="w-full" onClick={() => { window.location.href = `${getBackendOriginForOAuth()}/api/auth/alumni/google`; }}>
          Sign in with Google
        </Button>

        {/* Local review only, fenced like the members' dev sign-in: the endpoint
            404s unless DEV_ALUMNI_EMAIL is set, the app is not production and the
            request's host is loopback; the address must still be on the lists. */}
        {(import.meta.env.DEV || ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) && (
          <Button
            variant="secondary"
            className="mt-3 w-full"
            onClick={async () => {
              const response = await fetch('/api/alumni/dev-login', { method: 'POST', credentials: 'include' });
              if (response.ok) window.location.assign('/alumni');
              else navigate('/alumni/login?error=not_eligible');
            }}
          >
            Dev sign-in (local only)
          </Button>
        )}

        <p className="mt-6 text-center text-xs text-slate-500">
          Your alumni address or one a club admin added. Current members: <Link to="/login" className="underline">member sign-in</Link>.
        </p>
      </div>
    </div>
  );
}
