import { useState } from 'react';
import { api, fetchApi, type Member } from '../../api';
import { Button, ConfirmDialog, Notice } from '../ui/Primitives';

type Evidence = { historical_sender_ids: number[]; requires_explicit_confirmation: boolean };
export default function CampaignOwnershipReview({ campaignId, onResolved }: { campaignId: number; onResolved: () => Promise<void> }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [sender, setSender] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  return <section className="surface-card p-4 mt-3" aria-label="Legacy campaign ownership review">
    <p className="text-sm mb-3">This campaign cannot send until its original account is established. Historical send records remain unchanged.</p>
    {error && <Notice tone="danger">{error}</Notice>}
    {!evidence ? <Button variant="secondary" disabled={busy} onClick={async () => {
      setBusy(true); setError('');
      try {
        const [result, users] = await Promise.all([fetchApi<Evidence>(`/api/campaigns/${campaignId}/ownership-evidence`), api.admin.users.list()]);
        setEvidence(result); setMembers(users.filter(member => member.is_active));
      } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load ownership evidence.'); }
      finally { setBusy(false); }
    }}>Review ownership evidence</Button> : <>
      <p className="text-sm mb-3">Recorded senders: {evidence.historical_sender_ids.length ? evidence.historical_sender_ids.map(id => members.find(member => member.id === id)?.email || `Member #${id}`).join(', ') : 'No reliable sender history. Confirm using independent records.'}</p>
      {evidence.historical_sender_ids.length > 1 ? <Notice tone="warning">Conflicting sender history requires manual review. Automatic assignment is blocked.</Notice> : <>
        <label className="block text-sm mb-3">Original sender<select className="block mt-1 p-2 border w-full" value={sender} onChange={e => setSender(e.target.value)}><option value="">Choose a verified account</option>{members.filter(member => !evidence.historical_sender_ids.length || evidence.historical_sender_ids.includes(member.id)).map(member => <option key={member.id} value={member.id}>{member.email}</option>)}</select></label>
        <label className="block text-sm mb-3">Evidence supporting this assignment<textarea value={reason} onChange={e => setReason(e.target.value)} className="block mt-1 p-2 border w-full" /></label>
        <Button disabled={!sender || reason.trim().length < 10 || busy} onClick={() => setConfirm(true)}>Review assignment</Button>
      </>}
    </>}
    <ConfirmDialog open={confirm} title="Confirm original sender" body={`Assign ownership and future sending to ${members.find(member => member.id === Number(sender))?.email || 'the selected member'}? This does not send email or alter historical attribution.`} confirmLabel="Confirm assignment" busy={busy} onClose={() => setConfirm(false)} onConfirm={async () => {
      setBusy(true); setError('');
      try { await fetchApi<unknown>(`/api/campaigns/${campaignId}/reconcile-owner`, { method: 'POST', body: JSON.stringify({ sender_user_id: Number(sender), confirmed: true, reason: reason.trim() }) }); setConfirm(false); await onResolved(); }
      catch (e) { setError(e instanceof Error ? e.message : 'Ownership could not be confirmed.'); }
      finally { setBusy(false); }
    }} />
  </section>;
}
