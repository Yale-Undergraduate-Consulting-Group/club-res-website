import { useEffect, useState } from 'react';
import { workspaceRequest } from '../lib/workspaceApi';

/**
 * Who emailed whom, and when.
 *
 * The per-member totals this used to carry above the list are now the "By
 * member" section of the breakdown, so only the message list remains: it is
 * the one record that names the individual send rather than counting it.
 */

type Ledger = {
  days: number;
  items: {
    id: number; sender_name: string; sender_email: string; recipient: string;
    sent_at: string; campaign_name?: string; message_kind: string; replied: number; opened: number;
  }[];
};

export default function OutreachLedger() {
  const [data, setData] = useState<Ledger | null>(null);
  const [days, setDays] = useState(30);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    workspaceRequest<Ledger>(`/api/activity/outreach?days=${days}&offset=${offset}`)
      .then((value) => { if (active) { setData(value); setError(''); } })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [days, offset]);

  const items = data?.items ?? [];

  return (
    <section className="surface-card p-5" aria-label="Club outreach activity">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="app-section-title">Club outreach activity</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Confirmed sends, attributed to the sending member. Shared reporting never exposes
            another member’s mailbox or outgoing queue.
          </p>
        </div>
        <label className="text-xs font-semibold text-slate-600">
          Period{' '}
          <select
            className="ui-input ui-input--sm"
            value={days}
            onChange={(e) => { setDays(Number(e.target.value)); setOffset(0); }}
          >
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </label>
      </div>

      {error && <p role="alert" className="ui-notice ui-notice--danger mt-3">{error}</p>}

      {items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">No confirmed sends in this period.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-4 font-semibold">Sender</th>
                <th className="py-2 pr-4 font-semibold">Recipient</th>
                <th className="py-2 pr-4 font-semibold">Campaign</th>
                <th className="py-2 pr-4 font-semibold">Type</th>
                <th className="py-2 font-semibold">Sent</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {items.map((row) => (
                <tr key={row.id}>
                  <td className="py-2 pr-4 text-slate-700">{row.sender_name || row.sender_email}</td>
                  <td className="py-2 pr-4 text-slate-700">{row.recipient}</td>
                  <td className="py-2 pr-4 text-slate-500">{row.campaign_name || '—'}</td>
                  <td className="py-2 pr-4 text-slate-500">
                    {row.message_kind === 'initial' ? 'Initial email' : 'Follow-up'}
                  </td>
                  <td className="py-2 text-slate-500">{row.sent_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(offset > 0 || items.length >= 50) && (
        <div className="mt-3 flex gap-2">
          <button
            className="ui-button ui-button--secondary ui-button--sm"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </button>
          <button
            className="ui-button ui-button--secondary ui-button--sm"
            disabled={items.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
