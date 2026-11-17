import { useEffect, useState } from 'react';
import { api, type CostReport } from '../../api';
import { Notice, StatusBadge } from '../ui/Primitives';

const usd = (value: number | null | undefined) => value == null ? '—' : `$${value.toFixed(value < 1 ? 4 : 2)}`;

function AwsTable({ title, rows }: { title: string; rows: { name: string; usd: number }[] }) {
  return (
    <section className="surface-card min-w-0">
      <h3 className="font-semibold text-deep-navy mb-3">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-pale-sky"><th className="text-left py-2 pr-4">Name</th><th className="text-right py-2">Month to date</th></tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.name} className="border-b border-slate-100 last:border-0">
              <td className="py-2 pr-4 break-words">{row.name}</td>
              <td className="py-2 text-right tabular-nums whitespace-nowrap">{usd(row.usd)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

/** Admin → Costs: cached AWS billing and local provider counters. */
export default function Costs() {
  const [data, setData] = useState<CostReport | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.admin.costs().then(setData).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <Notice tone="danger">Could not load costs: {error}</Notice>;
  if (!data) return <p className="text-sm text-slate-500">Loading costs…</p>;

  return (
    <div className="space-y-6 min-w-0 max-w-full" data-testid="costs-dashboard">
      <section className="surface-card space-y-3 min-w-0 max-w-full">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-semibold text-deep-navy">AWS costs</h2>
            <p className="text-sm text-slate-600">Month to date for {data.aws.month}. Cost Explorer is read by a daily background job, never when this page opens.</p>
          </div>
          {data.aws.connected
            ? <StatusBadge tone="success">Cached {data.aws.fetched_at ? new Date(data.aws.fetched_at).toLocaleString() : 'daily'}</StatusBadge>
            : <StatusBadge tone="warning">Not connected</StatusBadge>}
        </div>
        {!data.aws.connected && <Notice tone="warning">{data.aws.message}</Notice>}
        {data.aws.connected && <p className="text-3xl font-semibold tabular-nums text-deep-navy">{usd(data.aws.total_usd)}</p>}
        {data.aws.connected && <p className="text-xs text-slate-500">One Cost Explorer request per day per environment costs {usd(data.aws.request_usd ?? 0.01)}. Untagged account costs are listed separately.</p>}
      </section>

      {data.aws.connected && (
        <div className="grid gap-4 lg:grid-cols-2 min-w-0 max-w-full">
          <AwsTable title="By service" rows={data.aws.by_service} />
          <AwsTable title="By environment" rows={data.aws.by_environment} />
        </div>
      )}

      <section className="surface-card space-y-3 min-w-0 max-w-full">
        <div>
          <h2 className="font-semibold text-deep-navy">Pay-per-call APIs</h2>
          <p className="text-sm text-slate-600">Calls are counted locally before network I/O. Estimates use public list prices; provider free tiers can make the invoice lower.</p>
        </div>
        <div className="overflow-x-auto min-w-0 max-w-full">
          <table className="min-w-[760px] w-full text-sm">
            <thead><tr className="border-b border-pale-sky">
              <th className="text-left py-2 pr-4">Provider</th><th className="text-right py-2 px-3">This month</th>
              <th className="text-right py-2 px-3">Estimated</th><th className="text-left py-2 px-3">Budget / limit</th>
              <th className="text-left py-2 pl-3">Three-month trend</th>
            </tr></thead>
            <tbody>{data.providers.map((row) => (
              <tr key={row.id} className="border-b border-slate-100 align-top" data-testid={`provider-cost-${row.id}`}>
                <td className="py-3 pr-4 min-w-[190px]"><p className="font-medium text-deep-navy">{row.name}</p><p className="text-xs text-slate-500 mt-1 max-w-xs">{row.note}</p></td>
                <td className="py-3 px-3 text-right tabular-nums whitespace-nowrap">{row.calls} {row.unit}</td>
                <td className="py-3 px-3 text-right tabular-nums whitespace-nowrap">{row.estimated_usd == null ? 'Not priced' : usd(row.estimated_usd)}</td>
                <td className="py-3 px-3 whitespace-nowrap">{row.budget_or_limit ?? 'Provider plan'}</td>
                <td className="py-3 pl-3 min-w-[210px]">
                  {row.trend.map((point) => <span key={point.month} className="block whitespace-nowrap">{point.month}: {point.calls} / {point.usd == null ? 'not priced' : usd(point.usd)}</span>)}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <section className="surface-card">
        <h2 className="font-semibold text-deep-navy mb-3">What can be reduced</h2>
        <ol className="space-y-3">
          {data.recommendations.map((row) => (
            <li key={row.rank} className="flex gap-3 min-w-0">
              <span className="shrink-0 flex h-7 w-7 items-center justify-center rounded-full bg-pale-sky text-sm font-semibold text-deep-navy">{row.rank}</span>
              <div className="min-w-0"><p className="font-medium text-deep-navy break-words">{row.title} <span className="text-sm font-normal text-slate-600">({row.saving})</span></p><p className="text-sm text-slate-600 break-words">Risk: {row.risk}</p></div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
