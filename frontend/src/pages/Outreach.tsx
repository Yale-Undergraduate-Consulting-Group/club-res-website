import type { Contact, Template, Sequence, PipelineMetrics, ContactNote, ContactActivity, ContactProfile, Worklist, CompanySummaryRow } from '../api';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import AppTabMenu from '../components/AppTabMenu';
import PageHeader from '../components/PageHeader';
import TrackingSync from '../components/TrackingSync';
import FollowUpSchedule from '../components/outreach/FollowUpSchedule';
import { useUrlTab } from '../lib/useUrlTab';

const PIPELINE_STATUSES = ['cold', 'contacted', 'replied', 'meeting', 'closed'];

function groupContactsByCompany(contacts: Contact[]): { company: string; contacts: Contact[] }[] {
  const byCompany = new Map<string, Contact[]>();
  for (const c of contacts) {
    const key = (c.company || '').trim() || 'No company';
    if (!byCompany.has(key)) byCompany.set(key, []);
    byCompany.get(key)!.push(c);
  }
  return Array.from(byCompany.entries())
    .map(([company, contacts]) => ({ company, contacts }))
    .sort((a, b) => (a.company === 'No company' ? 1 : b.company === 'No company' ? -1 : a.company.localeCompare(b.company)));
}

function ContactCard({ c, selectedContact, selectedIds, onSelect, onToggleSelect, onUpdatePipeline, draggable }: {
  c: Contact; selectedContact: Contact | null; selectedIds: Set<number>; onSelect: (c: Contact) => void; onToggleSelect: (id: number) => void; onUpdatePipeline: (id: number, status: string) => void; draggable: boolean;
}) {
  return (
    <div
      draggable={draggable}
      onDragStart={(e) => { e.dataTransfer.setData('contactId', String(c.id)); e.dataTransfer.setData('currentStatus', c.pipeline_status || 'cold'); }}
      onClick={() => onSelect(c)}
      className={`p-3 rounded-lg cursor-pointer border transition-colors ${
        selectedContact?.id === c.id
          ? 'border-[var(--btn-primary-bg)] bg-[var(--btn-primary-bg)]/5 ring-1 ring-[var(--btn-primary-bg)]'
          : 'border-pale-sky hover:border-[var(--btn-primary-hover)] hover:bg-pale-sky/10'
      } ${selectedIds.has(c.id) ? 'ring-2 ring-amber-500' : ''}`}
    >
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={selectedIds.has(c.id)}
          onChange={(e) => { e.stopPropagation(); onToggleSelect(c.id); }}
          onClick={(e) => e.stopPropagation()}
          aria-label={`Select ${c.name || c.email}`}
          className="rounded shrink-0"
        />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-slate-800 truncate text-sm">{c.name || c.email}</div>
          <div className="text-xs text-slate-500 truncate mt-0.5">{c.company || c.email}</div>
          {c.last_sent_at && (
            <div className="text-[11px] text-slate-400 truncate mt-0.5">
              Last send{c.last_campaign_name ? `: ${c.last_campaign_name}` : ''} · {String(c.last_sent_at).slice(0, 10)}
            </div>
          )}
        </div>
      </div>
      <select
        value={c.pipeline_status || 'cold'}
        onChange={(e) => { e.stopPropagation(); onUpdatePipeline(c.id, e.target.value); }}
        onClick={(e) => e.stopPropagation()}
        className="mt-2 w-full text-xs px-2 py-1.5 rounded border border-pale-sky bg-white"
      >
        {PIPELINE_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    </div>
  );
}

// What the send ledger says about a company, in the order a member asks it:
// did we mail them, did anyone answer, did anything bounce. A bounce is shown
// even when it is the only thing that happened, because it means the format
// for this company is wrong and every other address here shares it.
function outcomeLine(outcome?: CompanySummaryRow): string {
  if (!outcome) return '';
  const parts: string[] = [];
  if (outcome.mailed_count) parts.push(`${outcome.mailed_count} mailed`);
  if (outcome.replied_count) parts.push(`${outcome.replied_count} replied`);
  if (outcome.bounced_count) parts.push(`${outcome.bounced_count} bounced`);
  if (outcome.queued_count) parts.push(`${outcome.queued_count} queued`);
  if (!parts.length) return 'not contacted yet';
  return parts.join(' · ');
}

function CompanyFolder({ company, contacts, outcome, selectedContact, selectedIds, onSelect, onToggleSelect, onUpdatePipeline, draggable }: {
  company: string; contacts: Contact[]; outcome?: CompanySummaryRow; selectedContact: Contact | null; selectedIds: Set<number>; onSelect: (c: Contact) => void; onToggleSelect: (id: number) => void; onUpdatePipeline: (id: number, status: string) => void; draggable: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="rounded-lg border border-pale-sky overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full px-3 py-2 flex items-center justify-between bg-pale-sky/20 hover:bg-pale-sky/30 text-left text-sm font-medium text-deep-navy"
      >
        <span className="min-w-0 flex-1">
          <span className="truncate block">{company}</span>
          <span className={`block text-xs font-normal ${outcome?.bounced_count ? 'text-red-700' : 'text-slate-500'}`}>
            {outcomeLine(outcome)}
          </span>
        </span>
        <span className="text-slate-500 text-xs shrink-0 ml-2">({contacts.length})</span>
        <span className="text-slate-500">{expanded ? '▼' : '▶'}</span>
      </button>
      {expanded && (
        <div className="p-2 space-y-1 bg-white">
          {contacts.map((c) => (
            <ContactCard key={c.id} c={c} selectedContact={selectedContact} selectedIds={selectedIds} onSelect={onSelect} onToggleSelect={onToggleSelect} onUpdatePipeline={onUpdatePipeline} draggable={draggable} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Outreach() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useUrlTab<'pipeline' | 'worklists' | 'followups' | 'resources'>(['pipeline', 'worklists', 'followups', 'resources'], 'pipeline');
  const [groupByCompany, setGroupByCompany] = useState(false);
  const [mobileStatus, setMobileStatus] = useState('cold');
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [companyOutcomes, setCompanyOutcomes] = useState<Record<string, CompanySummaryRow>>({});
  const [templates, setTemplates] = useState<Template[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [pipelineMetrics, setPipelineMetrics] = useState<PipelineMetrics | null>(null);
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [notes, setNotes] = useState<ContactNote[]>([]);
  const [activities, setActivities] = useState<ContactActivity[]>([]);
  const [profile, setProfile] = useState<ContactProfile | null>(null);
  const [newNote, setNewNote] = useState('');
  const [newActivityType, setNewActivityType] = useState('email_sent');
  const [newActivityDetails, setNewActivityDetails] = useState('');
  const [emailVerified, setEmailVerified] = useState<boolean | null>(null);
  const [templateForm, setTemplateForm] = useState({ name: '', subject: '', body: '', industry: '', use_case: '' });
  const [sequenceForm, setSequenceForm] = useState({ name: '', steps: [{ days_after: 3, subject: '', body: '' }] });
  const [loading, setLoading] = useState(false);
  const [outreachCampaigns, setOutreachCampaigns] = useState<Worklist[]>([]);
  const [campaignForm, setCampaignForm] = useState({ name: '', type: 'individual' as 'community' | 'individual', description: '' });
  const [selectedCampaign, setSelectedCampaign] = useState<Worklist | null>(null);
  const [campaignContacts, setCampaignContacts] = useState<Contact[]>([]);
  const [contactSearch, setContactSearch] = useState('');
  // Read from the URL so a chart slice on Home can open exactly its rows
  // instead of dropping the member on an unfiltered table to find them again.
  const [searchParams, setSearchParams] = useSearchParams();
  const contactPipelineFilter = searchParams.get('status') || '';
  // "Yours" on the front page has to mean yours here too, or the split the
  // dashboard draws is a lie one click later.
  const mineOnly = searchParams.get('owner') === 'me';
  const setContactPipelineFilter = useCallback((next: string) => {
    setSearchParams((current) => {
      const updated = new URLSearchParams(current);
      if (next) updated.set('status', next);
      else updated.delete('status');
      return updated;
    }, { replace: true });
  }, [setSearchParams]);
  const [selectedContactIds, setSelectedContactIds] = useState<Set<number>>(new Set());
  const [inboxSyncBusy, setInboxSyncBusy] = useState(false);
  const [pipelineSortBusy, setPipelineSortBusy] = useState(false);
  const [inboxSyncBanner, setInboxSyncBanner] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const refreshContactsAndMetrics = async () => {
    const params: { q?: string; pipeline_status?: string; limit: number; mine_only?: boolean } = { limit: 1000 };
    if (contactSearch.trim()) params.q = contactSearch.trim();
    if (contactPipelineFilter) params.pipeline_status = contactPipelineFilter;
    if (mineOnly) params.mine_only = true;
    try {
      const [page, metrics] = await Promise.all([
        api.contacts.list(params),
        api.outreach.pipelineMetrics(),
      ]);
      setContacts(page.items);
      setPipelineMetrics(metrics);
    } catch {
      setContacts([]);
      setPipelineMetrics(null);
    }
  };

  useEffect(() => {
    const params: { q?: string; pipeline_status?: string; limit: number; mine_only?: boolean } = { limit: 1000 };
    if (contactSearch.trim()) params.q = contactSearch.trim();
    if (contactPipelineFilter) params.pipeline_status = contactPipelineFilter;
    if (mineOnly) params.mine_only = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.contacts.list(params, controller.signal)
        .then((page) => setContacts(page.items))
        .catch((error) => {
          if (!(error instanceof DOMException && error.name === 'AbortError')) setContacts([]);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [contactSearch, contactPipelineFilter, mineOnly]);

  useEffect(() => {
    api.outreach.templates.list().then(setTemplates).catch(() => setTemplates([]));
    api.outreach.sequences.list().then(setSequences).catch(() => setSequences([]));
    api.outreach.pipelineMetrics().then(setPipelineMetrics).catch(() => setPipelineMetrics(null));
    api.outreach.campaigns.list().then(setOutreachCampaigns).catch(() => setOutreachCampaigns([]));
    // Grouped by company, the question is what already happened here, and the
    // answer lives in the send ledger rather than on the contact rows.
    api.contacts.companiesSummary()
      .then((rows) => setCompanyOutcomes(Object.fromEntries(
        rows.map((row) => [row.company.trim().toLowerCase(), row]))))
      .catch(() => setCompanyOutcomes({}));
  }, []);

  useEffect(() => {
    if (!selectedCampaign?.id) return;
    api.outreach.campaigns.get(selectedCampaign.id).then((c) => {
      setCampaignContacts(c.contacts || []);
    }).catch(() => setCampaignContacts([]));
  }, [selectedCampaign?.id]);

  useEffect(() => {
    if (!selectedContact?.id) return;
    api.outreach.notes.list(selectedContact.id).then(setNotes).catch(() => setNotes([]));
    api.outreach.activities.list(selectedContact.id).then(setActivities).catch(() => setActivities([]));
    api.outreach.profile.get(selectedContact.id).then(setProfile).catch(() => setProfile(null));
  }, [selectedContact?.id]);

  const updatePipeline = async (contactId: number, status: string) => {
    try {
      await api.outreach.updatePipeline(contactId, status);
      setContacts((prev) =>
        prev.map((c) => (c.id === contactId ? { ...c, pipeline_status: status } : c))
      );
      if (selectedContact?.id === contactId) setSelectedContact((p: Contact | null) => (p ? { ...p, pipeline_status: status } : null));
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    }
  };

  const toggleContactSelection = (id: number) => {
    setSelectedContactIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleColumnDrop = (newStatus: string) => (e: React.DragEvent) => {
    e.preventDefault();
    const contactId = e.dataTransfer.getData('contactId');
    if (contactId) updatePipeline(parseInt(contactId, 10), newStatus);
  };

  const handleBulkMove = async (newStatus: string) => {
    if (selectedContactIds.size === 0) return;
    for (const id of selectedContactIds) {
      await updatePipeline(id, newStatus);
    }
    setSelectedContactIds(new Set());
  };

  const handleSyncInboxReplies = async () => {
    setInboxSyncBusy(true);
    setInboxSyncBanner(null);
    try {
      const r = await api.outreach.syncInboxReplies(true);
      if (!r.ok) {
        const msg =
          r.error === 'no_gmail_token'
            ? 'Sign in with Google. If you signed in before inbox sync existed, sign out and sign in again so the app can request inbox read access.'
            : r.message || 'Sync failed.';
        setInboxSyncBanner({ type: 'err', text: msg });
        return;
      }
      if (r.in_progress) {
        setInboxSyncBanner({ type: 'ok', text: 'Gmail sync started. Tracking will update when it finishes.' });
        return;
      }
      await refreshContactsAndMetrics();
      const errNote =
        Array.isArray(r.errors) && r.errors.length > 0
          ? ` Some threads could not be checked (${r.errors.length}); try re-authenticating if you see permission errors.`
          : '';
      setInboxSyncBanner({
        type: 'ok',
        text: `Inbox sync: ${r.marked_replied ?? 0} contact(s) marked replied, ${r.pipeline_promoted_contacted ?? 0} moved cold → contacted.${errNote}`,
      });
    } catch (e) {
      setInboxSyncBanner({ type: 'err', text: (e as Error)?.message || 'Sync failed.' });
    } finally {
      setInboxSyncBusy(false);
    }
  };

  const handleAutoSortPipeline = async () => {
    setPipelineSortBusy(true);
    setInboxSyncBanner(null);
    try {
      const r = await api.outreach.autoSortPipeline();
      await refreshContactsAndMetrics();
      setInboxSyncBanner({
        type: 'ok',
        text: `Pipeline sort: promoted ${r.promoted ?? 0} contact(s) from cold to contacted (sent, no reply yet, sent by you).`,
      });
    } catch (e) {
      setInboxSyncBanner({ type: 'err', text: (e as Error)?.message || 'Could not auto-sort pipeline.' });
    } finally {
      setPipelineSortBusy(false);
    }
  };

  const handleBulkAddToCampaign = async (campaignId: number) => {
    if (selectedContactIds.size === 0) return;
    const n = selectedContactIds.size;
    const ids = Array.from(selectedContactIds);
    try {
      await api.outreach.campaigns.addContacts(campaignId, ids);
      setSelectedContactIds(new Set());
      api.outreach.campaigns.list().then(setOutreachCampaigns).catch(() => {});
      if (selectedCampaign?.id === campaignId) {
        api.outreach.campaigns.get(campaignId).then((c) => setCampaignContacts(c.contacts || [])).catch(() => {});
      }
      alert(`Added ${n} contact(s) to campaign.`);
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    }
  };

  const handleBulkDeleteContacts = async () => {
    if (selectedContactIds.size === 0) return;
    const ids = Array.from(selectedContactIds);
    const n = ids.length;
    if (!window.confirm(`Are you sure? Permanently delete ${n} contact(s)? This removes them from campaigns, notes, and the database.`)) return;
    try {
      const res = await api.contacts.bulkDelete(ids);
      setSelectedContactIds(new Set());
      if (selectedContact?.id && ids.includes(selectedContact.id)) setSelectedContact(null);
      const params: { q?: string; pipeline_status?: string; limit: number } = { limit: 1000 };
      if (contactSearch.trim()) params.q = contactSearch.trim();
      if (contactPipelineFilter) params.pipeline_status = contactPipelineFilter;
      api.contacts.list(params).then((page) => setContacts(page.items)).catch(() => setContacts([]));
      if (res.skipped > 0) {
        alert(`Deleted ${res.deleted}. ${res.skipped} skipped (not allowed or missing).`);
      } else {
        alert(`Deleted ${res.deleted} contact(s).`);
      }
    } catch (e) {
      alert((e as Error)?.message || 'Bulk delete failed');
    }
  };

  const addNote = async () => {
    if (!selectedContact?.id || !newNote.trim()) return;
    try {
      await api.outreach.notes.create(selectedContact.id, newNote.trim());
      setNewNote('');
      api.outreach.notes.list(selectedContact.id).then(setNotes).catch(() => {});
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    }
  };

  const addActivity = async () => {
    if (!selectedContact?.id) return;
    try {
      await api.outreach.activities.create(selectedContact.id, newActivityType, newActivityDetails || undefined);
      setNewActivityDetails('');
      api.outreach.activities.list(selectedContact.id).then(setActivities).catch(() => {});
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    }
  };

  const saveTemplate = async () => {
    if (!templateForm.name || !templateForm.subject || !templateForm.body) return;
    setLoading(true);
    try {
      await api.outreach.templates.create({
        name: templateForm.name,
        subject: templateForm.subject,
        body: templateForm.body,
        industry: templateForm.industry || undefined,
        use_case: templateForm.use_case || undefined,
      });
      setTemplateForm({ name: '', subject: '', body: '', industry: '', use_case: '' });
      api.outreach.templates.list().then(setTemplates).catch(() => {});
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    } finally {
      setLoading(false);
    }
  };

  const saveSequence = async () => {
    if (!sequenceForm.name || !sequenceForm.steps.length) return;
    setLoading(true);
    try {
      await api.outreach.sequences.create(
        sequenceForm.name,
        sequenceForm.steps.filter((s) => s.subject || s.body)
      );
      setSequenceForm({ name: '', steps: [{ days_after: 3, subject: '', body: '' }] });
      api.outreach.sequences.list().then(setSequences).catch(() => {});
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    } finally {
      setLoading(false);
    }
  };

  const verifyEmail = async () => {
    if (!selectedContact?.email) return;
    try {
      const r = await api.outreach.verifyEmail(selectedContact.email);
      setEmailVerified(r.valid);
    } catch {
      setEmailVerified(false);
    }
  };

  const refreshProfile = async () => {
    if (!selectedContact?.id) return;
    setLoading(true);
    try {
      const p = await api.outreach.profile.refresh(selectedContact.id);
      setProfile(p);
    } catch (e) {
      alert((e as Error)?.message || 'Failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-workspace w-full max-w-[1920px]">
      <TrackingSync onSynced={refreshContactsAndMetrics} />
      <PageHeader
        title="Pipeline"
        subtitle="Track relationships from first contact through completed engagements."
      />

      <div className="mb-6">
        <AppTabMenu
          tabs={[
            { id: 'pipeline', label: 'Pipeline' },
            { id: 'worklists', label: 'Worklists' },
            { id: 'followups', label: 'Follow-ups' },
            { id: 'resources', label: 'Email resources' },
          ]}
          active={activeTab}
          onChange={(id) => setActiveTab(id as typeof activeTab)}
          label="Pipeline views"
        />
        {activeTab === 'resources' && (
          <p className="app-tab-panel text-sm text-slate-600 dark:text-slate-400">
            Templates hold reusable copy. Follow-up sequences and their schedule have their own tab.
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* List or full-width pipeline board */}
        <div className={activeTab === 'pipeline' ? 'lg:col-span-3' : activeTab === 'worklists' ? 'lg:col-span-2 space-y-4' : 'lg:col-span-1 space-y-4'}>
          {activeTab === 'pipeline' && (
            <div className="space-y-6">
              <div className="flex flex-wrap items-center gap-3">
                <input
                  type="search"
                  placeholder="Search name, email, company... (press /)"
                  value={contactSearch}
                  onChange={(e) => setContactSearch(e.target.value)}
                  className="px-3 py-2 rounded-lg border border-pale-sky text-sm w-56 max-w-full"
                  aria-label="Search contacts"
                  data-search-input
                />
                <select
                  value={contactPipelineFilter}
                  onChange={(e) => setContactPipelineFilter(e.target.value)}
                  className="px-3 py-2 rounded-lg border border-pale-sky text-sm bg-white"
                  aria-label="Filter by pipeline status"
                >
                  <option value="">All Statuses</option>
                  {PIPELINE_STATUSES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={groupByCompany}
                    onChange={(e) => setGroupByCompany(e.target.checked)}
                  />
                  Group by company
                </label>
                <span className="text-xs text-slate-500 hidden sm:inline">Merge contacts into company folders</span>
                <span className="w-px h-6 bg-pale-sky hidden sm:block" aria-hidden />
                <button
                  type="button"
                  onClick={() => void handleSyncInboxReplies()}
                  disabled={inboxSyncBusy}
                  className="ui-button ui-button--secondary ui-button--sm"
                  title="Scan Gmail for replies on campaign threads you sent, then refresh this board"
                >
                  {inboxSyncBusy ? 'Syncing inbox…' : 'Sync inbox (Gmail)'}
                </button>
                <button
                  type="button"
                  onClick={() => void handleAutoSortPipeline()}
                  disabled={pipelineSortBusy}
                  className="ui-button ui-button--secondary ui-button--sm"
                  title="Move cold → contacted when you already sent from a campaign but they have not replied yet (no Gmail call)"
                >
                  {pipelineSortBusy ? 'Sorting…' : 'Auto-sort pipeline'}
                </button>
              </div>
              <label className="md:hidden flex items-center gap-2 text-sm font-medium text-deep-navy">
                Stage
                <select
                  value={mobileStatus}
                  onChange={(event) => setMobileStatus(event.target.value)}
                  className="min-h-11 flex-1 rounded-lg border border-[var(--border)] bg-white px-3"
                >
                  {PIPELINE_STATUSES.map((status) => (
                    <option key={status} value={status}>{status}</option>
                  ))}
                </select>
              </label>
              {inboxSyncBanner && (
                <p
                  className={`text-sm rounded-lg px-3 py-2 border ${
                    inboxSyncBanner.type === 'ok'
                      ? 'bg-emerald-50 border-emerald-200 text-emerald-900 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-100'
                      : 'bg-amber-50 border-amber-200 text-amber-950 dark:bg-amber-950/50 dark:border-amber-800 dark:text-amber-100'
                  }`}
                >
                  {inboxSyncBanner.text}
                </p>
              )}
              {selectedContactIds.size > 0 && (
                <div className="flex flex-wrap items-center gap-2 p-3 rounded-xl surface-card border border-[var(--border)] shadow-sm">
                  <span className="text-sm font-medium text-deep-navy dark:text-[var(--text-primary)]">{selectedContactIds.size} selected</span>
                  <select
                    onChange={(e) => { const v = e.target.value; if (v) handleBulkMove(v); e.target.value = ''; }}
                    className="text-sm px-2 py-1.5 rounded-lg border border-[var(--border)] bg-white dark:bg-slate-700 text-deep-navy dark:text-slate-100"
                  >
                    <option value="">Move To...</option>
                    {PIPELINE_STATUSES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <select
                    onChange={(e) => { const v = e.target.value; if (v) handleBulkAddToCampaign(parseInt(v, 10)); e.target.value = ''; }}
                    className="text-sm px-2 py-1.5 rounded-lg border border-[var(--border)] bg-white dark:bg-slate-700 text-deep-navy dark:text-slate-100"
                  >
                    <option value="">Add To Campaign...</option>
                    {outreachCampaigns.map((oc) => (
                      <option key={oc.id} value={oc.id}>{oc.name}</option>
                    ))}
                  </select>
                  {/* Hands the exact people ticked to Drafts, where each
                      gets an advisory draft of their own - the same panel
                      Find people and Drafts' own sidebar open. */}
                  <button
                    type="button"
                    onClick={() => {
                      const chosen = contacts.filter((c) => selectedContactIds.has(c.id));
                      navigate(`/studio?${new URLSearchParams({
                        companies: [...new Set(chosen.map((c) => (c.company || '').trim()).filter(Boolean))].join(','),
                        contact_ids: chosen.map((c) => c.id).join(','),
                      })}`);
                    }}
                    className="ui-button ui-button--ghost ui-button--sm"
                  >
                    Write to each of these {selectedContactIds.size} →
                  </button>
                  <button type="button" onClick={() => setSelectedContactIds(new Set())} className="ui-button ui-button--ghost ui-button--sm">
                    Clear
                  </button>
                  <button
                    type="button"
                    onClick={handleBulkDeleteContacts}
                    className="ui-button ui-button--danger ui-button--sm"
                  >
                    Delete contacts
                  </button>
                </div>
              )}
              {/* Full-width Kanban-style pipeline board */}
              <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4 min-h-[420px]">
                {PIPELINE_STATUSES.map((status) => {
                  const inStatus = contacts.filter((c) => (c.pipeline_status || 'cold') === status);
                  const count = pipelineMetrics?.by_status?.find((s) => s.pipeline_status === status)?.count ?? inStatus.length;
                  return (
                    <div
                      key={status}
                      className={`${status === mobileStatus ? 'flex' : 'hidden'} md:flex surface-card rounded-xl flex-col overflow-hidden shadow-sm`}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={handleColumnDrop(status)}
                    >
                      <div className="px-4 py-3 border-b border-pale-sky bg-pale-sky/20 flex justify-between items-center">
                        <h3 className="font-semibold text-deep-navy capitalize">{status}</h3>
                        <span className="text-sm font-medium text-slate-600 bg-white px-2 py-0.5 rounded">
                          {count}
                        </span>
                      </div>
                      <div className="flex-1 overflow-y-auto p-3 space-y-2 min-h-[320px] max-h-[60vh]">
                        {groupByCompany ? (
                          groupContactsByCompany(inStatus).map(({ company, contacts: companyContacts }) => (
                            <CompanyFolder
                              key={company}
                              company={company}
                              contacts={companyContacts}
                              outcome={companyOutcomes[company.trim().toLowerCase()]}
                              selectedContact={selectedContact}
                              selectedIds={selectedContactIds}
                              onSelect={(c) => { setSelectedContact(c); setEmailVerified(null); }}
                              onToggleSelect={toggleContactSelection}
                              onUpdatePipeline={updatePipeline}
                              draggable={!groupByCompany}
                            />
                          ))
                        ) : (
                          inStatus.map((c) => (
                            <ContactCard
                              key={c.id}
                              c={c}
                              selectedContact={selectedContact}
                              selectedIds={selectedContactIds}
                              onSelect={(c) => { setSelectedContact(c); setEmailVerified(null); }}
                              onToggleSelect={toggleContactSelection}
                              onUpdatePipeline={updatePipeline}
                              draggable={true}
                            />
                          ))
                        )}
                        {inStatus.length === 0 && (
                          <div className="text-center text-slate-400 text-sm py-8">No contacts</div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {activeTab === 'worklists' && (
            <div className="surface-card rounded-xl p-4 max-h-[500px] overflow-y-auto space-y-4 w-full">
              <h3 className="font-semibold text-deep-navy">Outreach work lists</h3>
              <p className="text-sm text-slate-600">
                Organize contacts into shared or personal worklists. Prepare messages in Drafts, then track sending from Home.
              </p>
              <div className="flex gap-2 flex-wrap">
                <input
                  value={campaignForm.name}
                  onChange={(e) => setCampaignForm((p) => ({ ...p, name: e.target.value }))}
                  placeholder="Campaign name"
                  className="px-3 py-2 rounded-lg border border-pale-sky text-sm flex-1 min-w-[140px]"
                />
                <select
                  value={campaignForm.type}
                  onChange={(e) => setCampaignForm((p) => ({ ...p, type: e.target.value as 'community' | 'individual' }))}
                  className="px-3 py-2 rounded-lg border border-pale-sky text-sm"
                >
                  <option value="community">Community</option>
                  <option value="individual">Individual</option>
                </select>
                <button
                  onClick={async () => {
                    if (!campaignForm.name.trim()) return;
                    try {
                      await api.outreach.campaigns.create({
                        name: campaignForm.name.trim(),
                        type: campaignForm.type,
                        description: campaignForm.description || undefined,
                      });
                      setCampaignForm({ name: '', type: 'individual', description: '' });
                      api.outreach.campaigns.list().then(setOutreachCampaigns).catch(() => {});
                    } catch (e) {
                      alert((e as Error)?.message);
                    }
                  }}
                  disabled={!campaignForm.name.trim()}
                  className="ui-button ui-button--primary"
                >
                  Create
                </button>
              </div>
              <div className="space-y-2">
                {outreachCampaigns.map((oc) => (
                  <div
                    key={oc.id}
                    onClick={() => setSelectedCampaign(oc)}
                    className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                      selectedCampaign?.id === oc.id ? 'border-[var(--btn-primary-bg)] bg-[var(--btn-primary-bg)]/5' : 'border-pale-sky hover:bg-pale-sky/10'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-medium text-slate-800">{oc.name}</span>
                        <span className={`ml-2 text-xs px-1.5 py-0.5 rounded ${oc.type === 'community' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>
                          {oc.type}
                        </span>
                      </div>
                      <span className="text-xs text-slate-500">{oc.contact_count ?? 0} contacts</span>
                    </div>
                    {(oc.owner_email || oc.owner_name) && (
                      <div className="text-xs text-slate-500 mt-0.5">Author: {oc.owner_email || oc.owner_name}</div>
                    )}
                    {oc.type === 'community' && !oc.owner_email && !oc.owner_name && (
                      <div className="text-xs text-slate-500 mt-0.5">Community initiative</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Detail or editor */}
        <div className={`${activeTab === 'pipeline' ? 'lg:col-span-3' : activeTab === 'worklists' ? 'lg:col-span-1' : 'lg:col-span-2'} space-y-6`}>
          {activeTab === 'worklists' && selectedCampaign && (
            <div className="surface-card rounded-xl p-6">
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="font-semibold text-deep-navy">{selectedCampaign.name}</h3>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${selectedCampaign.type === 'community' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>
                    {selectedCampaign.type}
                  </span>
                  <p className="text-sm text-slate-500 mt-1">
                    Author: {selectedCampaign.type === 'community'
                      ? 'Community'
                      : (selectedCampaign.owner_email || selectedCampaign.owner_name || '—')}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={async () => {
                      if (!confirm('Delete this campaign?')) return;
                      try {
                        await api.outreach.campaigns.delete(selectedCampaign.id);
                        setSelectedCampaign(null);
                        api.outreach.campaigns.list().then(setOutreachCampaigns).catch(() => {});
                      } catch (e) {
                        alert((e as Error)?.message);
                      }
                    }}
                    className="ui-button ui-button--danger ui-button--sm"
                  >
                    Delete
                  </button>
                  <button onClick={() => setSelectedCampaign(null)} className="ui-button ui-button--ghost ui-button--sm">✕ Close</button>
                </div>
              </div>
              <h4 className="text-sm font-medium text-slate-600 mb-2">Contacts In Campaign</h4>
              {campaignContacts.length === 0 ? (
                <p className="text-slate-500 text-sm">No contacts yet. Add from the pipeline or contact list.</p>
              ) : (
                <ul className="space-y-2 max-h-60 overflow-y-auto mb-4">
                  {campaignContacts.map((c) => (
                    <li key={c.id} className="flex items-center justify-between py-2 border-b border-slate-100 text-sm">
                      <span>{c.name || c.email} ({c.company || '—'})</span>
                      <button
                        onClick={async () => {
                          try {
                            await api.outreach.campaigns.removeContact(selectedCampaign.id, c.id);
                            setCampaignContacts((prev) => prev.filter((x) => x.id !== c.id));
                          } catch (e) {
                            alert((e as Error)?.message);
                          }
                        }}
                        className="ui-button ui-button--danger ui-button--sm"
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="pt-4 border-t border-pale-sky">
                <p className="text-xs text-slate-600 mb-2">Add contacts: select from pipeline, then use the &quot;Add to campaign&quot; action (or add bulk from Contacts page).</p>
                <select
                  className="w-full px-3 py-2 rounded-lg border border-pale-sky text-sm"
                  onChange={async (e) => {
                    const cid = parseInt(e.target.value, 10);
                    if (!cid) return;
                    try {
                      await api.outreach.campaigns.addContacts(selectedCampaign.id, [cid]);
                      const added = contacts.find((c) => c.id === cid);
                      if (added) setCampaignContacts((prev) => [...prev, added]);
                      e.target.value = '';
                    } catch (err) {
                      alert((err as Error)?.message);
                    }
                  }}
                >
                  <option value="">Add Contact...</option>
                  {contacts
                    .filter((c) => !campaignContacts.some((cc) => cc.id === c.id))
                    .map((c) => (
                      <option key={c.id} value={c.id}>{c.name || c.email} — {c.company || '—'}</option>
                    ))}
                </select>
              </div>
            </div>
          )}
          {activeTab === 'worklists' && !selectedCampaign && (
            <div className="surface-card rounded-xl p-12 text-center">
              <p className="text-slate-500">Select a campaign to view contacts and add more.</p>
            </div>
          )}
          {activeTab === 'pipeline' && (
            selectedContact ? (
            <>
              <div className="surface-card rounded-xl p-6">
                <div className="flex justify-between items-start">
                  <h3 className="font-semibold text-deep-navy">Contact: {selectedContact.name || selectedContact.email}</h3>
                  <button
                    onClick={() => { setSelectedContact(null); setEmailVerified(null); }}
                    className="ui-button ui-button--ghost"
                  >
                    ✕ Clear Selection
                  </button>
                </div>
                <div className="flex flex-wrap gap-2 items-center mb-4">
                  <span className="text-sm text-slate-600">{selectedContact.email}</span>
                  <select
                    className="px-2 py-1 rounded text-xs border border-pale-sky bg-white"
                    onChange={async (e) => {
                      const cid = parseInt(e.target.value, 10);
                      if (!cid || !selectedContact?.id) return;
                      try {
                        await api.outreach.campaigns.addContacts(cid, [selectedContact.id]);
                        api.outreach.campaigns.list().then(setOutreachCampaigns).catch(() => {});
                        e.target.value = '';
                        alert('Added to campaign');
                      } catch (err) {
                        alert((err as Error)?.message);
                      }
                    }}
                  >
                    <option value="">+ Add To Campaign</option>
                    {outreachCampaigns.map((oc) => (
                      <option key={oc.id} value={oc.id}>{oc.name} ({oc.type})</option>
                    ))}
                  </select>
                  <button
                    onClick={verifyEmail}
                    className="ui-button ui-button--secondary ui-button--sm"
                  >
                    Verify Email
                  </button>
                  {emailVerified !== null && (
                    <span className={`text-xs ${emailVerified ? 'text-green-600' : 'text-red-600'}`}>
                      {emailVerified ? 'Valid' : 'Invalid'}
                    </span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              <div className="surface-card rounded-xl p-6">
                <h3 className="font-semibold text-deep-navy mb-4">Profile Analysis</h3>
                {profile ? (
                  <div className="space-y-3 text-sm">
                    <div>
                      <span className="font-medium text-slate-600">Value proposition:</span>
                      <p className="text-slate-800">{profile.value_proposition}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-600">Role summary:</span>
                      <p className="text-slate-800">{profile.role_summary}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-600">Online sentiment:</span>
                      <p className="text-slate-800">{profile.online_sentiment}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-600">Receptiveness:</span>
                      <p className="text-slate-800 whitespace-pre-wrap">{profile.receptiveness_notes}</p>
                    </div>
                    <button
                      onClick={refreshProfile}
                      disabled={loading}
                      className="ui-button ui-button--secondary ui-button--sm mt-2"
                    >
                      {loading ? 'Refreshing...' : 'Refresh Analysis'}
                    </button>
                  </div>
                ) : (
                  <p className="text-slate-500">Loading profile...</p>
                )}
              </div>
              <div className="space-y-6">
              <div className="surface-card rounded-xl p-6">
                <h3 className="font-semibold text-deep-navy mb-4">Notes</h3>
                <div className="flex gap-2 mb-4">
                  <textarea
                    value={newNote}
                    onChange={(e) => setNewNote(e.target.value)}
                    placeholder="Add A Note..."
                    className="flex-1 px-3 py-2 rounded-lg border border-pale-sky text-sm"
                    rows={2}
                  />
                  <button
                    onClick={addNote}
                    disabled={!newNote.trim()}
                    className="ui-button ui-button--primary"
                  >
                    Add
                  </button>
                </div>
                <ul className="space-y-2 max-h-40 overflow-y-auto">
                  {notes.map((n) => (
                    <li key={n.id} className="text-sm py-2 border-b border-pale-sky last:border-0">
                      {n.note}
                      <span className="text-xs text-slate-400 ml-2">
                        {new Date(n.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="surface-card rounded-xl p-6">
                <h3 className="font-semibold text-deep-navy mb-4">Activity Log</h3>
                <div className="flex gap-2 mb-4 flex-wrap">
                  <select
                    value={newActivityType}
                    onChange={(e) => setNewActivityType(e.target.value)}
                    className="px-3 py-2 rounded-lg border border-pale-sky text-sm"
                  >
                    <option value="email_sent">Email Sent</option>
                    <option value="call">Call</option>
                    <option value="meeting">Meeting</option>
                    <option value="replied">Replied</option>
                    <option value="other">Other</option>
                  </select>
                  <input
                    value={newActivityDetails}
                    onChange={(e) => setNewActivityDetails(e.target.value)}
                    placeholder="Details (optional)"
                    className="flex-1 min-w-[120px] px-3 py-2 rounded-lg border border-pale-sky text-sm"
                  />
                  <button
                    onClick={addActivity}
                    className="ui-button ui-button--primary"
                  >
                    Log
                  </button>
                </div>
                <ul className="space-y-2 max-h-32 overflow-y-auto">
                  {activities.map((a) => (
                    <li key={a.id} className="text-sm py-2 border-b border-pale-sky last:border-0">
                      <span className="font-medium capitalize">{a.activity_type.replace('_', ' ')}</span>
                      {a.details && ` — ${a.details}`}
                      <span className="text-xs text-slate-400 ml-2">
                        {new Date(a.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              </div>
              </div>
            </>
            ) : (
              <div className="surface-card rounded-xl p-12 text-center">
                <p className="text-slate-500">Select a contact from the pipeline board above to view profile, notes, and activity.</p>
              </div>
            )
          )}
        </div>
      </div>

      {activeTab === 'followups' && (
        <div className="space-y-6">
          <FollowUpSchedule />
          <div className="surface-card rounded-xl p-6 space-y-4">
            <div>
              <h3 className="font-semibold text-deep-navy">Sequences (follow-ups)</h3>
              <p className="text-xs text-slate-600 mt-1 leading-relaxed">
                Automated follow-up timing after the initial campaign send. Steps run only for
                contacts still marked as not replied; use{' '}
                <strong className="font-medium text-slate-700">Sync inbox</strong> on Pipeline so
                Gmail replies update the board.
              </p>
            </div>
            {sequences.length > 0 && (
              <div className="max-h-64 overflow-y-auto border border-pale-sky rounded-lg divide-y divide-pale-sky">
                {sequences.map((s) => (
                  <div key={s.id} className="p-3">
                    <div className="font-medium text-slate-800">{s.name}</div>
                    <div className="text-xs text-slate-500">{s.steps?.length || 0} steps</div>
                  </div>
                ))}
              </div>
            )}
            <div className="pt-2 border-t border-pale-sky space-y-3">
              <p className="text-sm font-medium text-deep-navy">New sequence</p>
              <p className="text-xs text-slate-500">Define automated follow-ups (e.g. Day 3, Day 7). Use with campaigns.</p>
              <input
                value={sequenceForm.name}
                onChange={(e) => setSequenceForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="Sequence name"
                className="w-full px-3 py-2 rounded-lg border border-pale-sky"
              />
              {sequenceForm.steps.map((step, i) => (
                <div key={i} className="p-4 border border-pale-sky rounded-lg space-y-2">
                  <div className="flex gap-2">
                    <input
                      type="number"
                      value={step.days_after}
                      onChange={(e) =>
                        setSequenceForm((p) => ({
                          ...p,
                          steps: p.steps.map((s, j) =>
                            j === i ? { ...s, days_after: parseInt(e.target.value) || 0 } : s
                          ),
                        }))
                      }
                      placeholder="Days after"
                      className="w-24 px-3 py-2 rounded-lg border border-pale-sky"
                    />
                    <input
                      value={step.subject}
                      onChange={(e) =>
                        setSequenceForm((p) => ({
                          ...p,
                          steps: p.steps.map((s, j) => (j === i ? { ...s, subject: e.target.value } : s)),
                        }))
                      }
                      placeholder="Subject"
                      className="flex-1 px-3 py-2 rounded-lg border border-pale-sky"
                    />
                  </div>
                  <textarea
                    value={step.body}
                    onChange={(e) =>
                      setSequenceForm((p) => ({
                        ...p,
                        steps: p.steps.map((s, j) => (j === i ? { ...s, body: e.target.value } : s)),
                      }))
                    }
                    placeholder="Body"
                    className="w-full px-3 py-2 rounded-lg border border-pale-sky"
                    rows={3}
                  />
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() =>
                    setSequenceForm((p) => ({
                      ...p,
                      steps: [...p.steps, { days_after: 7, subject: '', body: '' }],
                    }))
                  }
                  className="ui-button ui-button--ghost"
                >
                  + Add step
                </button>
                <button
                  onClick={saveSequence}
                  disabled={loading || !sequenceForm.name}
                  className="ui-button ui-button--primary"
                >
                  {loading ? 'Saving...' : 'Save sequence'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'resources' && (
        <div className="grid grid-cols-1 gap-6">
          <div className="surface-card rounded-xl p-6 space-y-4">
            <div>
              <h3 className="font-semibold text-deep-navy">Templates</h3>
              <p className="text-xs text-slate-600 mt-1 leading-relaxed">
                Reusable copy for first touches and follow-ups. Prefer a few strong templates per
                industry or use case; refine based on reply rate, not volume.
              </p>
            </div>
            {templates.length > 0 && (
              <div className="max-h-64 overflow-y-auto border border-pale-sky rounded-lg divide-y divide-pale-sky">
                {templates.map((t) => (
                  <div key={t.id} className="p-3 flex justify-between items-center">
                    <div>
                      <div className="font-medium text-slate-800">{t.name}</div>
                      <div className="text-xs text-slate-500">{t.industry || 'General'}</div>
                    </div>
                    <button
                      onClick={async () => {
                        if (confirm('Delete this template?')) {
                          try {
                            await api.outreach.templates.delete(t.id);
                            setTemplates((prev) => prev.filter((x) => x.id !== t.id));
                          } catch (e) {
                            alert((e as Error)?.message);
                          }
                        }
                      }}
                      className="ui-button ui-button--danger ui-button--sm"
                    >
                      Delete
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="pt-2 border-t border-pale-sky space-y-3">
              <p className="text-sm font-medium text-deep-navy">New template</p>
              <p className="text-xs text-slate-500">
                Use {'{first}'}, {'{last}'}, {'{company}'}, {'{title}'} for merge fields.
              </p>
              <input
                value={templateForm.name}
                onChange={(e) => setTemplateForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="Template name"
                className="w-full px-3 py-2 rounded-lg border border-pale-sky"
              />
              <input
                value={templateForm.industry}
                onChange={(e) => setTemplateForm((p) => ({ ...p, industry: e.target.value }))}
                placeholder="Industry (optional)"
                className="w-full px-3 py-2 rounded-lg border border-pale-sky"
              />
              <input
                value={templateForm.subject}
                onChange={(e) => setTemplateForm((p) => ({ ...p, subject: e.target.value }))}
                placeholder="Subject"
                className="w-full px-3 py-2 rounded-lg border border-pale-sky"
              />
              <textarea
                value={templateForm.body}
                onChange={(e) => setTemplateForm((p) => ({ ...p, body: e.target.value }))}
                placeholder="Body"
                className="w-full px-3 py-2 rounded-lg border border-pale-sky"
                rows={5}
              />
              <button
                onClick={saveTemplate}
                disabled={loading || !templateForm.name || !templateForm.subject || !templateForm.body}
                className="ui-button ui-button--primary"
              >
                {loading ? 'Saving...' : 'Save template'}
              </button>
            </div>
          </div>

        </div>
      )}
    </div>
  );
}
