import { useCallback, useEffect, useState } from 'react';
import { fetchApi } from '../../api';
import { Button, Notice, StatusBadge } from '../ui/Primitives';
type Dispatch = { dispatch_key: string; recipient: string; sender_user_id: number; state: string; claimed_at?: number; completed_at?: number; last_error?: string };
export default function DispatchRecovery({ campaignId, onReconciled }: { campaignId: number; onReconciled: () => Promise<void> }) {
  const [dispatches, setDispatches] = useState<Dispatch[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = useCallback(async () => { setDispatches(await fetchApi<Dispatch[]>(`/api/campaigns/${campaignId}/dispatches`)); }, [campaignId]);
  useEffect(() => {
    fetchApi<Dispatch[]>(`/api/campaigns/${campaignId}/dispatches`)
      .then(setDispatches)
      .catch(e => setError(e instanceof Error ? e.message : 'Unable to check dispatch recovery.'));
  }, [campaignId]);
  const uncertain = dispatches.filter(item => ['claimed', 'ambiguous'].includes(item.state));
  if (!uncertain.length && !error && !message) return null;
  return <section className="surface-card p-4 mb-5" aria-label="Send recovery">
    <h2 className="app-section-title">Sending needs review</h2>
    <p className="text-sm text-slate-600 my-2">An interrupted request may have reached Gmail. These messages stay paused until their status is known. Checking Sent mail does not send another email.</p>
    {error && <Notice tone="danger">{error}</Notice>}
    {message && <Notice tone="info">{message}</Notice>}
    {uncertain.map(item => <div className="flex flex-wrap gap-3 justify-between items-center py-3 border-b" key={item.dispatch_key}>
      <span className="text-sm break-all">{item.recipient} <StatusBadge tone="warning">{item.state}</StatusBadge></span>
      <Button variant="secondary" disabled={busy !== null} onClick={async () => {
        setBusy(item.dispatch_key); setError(''); setMessage('');
        try {
          const result = await fetchApi<{ reconciled: boolean; state: string; reason?: string }>(`/api/campaigns/${campaignId}/dispatches/reconcile`, { method: 'POST', body: JSON.stringify({ dispatch_key: item.dispatch_key }) });
          setMessage(result.reconciled ? 'Confirmed in your Sent mail. The message will not be resent.' : result.reason || 'No confirmed match. The message remains paused for review.');
          await refresh(); await onReconciled();
        } catch (e) { setError(e instanceof Error ? e.message : 'Unable to check Sent mail.'); }
        finally { setBusy(null); }
      }}>{busy === item.dispatch_key ? 'Checking…' : 'Check Sent mail'}</Button>
    </div>)}
  </section>;
}
