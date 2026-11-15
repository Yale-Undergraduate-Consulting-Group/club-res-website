import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import QRCode from 'qrcode';
import { workspaceRequest } from '../lib/workspaceApi';
import { Button, ConfirmDialog, Notice } from './ui/Primitives';

type JoinSession = {
  id: number;
  expires_at: number;
  max_members: number;
  closed: boolean;
  open: boolean;
  members: { name?: string | null; email: string; joined_at: number }[];
  token?: string;
};

const BASE = '/api/admin/invitations/join-sessions';

/**
 * Join by QR code (backend: routers/invitations.py, join sessions). An admin
 * opens a session and shows its QR code full screen; anyone in the room who
 * signs in through it with a verified @yale.edu Google account becomes a
 * member at once, until it expires, fills, or is closed here.
 */
export default function JoinQr({ onJoined }: { onJoined?: () => void }) {
  const [minutes, setMinutes] = useState(120);
  const [maxMembers, setMaxMembers] = useState(200);
  const [session, setSession] = useState<JoinSession | null>(null);
  const [link, setLink] = useState('');
  const [qr, setQr] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // While the QR is up, the count and names refresh every few seconds.
  useEffect(() => {
    if (!session?.open) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      workspaceRequest<JoinSession>(`${BASE}/${session.id}`)
        .then((next) => setSession((current) => (current ? { ...next, token: current.token } : next)))
        .catch((e: Error) => setError(e.message));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [session?.id, session?.open]);

  // The members list on this page picks up each new arrival. The callback is
  // read through a ref so a new function from the parent never refetches.
  const onJoinedRef = useRef(onJoined);
  useEffect(() => { onJoinedRef.current = onJoined; }, [onJoined]);
  const joinedCount = session?.members.length ?? 0;
  useEffect(() => {
    if (joinedCount > 0) onJoinedRef.current?.();
  }, [joinedCount]);

  async function open() {
    setBusy(true);
    setError('');
    try {
      const created = await workspaceRequest<JoinSession>(BASE, 'POST', { minutes, max_members: maxMembers });
      const url = `${window.location.origin}/api/auth/google?join=${created.token}`;
      setLink(url);
      setQr(await QRCode.toDataURL(url, { width: 640, margin: 2, color: { dark: '#00346b', light: '#ffffff' } }));
      setSession(created);
      setNow(Date.now());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (!session) return;
    setBusy(true);
    try {
      const closed = await workspaceRequest<JoinSession>(`${BASE}/${session.id}/close`, 'POST');
      setSession({ ...closed, token: session.token });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setConfirmClose(false);
    }
  }

  const minutesLeft = session ? Math.max(0, Math.ceil((session.expires_at * 1000 - now) / 60000)) : 0;
  const joined = session?.members.length ?? 0;

  return (
    <section className="surface-card" aria-labelledby="join-qr-heading">
      <h2 id="join-qr-heading" className="app-section-title mb-1">Join by QR code</h2>
      <p className="text-sm text-slate-600 mb-4">
        Show a QR code to a room. Anyone who scans it and signs in with their Yale Google account becomes a member
        right away, until it expires, fills up, or you close it.
      </p>
      {error && <Notice tone="danger" className="mb-3">{error}</Notice>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="block mb-1 text-slate-600">Open for</span>
          <select className="ui-input" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {[30, 60, 120, 240].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? 's' : ''}`}</option>)}
          </select>
        </label>
        <label className="text-sm">
          <span className="block mb-1 text-slate-600">At most</span>
          <input className="ui-input w-28" type="number" min={1} max={500} value={maxMembers}
                 onChange={(e) => setMaxMembers(Math.min(500, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
        <Button disabled={busy} onClick={() => void open()}>{busy ? 'Opening…' : 'Show join QR'}</Button>
      </div>

      {/* Portaled to <body>: the page content sits inside an animated
          (transformed) container, which would trap a fixed overlay. */}
      {session && qr && createPortal(
        <div className="join-qr" role="dialog" aria-modal="true" aria-labelledby="join-qr-title">
          <div className="join-qr__panel">
            <img src="/yucg-logo.png" alt="" className="join-qr__logo" />
            <h2 id="join-qr-title" className="join-qr__title">Scan to join YUCG Resources</h2>
            <p className="join-qr__hint">Sign in with your Yale Google account (@yale.edu).</p>
            {session.open ? (
              <img src={qr} alt="QR code that opens Yale sign-in for YUCG Resources" className="join-qr__code" />
            ) : (
              <div className="join-qr__code join-qr__code--closed">Joining is closed</div>
            )}
            <p className="join-qr__count" role="status">
              <strong>{joined}</strong> joined
              {session.open ? ` · ${minutesLeft} min left · room for ${session.max_members - joined} more` : ''}
            </p>
            {joined > 0 && (
              <p className="join-qr__names">{session.members.slice(0, 8).map((m) => m.name || m.email).join(' · ')}</p>
            )}
            <div className="join-qr__actions">
              {session.open && <Button variant="secondary" onClick={() => setConfirmClose(true)}>Close joining</Button>}
              <Button variant="ghost" onClick={() => { setQr(''); if (!session.open) setSession(null); }}>
                {session.open ? 'Hide (stays open)' : 'Done'}
              </Button>
            </div>
            {session.open && <p className="join-qr__link">{link}</p>}
          </div>
        </div>,
        document.body,
      )}
      {session?.open && !qr && (
        <p className="text-sm mt-3">
          A join QR is open: {joined} joined, {minutesLeft} min left.{' '}
          <button type="button" className="underline" onClick={() => void QRCode.toDataURL(link, { width: 640, margin: 2, color: { dark: '#00346b', light: '#ffffff' } }).then(setQr)}>Show it again</button>
          {' · '}
          <button type="button" className="underline" onClick={() => setConfirmClose(true)}>Close joining</button>
        </p>
      )}
      {createPortal(
        <ConfirmDialog
          open={confirmClose}
          title="Close joining?"
          body={`${joined} people joined through this QR code. Closing stops anyone else from joining with it; those who joined stay members.`}
          confirmLabel="Close joining"
          busy={busy}
          onConfirm={() => void close()}
          onClose={() => setConfirmClose(false)}
        />,
        document.body,
      )}
    </section>
  );
}
