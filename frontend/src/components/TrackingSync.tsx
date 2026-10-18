import { trackingTime } from '../lib/trackingTime';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

export default function TrackingSync({ onSynced }: { onSynced?: () => Promise<void> } = {}) {
  const [state, setState] = useState<{ last_success_at?: string; error?: string; in_progress?: boolean } | null>(null);
  const onSyncedRef = useRef(onSynced);
  const successRef = useRef<string | undefined>(undefined);
  useEffect(() => { onSyncedRef.current = onSynced; }, [onSynced]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const refresh = () => api.outreach.syncStatus().then(s => { if (active) {
        if (s.last_success_at && successRef.current !== undefined && successRef.current !== s.last_success_at) {
          void onSyncedRef.current?.().catch(() => setError('Sync completed, but the page could not refresh.'));
        }
        successRef.current = s.last_success_at || '';
        setState(s); setError('');
      } })
      .catch(() => { if (active) setError('Unable to load Gmail tracking status.'); });
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  return (
    <section className="surface-card rounded-xl p-4 mb-6 flex flex-wrap items-center justify-between gap-3" aria-label="Gmail tracking">
      <div className="text-sm" aria-live="polite">
        <p className="font-semibold text-deep-navy">Your Gmail tracking</p>
        <p className="text-slate-600">Last successful sync: {trackingTime(state?.last_success_at)}</p>
        <p className="text-xs text-slate-500">Checks every two minutes while the server is running. Open detection is approximate.</p>
        {(error || state?.error) && <p className="text-red-700 mt-1" role="alert">{error || state?.error}</p>}
      </div>
      <button type="button" disabled={busy || state?.in_progress} className="ui-button ui-button--secondary ui-button--sm shrink-0"
        onClick={async () => {
          setBusy(true); setError('');
          try {
            const result = await api.outreach.syncInboxReplies();
            if (!result.ok) setError(result.error || 'Gmail synchronization failed.');
            setState(await api.outreach.syncStatus());
            if (result.ok && result.in_progress) setState(previous => ({ ...previous, in_progress: true }));
            else if (result.ok) await onSynced?.();
          } catch (e) { setError(e instanceof Error ? e.message : 'Synchronization failed.'); }
          finally { setBusy(false); }
        }}>{busy || state?.in_progress ? 'Syncing…' : 'Sync now'}</button>
    </section>
  );
}
