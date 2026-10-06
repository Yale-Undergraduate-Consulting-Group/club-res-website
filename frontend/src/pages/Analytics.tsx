import { useEffect, useState } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import { api, type Campaign, type BreakdownSection, type OutcomeSplit } from '../api';
import PageHeader from '../components/PageHeader';
import SectionChart from '../components/SectionChart';
import OutcomePie from '../components/OutcomePie';
import OutreachLedger from '../components/OutreachLedger';
import { Button, EmptyState, Notice, StatusBadge } from '../components/ui/Primitives';

type Breakdown = { sections: BreakdownSection[]; mine?: OutcomeSplit | null; club?: OutcomeSplit };

function outcomeSlices(split: OutcomeSplit | null | undefined) {
  return [
    { label: 'replied', value: split?.replied ?? 0, colour: 'var(--chart-good)' },
    { label: 'awaiting a reply', value: split?.awaiting ?? 0, colour: 'var(--chart-4)' },
    { label: 'bounced', value: split?.bounced ?? 0, colour: 'var(--chart-bad)' },
    { label: 'still queued', value: split?.queued ?? 0, colour: 'var(--chart-idle)' },
  ];
}

export default function Analytics() {
  const { user } = useOutletContext<{ user: { id: number } }>();
  const [breakdown, setBreakdown] = useState<Breakdown>({ sections: [] });
  const [insights, setInsights] = useState<string[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // Whose numbers the outcome charts describe. The club is not the default
  // view of a member's own work, and a member's work is not the club's.
  const [scope, setScope] = useState<'mine' | 'club'>('mine');

  useEffect(() => {
    Promise.all([
      api.analytics.breakdown(),
      api.analytics.insights().catch(() => ({ insights: [] as string[] })),
      api.campaigns.list().catch(() => [] as Campaign[]),
    ])
      .then(([data, insightResponse, campaignRows]) => {
        // Coerced at the boundary: this page is the club's statistics surface,
        // and one unexpected payload must not blank all of it.
        setBreakdown({
          sections: Array.isArray(data?.sections) ? data.sections : [],
          mine: data?.mine ?? null,
          club: data?.club,
        });
        setInsights(Array.isArray(insightResponse?.insights) ? insightResponse.insights : []);
        setCampaigns(Array.isArray(campaignRows) ? campaignRows : []);
      })
      .catch((requestError) => setError((requestError as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="flex min-h-[60vh] items-center justify-center text-slate-500">Loading stats…</div>;
  }

  const split = scope === 'mine' ? breakdown.mine : breakdown.club;
  const mailedAnything = (breakdown.club?.mailed ?? 0) > 0;

  return (
    <div className="app-workspace max-w-6xl space-y-4">
      <PageHeader
        title="Full breakdown"
        subtitle="Every measurement the database can answer. Sections appear as the data exists."
        actions={
          <Button onClick={() => api.analytics.exportCsv().catch((e) => setError((e as Error).message))}>
            Export CSV
          </Button>
        }
      />

      {error && <Notice tone="danger" className="mb-4">{error}</Notice>}

      <section className="surface-card p-5" aria-label="Outcomes">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="app-section-title">Outcomes</h2>
          <div className="flex gap-1 rounded-lg bg-pale-sky/40 p-0.5" role="group" aria-label="Whose results">
            {(['mine', 'club'] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={scope === option}
                onClick={() => setScope(option)}
                // Matches the small-button height so the control row lines
                // up; it keeps its own radius because .ui-button would break
                // the segmented seam.
                className={`min-h-9 rounded-md px-3 text-xs font-semibold ${
                  scope === option ? 'bg-white text-deep-navy shadow-sm' : 'text-slate-600 hover:text-deep-navy'}`}
              >
                {option === 'mine' ? 'Mine' : 'The club'}
              </button>
            ))}
          </div>
        </div>
        {/* The chart sat alone against a wide empty card. The headline figures
            now occupy that space, so the row carries the summary a member
            would otherwise have to compute from the slices. */}
        <div className="grid grid-cols-1 gap-5 md:grid-cols-[auto_1fr] md:items-center">
          <OutcomePie
            title={scope === 'mine' ? 'Yours' : 'The club'}
            size={132}
            slices={outcomeSlices(split)}
            empty={scope === 'mine'
              ? 'You have not mailed anyone yet.'
              : 'Nobody has mailed anyone yet.'}
          />
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ['People mailed', split?.mailed ?? 0],
              ['Replied', split?.replied ?? 0],
              ['Reply rate', `${split?.reply_rate ?? 0}%`],
              ['Bounced', split?.bounced ?? 0],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg bg-pale-sky/25 px-3 py-2">
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd className="mt-0.5 text-xl font-bold text-deep-navy">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {!mailedAnything && (
        <EmptyState
          className=""
          title="No send activity yet"
          body="Release a reviewed campaign. Delivery and reply activity appear here once the server sends it."
          action={<Link to="/" className="ui-button ui-button--primary">Go to Home</Link>}
        />
      )}

      {/* Whatever the server measured, in the order it measured it. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {breakdown.sections.map((section) => (
          <SectionChart key={section.id} section={section} />
        ))}
      </div>

      <section className="surface-card p-5" aria-label="Campaign drilldown">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="app-section-title">Campaign drilldown</h2>
          <Link to="/" className="text-sm font-semibold text-[var(--accent)] hover:underline">All campaigns</Link>
        </div>
        {campaigns.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No campaigns to compare.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {campaigns.map((campaign) => {
              const content = (
                <>
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{campaign.name}</span>
                    <span className="block text-xs text-slate-500">
                      {campaign.sent_count ?? 0} sent · {campaign.pending_count ?? 0} queued · {campaign.failed_count ?? 0} failed
                    </span>
                  </span>
                  <StatusBadge tone={campaign.status === 'sent' ? 'success' : campaign.status === 'needs_attention' ? 'danger' : 'neutral'}>
                    {campaign.status.replace('_', ' ')}
                  </StatusBadge>
                </>
              );
              return (
                <li key={campaign.id}>
                  {campaign.owner_user_id === user.id ? (
                    <Link to={`/campaigns/${campaign.id}`} className="flex min-h-14 items-center justify-between gap-4 py-3 hover:text-[var(--accent)]">
                      {content}
                    </Link>
                  ) : (
                    <div className="flex min-h-14 items-center justify-between gap-4 py-3">{content}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {insights.length > 0 && (
        <section className="surface-card p-5" aria-label="Notes from the data">
          <h2 className="app-section-title mb-3">Notes from the data</h2>
          <ul className="space-y-2">
            {insights.map((insight) => <li key={insight} className="text-sm text-slate-600">• {insight}</li>)}
          </ul>
        </section>
      )}

      <OutreachLedger />
    </div>
  );
}
