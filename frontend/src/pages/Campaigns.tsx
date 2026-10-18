import { canManageCampaign, campaignAccessLabel } from '../lib/campaignAccess';
import CampaignOwnershipReview from '../components/campaigns/CampaignOwnershipReview';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import { api, type Campaign } from '../api';
import PageHeader from '../components/PageHeader';
import { Button, ConfirmDialog, EmptyState, Notice, StatusBadge } from '../components/ui/Primitives';

function campaignTone(status: string): 'neutral' | 'info' | 'warning' | 'success' | 'danger' {
  if (status === 'sent') return 'success';
  if (status === 'releasing') return 'info';
  if (status === 'needs_attention') return 'danger';
  if (status === 'paused') return 'warning';
  return 'neutral';
}

export default function Campaigns() {
  const navigate = useNavigate();
  const { user } = useOutletContext<{ user: { id: number; role: string } }>();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<Campaign | null>(null);

  const refresh = async () => {
    try {
      setCampaigns(await api.campaigns.list());
      setError('');
    } catch (requestError) {
      setError((requestError as Error).message);
    }
  };

  useEffect(() => {
    api.campaigns.list().then((rows) => {
      setCampaigns(rows);
      setError('');
    }).catch((requestError) => {
      setError((requestError as Error).message);
    });
  }, []);

  useEffect(() => {
    if (!campaigns.some((campaign) => campaign.status === 'releasing')) return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [campaigns]);

  const createCampaign = async () => {
    if (!newName.trim()) return;
    setLoading(true);
    setError('');
    try {
      const campaign = await api.campaigns.create(newName.trim());
      setNewName('');
      navigate(`/campaigns/${campaign.id}`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const deleteCampaign = async () => {
    if (!deleteTarget) return;
    setLoading(true);
    try {
      await api.campaigns.delete(deleteTarget.id);
      setDeleteTarget(null);
      await refresh();
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-workspace max-w-6xl">
      <PageHeader
        title="Campaigns"
        subtitle="Prepare and send from your own account. Shared summaries show the club’s outreach activity."
      />

      {error && <Notice tone="danger" className="mb-5">{error}</Notice>}

      <section className="surface-card rounded-xl p-5 mb-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 text-sm font-semibold text-deep-navy">
            New campaign
            <input
              id="create-campaign-input"
              type="text"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void createCampaign();
              }}
              placeholder="Campaign name"
              className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-white px-3 text-slate-800"
            />
          </label>
          <Button onClick={createCampaign} disabled={loading || !newName.trim()}>
            {loading ? 'Creating…' : 'Create campaign'}
          </Button>
        </div>
      </section>

      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="app-section-title">Mail campaigns</h2>
        <Button variant="secondary" onClick={refresh}>Refresh</Button>
      </div>

      {campaigns.length === 0 ? (
        <EmptyState
          title="No mail campaigns"
          body="Prepare a draft, select recipients, and create a campaign for review."
          action={<Link className="ui-button ui-button--primary" to="/studio">Open drafts</Link>}
        />
      ) : (
        <div className="space-y-3">
          {campaigns.map((campaign) => {
            const sent = campaign.sent_count ?? 0;
            const total = campaign.contact_count ?? 0;
            const progress = total ? Math.round((sent / total) * 100) : 0;
            return (
              <article key={campaign.id} className="surface-card rounded-xl p-4">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  <button disabled={!canManageCampaign(campaign, user.id)} className="min-w-0 flex-1 text-left disabled:cursor-default" onClick={() => navigate(`/campaigns/${campaign.id}`)}>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-semibold text-deep-navy">{campaign.name}</h3>
                      <StatusBadge>{campaignAccessLabel(campaign, user.id)}</StatusBadge>
                      <StatusBadge tone={campaignTone(campaign.status)}>{campaign.status.replace('_', ' ')}</StatusBadge>
                    </div>
                    <p className="mt-1 text-sm text-slate-600">
                      {total} recipients · {sent} sent · {campaign.pending_count ?? 0} queued
                      {(campaign.failed_count ?? 0) > 0 ? ` · ${campaign.failed_count} failed` : ''}
                    </p>
                    <div
                      className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-200"
                      role="progressbar"
                      aria-valuenow={progress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      title={`${progress}% sent (${sent} of ${total})`}
                    >
                      <div className="h-full bg-[var(--accent)]" style={{ width: `${progress}%` }} />
                    </div>
                  </button>
                  <div className="flex gap-2">
                    {canManageCampaign(campaign, user.id) && <Button variant="secondary" onClick={() => navigate(`/campaigns/${campaign.id}`)}>Review</Button>}
                    {canManageCampaign(campaign, user.id) && campaign.status !== 'releasing' && (
                      <Button variant="danger" onClick={() => setDeleteTarget(campaign)}>Delete</Button>
                    )}
                  </div>
                </div>
                {user.role === 'admin' && !campaign.owner_user_id && !campaign.sender_user_id && <CampaignOwnershipReview campaignId={campaign.id} onResolved={refresh} />}
              </article>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget != null}
        title="Delete campaign?"
        body={deleteTarget ? `“${deleteTarget.name}” and its recipient queue will be permanently deleted.` : ''}
        confirmLabel="Delete campaign"
        danger
        busy={loading}
        onClose={() => setDeleteTarget(null)}
        onConfirm={deleteCampaign}
      />
    </div>
  );
}
