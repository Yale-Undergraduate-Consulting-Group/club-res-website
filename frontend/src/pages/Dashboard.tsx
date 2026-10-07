import { useEffect, useState } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import { api, type LeaderboardRow, type CompanyReached, type Campaign, type PipelineMetrics, type OutcomeSplit } from '../api';
import PageHeader from '../components/PageHeader';
import GmailConnection from '../components/GmailConnection';
import SlackIntegration from '../components/SlackIntegration';
import { canManageCampaign } from '../lib/campaignAccess';
import OutcomePie from '../components/OutcomePie';
import { StatusBadge } from '../components/ui/Primitives';
import CampaignOwnershipReview from '../components/campaigns/CampaignOwnershipReview';

function campaignTone(status: string): 'neutral' | 'info' | 'warning' | 'success' | 'danger' {
  if (status === 'sent') return 'success';
  if (status === 'releasing') return 'info';
  if (status === 'needs_attention') return 'danger';
  if (status === 'paused') return 'warning';
  return 'neutral';
}

const DEFAULT_DATA = {
  contacts_discovered_today: 0,
  emails_in_queue: 0,
  active_campaigns: 0,
  total_sent: 0,
  open_rate: 0,
  reply_rate: 0,
  mine: null as OutcomeSplit | null,
  club: undefined as OutcomeSplit | undefined,
  my_sectors: [] as { sector: string; count: number }[],
  club_sectors: [] as { sector: string; count: number }[],
};

// Slices use the club's chart tokens, and each one knows where its rows live:
// a chart you can click is the difference between a number and an answer.
function slicesFor(split: OutcomeSplit | null | undefined, mine: boolean) {
  const scope = mine ? '&owner=me' : '';
  return [
    { label: 'replied', value: split?.replied ?? 0, colour: 'var(--chart-good)', to: `/outreach?status=replied${scope}` },
    { label: 'awaiting a reply', value: split?.awaiting ?? 0, colour: 'var(--chart-4)', to: `/outreach?status=contacted${scope}` },
    { label: 'bounced', value: split?.bounced ?? 0, colour: 'var(--chart-bad)', to: `/?filter=needs_attention` },
    { label: 'still queued', value: split?.queued ?? 0, colour: 'var(--chart-idle)', to: `/` },
  ];
}


export default function Dashboard() {
  const { user } = useOutletContext<{ user: { id?: number; name?: string; picture?: string; role?: string } }>();
  const [data, setData] = useState<typeof DEFAULT_DATA>(DEFAULT_DATA);
  const [dueFollowUps, setDueFollowUps] = useState(0);
  const [apiError, setApiError] = useState(false);
  const [board, setBoard] = useState<LeaderboardRow[]>([]);
  const [myCompanies, setMyCompanies] = useState<CompanyReached[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [pipeline, setPipeline] = useState<PipelineMetrics['by_status']>([]);

  const refreshCampaigns = async () => {
    try {
      const rows = await api.campaigns.list();
      setCampaigns(Array.isArray(rows) ? rows : []);
    } catch {
      setCampaigns([]);
    }
  };

  useEffect(() => {
    api.analytics
      .dashboard()
      .then((d) => setData({ ...DEFAULT_DATA, ...d }))
      .catch(() => {
        setData(DEFAULT_DATA);
        setApiError(true);
      });
    api.analytics
      .dueFollowUps()
      .then((d) => setDueFollowUps(d?.count ?? 0))
      .catch(() => setDueFollowUps(0));
    // Every list is coerced: the home page must not blank because one
    // endpoint answered with something other than an array.
    api.analytics
      .leaderboard()
      .then((d) => setBoard(Array.isArray(d?.leaderboard) ? d.leaderboard : []))
      .catch(() => setBoard([]));
    api.campaigns
      .list()
      .then((d) => setCampaigns(Array.isArray(d) ? d : []))
      .catch(() => setCampaigns([]));
    api.outreach
      .pipelineMetrics()
      .then((d) => setPipeline(Array.isArray(d?.by_status) ? d.by_status : []))
      .catch(() => setPipeline([]));
  }, []);

  useEffect(() => {
    if (!user?.id) return;
    api.analytics
      .companiesReached(user.id)
      .then((d) => setMyCompanies(Array.isArray(d?.companies) ? d.companies : []))
      .catch(() => setMyCompanies([]));
  }, [user?.id]);

  const myCampaigns = [...campaigns]
    .filter((c) => canManageCampaign(c, user?.id))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  const needsAttention = myCampaigns.filter((c) => c.status === 'needs_attention' || c.status === 'paused');
  const orphanedCampaigns = campaigns.filter((c) => !c.owner_user_id && !c.sender_user_id);
  const pipelineTotal = pipeline.reduce((sum, p) => sum + p.count, 0);

  return (
    <div className="app-workspace max-w-[1920px]">
      <PageHeader title="Home" />
      {apiError && (
        <p className="ui-notice ui-notice--warning mb-4">
          Live metrics are temporarily unavailable. The work pages remain usable.
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 items-start">
        <div className="space-y-6 min-w-0">
          {needsAttention.length > 0 && (
            <section className="surface-card p-5" aria-label="Needs your attention">
              <h2 className="app-section-title mb-3">Needs your attention</h2>
              <ul className="space-y-1">
                {needsAttention.map((c) => (
                  <li key={c.id}>
                    <Link
                      to={`/campaigns/${c.id}`}
                      className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-pale-sky/30"
                    >
                      <span className="font-medium text-deep-navy truncate">{c.name}</span>
                      <span className="text-xs font-semibold text-red-700 whitespace-nowrap">
                        {c.status === 'needs_attention' ? `${c.failed_count || 0} failed` : 'Paused'}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {user.role === 'admin' && orphanedCampaigns.length > 0 && (
            <section className="surface-card p-5" aria-label="Needs ownership review">
              <h2 className="app-section-title mb-1">Needs ownership review</h2>
              <p className="text-sm text-slate-600 mb-3">
                These campaigns have no account on record. They cannot send until one is confirmed.
              </p>
              <div className="space-y-3">
                {orphanedCampaigns.map((c) => (
                  <div key={c.id}>
                    <p className="font-medium text-deep-navy">{c.name}</p>
                    <CampaignOwnershipReview campaignId={c.id} onResolved={refreshCampaigns} />
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* The standalone Campaigns page is disabled - a member's whole
              campaign list lives here now, not just the ones needing
              attention above. Creating one still only happens through
              Drafts/Find people; an empty campaign with nobody in it was
              never a page worth keeping. */}
          <section className="surface-card p-5" aria-labelledby="my-campaigns-title">
            <h2 id="my-campaigns-title" className="app-section-title mb-3">Your campaigns</h2>
            {myCampaigns.length === 0 ? (
              <p className="text-sm text-slate-500">
                Nothing built yet. Draft a message in Find people or Drafts, then build the campaign.
              </p>
            ) : (
              <ul className="space-y-1">
                {myCampaigns.slice(0, 8).map((c) => (
                  <li key={c.id}>
                    <Link
                      to={`/campaigns/${c.id}`}
                      className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-pale-sky/30"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="font-medium text-deep-navy truncate">{c.name}</span>
                        <span className="block text-xs text-slate-500">
                          {c.contact_count ?? 0} recipients · {c.sent_count ?? 0} sent
                        </span>
                      </span>
                      <StatusBadge tone={campaignTone(c.status)}>{c.status.replace('_', ' ')}</StatusBadge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="surface-card p-4">
              <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">Sending</div>
              <div className="mt-1 text-2xl font-bold text-deep-navy">{data.active_campaigns}</div>
              <div className="text-xs text-slate-500">active · {data.emails_in_queue} queued</div>
            </div>
            <Link to="/outreach" className="surface-card p-4 block hover:bg-pale-sky/10">
              <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">Follow-ups due</div>
              <div className="mt-1 text-2xl font-bold text-deep-navy">{dueFollowUps}</div>
              <div className="text-xs text-slate-500">in the pipeline</div>
            </Link>
            <Link to="/analytics" className="surface-card p-4 block hover:bg-pale-sky/10">
              <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">Reply rate</div>
              <div className="mt-1 text-2xl font-bold text-deep-navy">{data.reply_rate}%</div>
              <div className="text-xs text-slate-500">{data.total_sent} sent</div>
            </Link>
          </div>

          <section className="surface-card p-5" aria-label="Results">
            <div className="flex items-center justify-between gap-2 mb-1">
              <h2 className="app-section-title">Results</h2>
              <Link to="/analytics" className="text-xs font-semibold text-steel-blue hover:underline">
                Full breakdown
              </Link>
            </div>
            {/* Yours and the club's side by side. A club total hides whether
                anyone is doing anything; a personal total hides whether the
                club is. Both are on the front page so neither is the only
                story a member sees. */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mt-3">
              <OutcomePie
                title="Yours"
                slices={slicesFor(data.mine, true)}
                empty="You have not mailed anyone yet. Pick companies that interest you and start there."
              />
              <OutcomePie
                title="The club"
                slices={slicesFor(data.club, false)}
                empty="Nobody has mailed anyone yet."
              />
            </div>
            {(data.my_sectors.length > 0 || data.club_sectors.length > 0) && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mt-5 pt-4 border-t border-[var(--border)]">
                <div>
                  <p className="text-[13px] font-semibold text-deep-navy">What you work on</p>
                  {data.my_sectors.length === 0 ? (
                    <p className="text-xs text-slate-500 mt-1">
                      Nothing yet — whatever you choose becomes your own list, not a handed-down one.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-1">
                      {data.my_sectors.map((s) => (
                        <li key={s.sector} className="text-xs text-slate-700">
                          <div className="flex items-center justify-between gap-2">
                            <span>{s.sector}</span>
                            <span className="font-semibold text-deep-navy">{s.count}</span>
                          </div>
                          <div className="mt-0.5 h-1.5 rounded-full bg-pale-sky/60">
                            <div
                              className="h-1.5 rounded-full bg-steel-blue"
                              style={{ width: `${Math.round((s.count / data.my_sectors[0].count) * 100)}%` }}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="text-[13px] font-semibold text-deep-navy">What the club works on</p>
                  <ul className="mt-2 space-y-1">
                    {data.club_sectors.map((s) => (
                      <li key={s.sector} className="text-xs text-slate-700">
                        <div className="flex items-center justify-between gap-2">
                          <span>{s.sector}</span>
                          <span className="font-semibold text-deep-navy">{s.count}</span>
                        </div>
                        <div className="mt-0.5 h-1.5 rounded-full bg-pale-sky/60">
                          <div
                            className="h-1.5 rounded-full bg-deep-navy/60"
                            style={{ width: `${Math.round((s.count / data.club_sectors[0].count) * 100)}%` }}
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </section>

          <section className="surface-card p-5" aria-label="Pipeline">
            <div className="flex items-center justify-between gap-2 mb-3">
              <h2 className="app-section-title">Pipeline</h2>
              <Link to="/outreach" className="text-xs font-semibold text-steel-blue">
                Open board
              </Link>
            </div>
            {pipelineTotal === 0 ? (
              <p className="text-sm text-slate-500">No contacts in the pipeline yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {pipeline.map((p) => (
                  <Link
                    key={p.pipeline_status}
                    to="/outreach"
                    className="rounded-lg bg-pale-sky/30 px-3 py-2 hover:bg-pale-sky/50 min-w-[5.5rem]"
                    title={`${p.count} of ${pipelineTotal} contacts (${Math.round((p.count / pipelineTotal) * 100)}%) are ${p.pipeline_status}`}
                  >
                    <span className="block text-xs text-slate-600 capitalize">{p.pipeline_status}</span>
                    <span className="block text-lg font-bold text-deep-navy">{p.count}</span>
                  </Link>
                ))}
              </div>
            )}
          </section>

          <section className="surface-card p-6" aria-label="Leaderboard">
            {board.length === 0 ? (
              <p className="text-sm text-slate-500">No sends recorded yet.</p>
            ) : (
              <ol className="space-y-2">
                {board.map((row, i) => (
                  <li
                    key={row.user_id}
                    className={`flex items-center gap-3 rounded-xl px-3 py-2 ${
                      row.user_id === user?.id ? 'bg-pale-sky/40' : ''
                    }`}
                  >
                    <span className="w-5 text-sm font-semibold text-slate-500">{i + 1}</span>
                    {row.picture && (
                      <img src={row.picture} alt="" className="w-7 h-7 rounded-full" referrerPolicy="no-referrer" />
                    )}
                    <span className="flex-1 text-sm font-medium text-deep-navy truncate">
                      {row.name || 'Member'}
                      {row.user_id === user?.id ? ' (you)' : ''}
                    </span>
                    <span className="text-xs text-slate-500">
                      {row.replied} replied · {row.companies_reached} companies
                      {row.penalized_bounces ? ` · ${row.penalized_bounces} bounced` : ''}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>

        <div className="space-y-6 min-w-0">
          <GmailConnection />
          <SlackIntegration />
          <section className="surface-card p-5" aria-labelledby="my-companies-title">
            <div className="mb-1">
              <h2 id="my-companies-title" className="app-section-title">
                Companies you've reached
              </h2>
              <Link to="/outreach" className="text-xs font-semibold text-steel-blue">
                View pipeline
              </Link>
            </div>
            {myCompanies.length === 0 ? (
              <p className="text-sm text-slate-500 mt-2">
                Nothing sent yet. Check here before starting outreach somewhere a teammate
                already covered.
              </p>
            ) : (
              <ul className="mt-3 space-y-2">
                {myCompanies.slice(0, 8).map((c) => (
                  <li key={c.company} className="text-sm">
                    <span className="font-medium text-deep-navy">{c.company}</span>
                    <span className="text-slate-500">
                      {' '}
                      · {c.contacts_reached} contact{c.contacts_reached === 1 ? '' : 's'}
                      {c.replies ? ` · ${c.replies} replied` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
