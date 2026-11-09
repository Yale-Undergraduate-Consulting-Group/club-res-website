import { useEffect, useState } from 'react';
import { api, type ClubMetrics as Metrics, type MetricTarget } from '../../api';
import { EmptyState, Notice, StatusBadge } from '../ui/Primitives';

const PERIODS = [7, 30, 90] as const;

function TargetStatus({ row, minSample }: { row: MetricTarget; minSample: number }) {
  if (row.met === null) return <StatusBadge tone="neutral">{row.of < minSample ? `Not enough data (${row.of} of ${minSample})` : 'No data'}</StatusBadge>;
  return <StatusBadge tone={row.met ? 'success' : 'warning'}>{row.met ? 'On target' : 'Below target'}</StatusBadge>;
}

/** Admin → Metrics: the club against its semester targets, and per member. */
export default function ClubMetrics() {
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [data, setData] = useState<Metrics | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.analytics.clubMetrics(days).then(setData).catch((e: Error) => setError(e.message));
  }, [days]);

  const choose = (period: (typeof PERIODS)[number]) => {
    if (period === days) return;
    setData(null);
    setError('');
    setDays(period);
  };

  return (
    <div className="space-y-6" data-testid="club-metrics">
      <section className="surface-card rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-deep-navy">Semester targets</h2>
            <p className="text-sm text-slate-600">A target is judged once it has at least {data?.min_sample ?? 5} cases in the period.</p>
          </div>
          <div className="flex gap-2" role="group" aria-label="Period">
            {PERIODS.map((p) => (
              <button key={p} type="button" aria-pressed={days === p} className="ui-chip" onClick={() => choose(p)}>
                Last {p} days
              </button>
            ))}
          </div>
        </div>
        {error && <Notice tone="danger">Could not load metrics: {error}</Notice>}
        {!data && !error && <p className="text-sm text-slate-500">Loading…</p>}
        {data && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-pale-sky text-left">
                  <th className="py-2">Measure</th>
                  <th className="py-2">Now</th>
                  <th className="py-2">Count</th>
                  <th className="py-2">Target</th>
                  <th className="py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.targets.map((row) => (
                  <tr key={row.key} className="border-b border-pale-sky/50" data-testid={`target-${row.key}`}>
                    <td className="py-2">{row.label}</td>
                    <td className="py-2 font-semibold">{row.value === null ? '—' : `${row.value}%`}</td>
                    <td className="py-2 text-slate-600">{row.count} of {row.of}</td>
                    <td className="py-2">{row.target}</td>
                    <td className="py-2"><TargetStatus row={row} minSample={data.min_sample} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.waiting.contacts > 0 && (
          <Notice tone="warning">
            {data.waiting.contacts} contact{data.waiting.contacts === 1 ? ' is' : 's are'} waiting in draft or paused campaigns
            {data.waiting.oldest_added_at ? `, the oldest added ${data.waiting.oldest_added_at.slice(0, 10)}` : ''}.
          </Notice>
        )}
        {data && <p className="text-xs text-slate-500">Not measured yet: {data.not_measured.join(' ')}</p>}
      </section>

      {data && (
        <section className="surface-card rounded-xl p-6">
          <h2 className="mb-3 font-semibold text-deep-navy">By member, last {data.days} days</h2>
          {data.members.length === 0 ? <EmptyState title="No active members" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-pale-sky text-left">
                    <th className="py-2">Member</th>
                    <th className="py-2">Can send</th>
                    <th className="py-2">Searches</th>
                    <th className="py-2">Drafts</th>
                    <th className="py-2">Sent</th>
                    <th className="py-2">Replies</th>
                    <th className="py-2">Bounces</th>
                  </tr>
                </thead>
                <tbody>
                  {data.members.map((m) => (
                    <tr key={m.user_id} className="border-b border-pale-sky/50" data-testid={`member-metrics-${m.user_id}`}>
                      <td className="py-2">{m.name}</td>
                      <td className="py-2">{m.ready_to_send ? 'Yes' : 'No'}</td>
                      <td className="py-2">{m.searches}</td>
                      <td className="py-2">{m.drafts}</td>
                      <td className="py-2">{m.sent}</td>
                      <td className="py-2">{m.replies}</td>
                      <td className="py-2">{m.bounces}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
