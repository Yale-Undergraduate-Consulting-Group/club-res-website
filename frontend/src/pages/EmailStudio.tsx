import { sanitizeRichText, insertSafeTransfer } from '../lib/richText';
import type { Sentiment, Attachment, Release, OneDriveItem, Sequence } from '../api';
import { useCallback, useEffect, useState, useRef, useMemo } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import { api, type Contact, type GeneratedEmail } from '../api';
import AppSubnav from '../components/AppSubnav';
import PageHeader from '../components/PageHeader';
import AiModelSelect from '../components/AiModelSelect';
import EmailToolbar from '../components/EmailToolbar';
import AddressCheck from '../components/AddressCheck';
import AdvisoryBatch from '../components/AdvisoryBatch';
import { useAiModel } from '../contexts/useAiModel';
import { useUrlTab } from '../lib/useUrlTab';


/** One screen of companies. The catalogue can hold tens of thousands, so the
 *  picker pages the server instead of holding them all in the browser. */
const COMPANY_PAGE = 200;

/** The editor's default text size; the toolbar sets any other size per
 *  selection, exactly as Gmail does. */
const BASE_FONT_SIZE = 14;

function lastSendHint(c: { last_sent_at?: string | null; last_campaign_name?: string | null }) {
  if (!c.last_sent_at) return '';
  const when = String(c.last_sent_at).slice(0, 10);
  return c.last_campaign_name ? `Last send: ${c.last_campaign_name} · ${when}` : `Last send ${when}`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char] || char);
}

function renderCampaignTemplate(value: string, contact: Contact, htmlBody = false): string {
  const rawFields: Record<string, string> = {
    name: contact.name || '',
    first_name: (contact.name || '').trim().split(/\s+/)[0] || '',
    company: contact.company || '',
    title: contact.title || '',
    email: contact.email || '',
  };
  const fields = htmlBody
    ? Object.fromEntries(Object.entries(rawFields).map(([key, field]) => [key, escapeHtml(field)]))
    : rawFields;
  return value.replace(/{{\s*(name|first_name|company|title|email)\s*}}/gi, (_, key: string) => fields[key.toLowerCase()] || '');
}

async function loadCompanyEmployees(companies: string[]): Promise<Contact[]> {
  const items: Contact[] = [];
  let offset = 0;
  while (true) {
    const page = await api.contacts.list({
      companies: companies.join(','),
      employee_only: true,
      limit: 500,
      offset,
    });
    items.push(...page.items);
    offset += page.limit;
    if (offset >= page.total) return items;
  }
}

async function loadAllReleaseContacts(releaseId: number, q?: string, signal?: AbortSignal): Promise<Contact[]> {
  const items: Contact[] = [];
  let offset = 0;
  while (true) {
    const page = await api.contacts.list({ release_id: releaseId, q, limit: 500, offset }, signal);
    items.push(...page.items);
    offset += page.limit;
    if (offset >= page.total) return items;
  }
}

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

function CompanyFolder({ company, contacts, selected, onSelect, bulkSelectedIds, onToggleBulk, onToggleAllInCompany }: {
  company: string;
  contacts: Contact[];
  selected: Contact | null;
  onSelect: (c: Contact) => void;
  bulkSelectedIds: Set<number>;
  onToggleBulk: (id: number) => void;
  onToggleAllInCompany: (companyContacts: Contact[]) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const numericIds = useMemo(
    () => contacts.map((c) => c.id).filter((id): id is number => typeof id === 'number'),
    [contacts]
  );
  const selectedInCompany = useMemo(
    () => numericIds.filter((id) => bulkSelectedIds.has(id)).length,
    [numericIds, bulkSelectedIds]
  );
  const allSelected = numericIds.length > 0 && selectedInCompany === numericIds.length;
  const someSelected = selectedInCompany > 0 && !allSelected;

  useEffect(() => {
    const el = selectAllRef.current;
    if (el) el.indeterminate = someSelected;
  }, [someSelected]);

  return (
    <div className="rounded-lg border border-pale-sky dark:border-slate-600 overflow-hidden">
      <div className="flex items-stretch gap-0 bg-white border-b border-[var(--border)] dark:bg-slate-700/40">
        {numericIds.length > 0 ? (
          <label
            className="flex items-center pl-2 pr-1 shrink-0 cursor-pointer self-center"
            title={`Select all contacts in ${company}`}
            onClick={(e) => e.stopPropagation()}
          >
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allSelected}
              onChange={() => onToggleAllInCompany(contacts)}
              aria-label={`Select all contacts in ${company}`}
              className="rounded border-slate-400 dark:border-slate-500 text-[var(--accent)] focus:ring-[var(--accent)]"
            />
          </label>
        ) : null}
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="flex-1 min-w-0 px-3 py-2 flex items-center gap-2 hover:bg-pale-sky/15 dark:hover:bg-slate-600/50 text-left text-sm font-medium text-deep-navy dark:text-[var(--text-primary)]"
        >
          <span className="truncate min-w-0">{company}</span>
          <span className="text-[var(--text-muted)] text-xs shrink-0">({contacts.length})</span>
          <span className="ui-disclosure-chevron text-[var(--text-muted)] text-xs shrink-0 ml-auto" data-open={expanded} aria-hidden>▼</span>
        </button>
      </div>
      <div className="ui-disclosure" data-open={expanded}>
        <div className="overflow-hidden min-h-0">
          <div className="divide-y divide-slate-100 dark:divide-slate-600">
          {contacts.map((c) => (
            <div
              key={c.id}
              className={`flex items-stretch gap-1 px-1 py-0.5 hover:bg-pale-sky/[0.08] dark:hover:bg-slate-700/40 ${
                selected?.id === c.id ? 'bg-white dark:bg-slate-600/50 border-l-4 border-l-[var(--accent)] shadow-sm' : ''
              }`}
            >
              <input
                type="checkbox"
                checked={bulkSelectedIds.has(c.id)}
                onChange={() => onToggleBulk(c.id)}
                aria-label={`Select ${c.name || c.email}`}
                className="mt-2.5 ml-1 rounded border-slate-400 dark:border-slate-500 text-[var(--accent)] focus:ring-[var(--accent)] shrink-0"
              />
              <button
                type="button"
                onClick={() => onSelect(c)}
                className="flex-1 min-w-0 text-left py-2 pr-2"
              >
                <div className="font-medium text-deep-navy dark:text-[var(--text-primary)] text-xs truncate">{c.name || c.email}</div>
                <div className="text-xs text-[var(--text-muted)] truncate">{c.title}{c.company ? ` • ${c.company}` : ''}</div>
                {lastSendHint(c) && <div className="text-[11px] text-[var(--text-muted)] truncate">{lastSendHint(c)}</div>}
              </button>
            </div>
          ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function EmailStudio() {
  const navigate = useNavigate();
  const { user } = useOutletContext<{ user: { email: string; name?: string; role?: string } }>();
  const { modelId } = useAiModel();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [email, setEmail] = useState<{ subject: string; body: string } | null>(null);
  const [signOff, setSignOff] = useState({
    name: '', pronouns: '', role: '', organization: 'Yale Undergraduate Consulting Group',
    linkedin: '', phone: '', logoUrl: '',
  });
  const [loading, setLoading] = useState(false);
  const [generationError, setGenerationError] = useState('');
  const [testSending, setTestSending] = useState(false);
  const [tone, setTone] = useState('professional');
  const [length, setLength] = useState('short');
  const [angle, setAngle] = useState('pain_point');
  const [valueProp, setValueProp] = useState('');
  const [customInstructions, setCustomInstructions] = useState('');
  const [citeSuggestLoading, setCiteSuggestLoading] = useState(false);
  const [citeSuggestMessage, setCiteSuggestMessage] = useState('');
  const [generatedEmails, setGeneratedEmails] = useState<GeneratedEmail[]>([]);
  const [sortBy, setSortBy] = useState('created_desc');
  const [activeTab, setActiveTab] = useUrlTab<'editor' | 'cache'>(['editor', 'cache'], 'editor', 'panel');
  const [quickCompose, setQuickCompose] = useState({ name: '', company: '', title: '', email: '' });
  // The toolbar sets size per selection; this is only the editor's default,
  // matched by the preview so both read at the same scale.
  const [selectedDraftId, setSelectedDraftId] = useState<number | null>(null);
  const [draftSaving, setDraftSaving] = useState(false);
  const [draftMessage, setDraftMessage] = useState('');
  const [mobileStep, setMobileStep] = useState<'contacts' | 'generate' | 'edit'>(() => activeTab === 'cache' ? 'contacts' : 'edit');
  const [focusWriting, setFocusWriting] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [draftDescription, setDraftDescription] = useState('');
  const [draftTargetAudience, setDraftTargetAudience] = useState('');
  const [draftCompany, setDraftCompany] = useState('');
  const [sentimentAnalysis, setSentimentAnalysis] = useState<Sentiment | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(false);
  const [sentimentIndustry, setSentimentIndustry] = useState('');
  const [attachmentLibrary, setAttachmentLibrary] = useState<Attachment[]>([]);
  const [selectedAttachmentIds, setSelectedAttachmentIds] = useState<Set<number>>(new Set());
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [groupByCompany, setGroupByCompany] = useState(false);
  const [contactsPanelExpanded, setContactsPanelExpanded] = useState(true);
  const [aiGeneratorExpanded, setAiGeneratorExpanded] = useState(true);
  const [contactSearch, setContactSearch] = useState(() => new URLSearchParams(window.location.search).get('q') || '');
  const [releaseFilter, setReleaseFilter] = useState(() => new URLSearchParams(window.location.search).get('release_id') || '');
  const [releases, setReleases] = useState<Release[]>([]);
  const [onedriveOpen, setOnedriveOpen] = useState(false);
  const [onedriveConfigured, setOnedriveConfigured] = useState(false);
  const [onedriveItems, setOnedriveItems] = useState<OneDriveItem[]>([]);
  const [onedriveBusy, setOnedriveBusy] = useState(false);
  const [companiesSummary, setCompaniesSummary] = useState<{ company: string; company_domain?: string; contact_count: number }[]>([]);
  const [selectedCompanyNames, setSelectedCompanyNames] = useState<Set<string>>(new Set());
  const [studioCampaignContacts, setStudioCampaignContacts] = useState<Contact[]>([]);
  const [selectedCampaignContactIds, setSelectedCampaignContactIds] = useState<Set<number>>(new Set());
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [companySearch, setCompanySearch] = useState('');
  const [campaignSequenceId, setCampaignSequenceId] = useState('');
  const [campaignName, setCampaignName] = useState('');
  const [campaignBusy, setCampaignBusy] = useState(false);
  const [campaignMessage, setCampaignMessage] = useState<string | null>(null);
  const [createdCampaignId, setCreatedCampaignId] = useState<number | null>(null);
  const [campaignPanelOpen, setCampaignPanelOpen] = useState(false);
  const [studioContactsListOpen, setStudioContactsListOpen] = useState(true);
  const [studioCompanyListOpen, setStudioCompanyListOpen] = useState(false);
  const [studioListDeleting, setStudioListDeleting] = useState(false);
  const [sidebarBulkIds, setSidebarBulkIds] = useState<Set<number>>(new Set());
  const [sidebarDeleting, setSidebarDeleting] = useState(false);
  const [generatedClearBusy, setGeneratedClearBusy] = useState(false);

  useEffect(() => {
    api.settings.get().then((s) => {
      setSignOff({
        name: s.sign_off_name || user?.name || '',
        pronouns: s.sign_off_pronouns || '',
        role: s.sign_off_role || '',
        organization: s.sign_off_organization || 'Yale Undergraduate Consulting Group',
        linkedin: s.sign_off_linkedin || '',
        phone: s.sign_off_phone || '',
        logoUrl: s.sign_off_logo_url || '',
      });
    }).catch(() => {});
  }, [user?.name]);

  const contactListParams = useCallback(() => ({
    ...(contactSearch.trim() ? { q: contactSearch.trim() } : {}),
    ...(releaseFilter ? { release_id: Number(releaseFilter) } : {}),
    limit: 100,
  }), [contactSearch, releaseFilter]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const params = contactListParams();
      const request = params.release_id
        ? loadAllReleaseContacts(params.release_id, params.q, controller.signal)
        : api.contacts.list(params, controller.signal).then((page) => page.items);
      request
        .then(setContacts)
        .catch((error) => {
          if (!(error instanceof DOMException && error.name === 'AbortError')) setContacts([]);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [contactListParams]);

  useEffect(() => {
    api.yucg.listReleases().then(setReleases).catch(() => setReleases([]));
  }, []);
  // Deep link from research acceptance: preselect the exact accepted contact.
  const deepLinkContactId = Number(new URLSearchParams(window.location.search).get('contact_id')) || null;
  const [deepLinkApplied, setDeepLinkApplied] = useState(false);
  const deepLinkTarget = !deepLinkApplied && deepLinkContactId != null
    ? contacts.find((contact) => contact.id === deepLinkContactId)
    : undefined;
  if (deepLinkTarget) {
    setDeepLinkApplied(true);
    setSelected(deepLinkTarget);
    setEmail(null);
    setSelectedDraftId(null);
    setMobileStep('edit');
  }

  // A hand-off from Find people: the companies and the exact people chosen
  // there. Read once; closing the panel drops it from the address too.
  const [handoff, setHandoff] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const companies = (params.get('companies') || '').split(',').map((c) => c.trim()).filter(Boolean);
    const contactIds = (params.get('contact_ids') || '').split(',').map(Number).filter((n) => Number.isInteger(n) && n > 0);
    return companies.length && contactIds.length ? { companies, contactIds } : null;
  });
  const openHandoffContact = async (contact: Contact) => {
    const drafts = await api.emails.generated({ contact_id: contact.id }).catch(() => [] as GeneratedEmail[]);
    const latest = drafts.find((d) => !d.campaign_id) ?? drafts[0];
    if (latest) {
      loadDraftIntoEditor(latest);
    } else {
      setSelected(contact);
      setEmail(null);
      setSelectedDraftId(null);
      setMobileStep('edit');
    }
    document.getElementById('email-editor-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const sidebarSelectedIds = useMemo(() => {
    const allowed = new Set(contacts.map((c) => c.id));
    return new Set([...sidebarBulkIds].filter((id) => allowed.has(id)));
  }, [contacts, sidebarBulkIds]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.contacts.companiesSummary({ q: companySearch, limit: COMPANY_PAGE }, controller.signal)
        .then(setCompaniesSummary)
        .catch((error) => {
          if (!(error instanceof DOMException && error.name === 'AbortError')) setCompaniesSummary([]);
        });
    }, companySearch ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [companySearch]);

  useEffect(() => {
    api.outreach.sequences.list().then(setSequences).catch(() => setSequences([]));
  }, []);

  useEffect(() => {
    api.attachments.list().then(setAttachmentLibrary).catch(() => setAttachmentLibrary([]));
  }, []);

  useEffect(() => {
    api.emails.generated({ sort: sortBy }).then(setGeneratedEmails).catch(() => setGeneratedEmails([]));
  }, [sortBy]);

  // Sync contentEditable body when email.body is set externally (e.g. Generate, Load Draft)
  useEffect(() => {
    if (bodyRef.current != null && email?.body !== undefined && bodyRef.current.innerHTML !== email.body) {
      bodyRef.current.innerHTML = sanitizeRichText(email.body);
    }
  }, [email?.body]);


  const generateEmail = async () => {
    setLoading(true);
    setGenerationError('');
    try {
      if (selected?.id) {
        const instructions = [
          draftDescription && `Email purpose: ${draftDescription}`,
          draftTargetAudience && `Target audience: ${draftTargetAudience}`,
          customInstructions,
        ].filter(Boolean).join('. ');
        const res = await api.emails.generate({
          contact_id: selected.id,
          tone,
          length,
          angle,
          value_proposition: valueProp || undefined,
          custom_instructions: instructions || customInstructions || undefined,
          model: modelId,
        });
        setEmail({ subject: res.subject, body: res.body });
        setMobileStep('edit');
        const savedDrafts = await api.emails.generated({ sort: sortBy });
        setGeneratedEmails(savedDrafts);
        setSelectedDraftId(savedDrafts.find((draft) => draft.contact_id === selected.id && draft.subject === res.subject)?.id ?? null);
      } else {
        const instructions = [
          draftDescription && `Email purpose: ${draftDescription}`,
          draftTargetAudience && `Target audience: ${draftTargetAudience}`,
          customInstructions,
        ].filter(Boolean).join('. ');
        const res = await api.emails.generateTemplate({
          name: quickCompose.name || undefined,
          company: draftCompany || quickCompose.company || undefined,
          title: quickCompose.title || undefined,
          email: quickCompose.email || undefined,
          tone,
          length,
          angle,
          value_proposition: valueProp || undefined,
          custom_instructions: instructions || customInstructions || undefined,
          model: modelId,
        });
        setEmail({ subject: res.subject, body: res.body });
        setMobileStep('edit');
        setSelected({
          id: res.contact_id ?? 0,
          name: quickCompose.name || 'Recipient',
          email: quickCompose.email || '',
          company: draftCompany || quickCompose.company,
        });
        const savedDrafts = await api.emails.generated({ sort: sortBy });
        setGeneratedEmails(savedDrafts);
        setSelectedDraftId(savedDrafts.find((draft) => draft.contact_id === res.contact_id && draft.subject === res.subject)?.id ?? null);
      }
    } catch (e) {
      console.error(e);
      setGenerationError(e instanceof Error ? e.message : 'Draft generation is unavailable. Your current draft was not changed.');
    } finally {
      setLoading(false);
    }
  };

  const handleSuggestCitations = async () => {
    const company = selected?.company;
    if (!company) return;
    setCiteSuggestLoading(true);
    setCiteSuggestMessage('');
    try {
      const res = await api.projects.suggestCitations(company);
      const clientNames = res.projects.map((p) => p.client_name).filter((name): name is string => Boolean(name));
      const teamLines = res.team_experience
        .filter((t) => t.user_name)
        .map((t) => `${t.user_name}${t.role_in_project ? ` (${t.role_in_project}` : ''}${t.client_name ? `${t.role_in_project ? ', ' : ' ('}${t.client_name} project` : ''}${t.role_in_project || t.client_name ? ')' : ''}`);
      const parts = [
        clientNames.length > 0 && `Past clients we can discuss: ${clientNames.join(', ')}.`,
        teamLines.length > 0 && `Team members with relevant experience: ${teamLines.join(', ')}.`,
      ].filter(Boolean);
      if (parts.length === 0) {
        setCiteSuggestMessage('No nameable past projects on file yet.');
        return;
      }
      const suggestion = parts.join(' ');
      setValueProp((prev) => (prev.trim() ? `${prev}\n\n${suggestion}` : suggestion));
    } catch (e) {
      setCiteSuggestMessage(e instanceof Error ? e.message : 'Could not load citation suggestions.');
    } finally {
      setCiteSuggestLoading(false);
    }
  };

  const saveCurrentAsDraft = async () => {
    if (!email?.subject && !email?.body) return;
    if (!selected?.id) {
      setDraftMessage('Choose a contact, or add a recipient email and generate once, before saving.');
      return;
    }
    setDraftSaving(true);
    setDraftMessage('');
    try {
      if (selectedDraftId) {
        await api.emails.updateDraft(selectedDraftId, email);
      } else {
        const saved = await api.emails.saveDraft({ contact_id: selected.id, ...email });
        setSelectedDraftId(saved.id);
      }
      setGeneratedEmails(await api.emails.generated({ sort: sortBy }));
      setDraftMessage('Draft saved to your account.');
    } catch (requestError) {
      setDraftMessage((requestError as Error).message);
    } finally {
      setDraftSaving(false);
    }
  };

  const loadDraftIntoEditor = (draft: GeneratedEmail) => {
    setMobileStep('edit');
    setSelectedDraftId(draft.id);
    setEmail({ subject: draft.subject, body: draft.body });
    setDraftCompany(draft.company || '');
    setQuickCompose({
      name: draft.name || '',
      company: draft.company || '',
      title: '',
      email: draft.email || '',
    });
    setSelected({ id: draft.contact_id, name: draft.name, email: draft.email || '', company: draft.company });
    setActiveTab('editor');
  };

  const analyzeSentiment = async () => {
    if (!email?.subject && !email?.body) return;
    setSentimentLoading(true);
    setSentimentAnalysis(null);
    try {
      const res = await api.outreach.sentiment.analyze({
        subject: email?.subject || '',
        body: email?.body || '',
        industry: sentimentIndustry || undefined,
        target_role: selected?.title || undefined,
      });
      setSentimentAnalysis(res);
    } catch (e) {
      setSentimentAnalysis({ error: (e as Error)?.message || 'Analysis failed' });
    } finally {
      setSentimentLoading(false);
    }
  };

  const testSend = async () => {
    if (!email?.body) return;
    const toEmail = user?.email;
    if (!toEmail) return;
    setTestSending(true);
    try {
      await api.emails.testSend({
        to_email: toEmail,
        subject: email.subject,
        body: email.body,
        attachment_ids: selectedAttachmentIds.size > 0 ? Array.from(selectedAttachmentIds) : undefined,
      });
      alert(`Test email sent to ${toEmail}. Check your inbox to verify delivery.`);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      alert(eMessage || 'Failed to send. Try signing out and back in to re-authorize Gmail.');
    } finally {
      setTestSending(false);
    }
  };

  const toggleCompanyPick = (name: string) => {
    setSelectedCompanyNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const loadStudioContactsForCompanies = async () => {
    const names = [...selectedCompanyNames];
    setCampaignMessage(null);
    if (!names.length) {
      setStudioCampaignContacts([]);
      setSelectedCampaignContactIds(new Set());
      setCampaignMessage('Select at least one company.');
      return;
    }
    try {
      const items = await loadCompanyEmployees(names);
      setStudioCampaignContacts(items);
      setSelectedCampaignContactIds(new Set(items.map((contact) => contact.id)));
    } catch (requestError) {
      setStudioCampaignContacts([]);
      setCampaignMessage((requestError as Error).message || 'Failed to load contacts');
    }
  };

  const refreshStudioCampaignContacts = async () => {
    const names = [...selectedCompanyNames];
    if (!names.length) {
      setStudioCampaignContacts([]);
      setSelectedCampaignContactIds(new Set());
      return;
    }
    try {
      const items = await loadCompanyEmployees(names);
      setStudioCampaignContacts(items);
      setSelectedCampaignContactIds((prev) => {
        const allowed = new Set(items.map((contact) => contact.id));
        const next = new Set<number>();
        prev.forEach((id) => {
          if (allowed.has(id)) next.add(id);
        });
        return next;
      });
    } catch {
      /* keep existing loaded list */
    }
  };

  const deleteSelectedStudioContactsFromDb = async () => {
    const ids = [...selectedCampaignContactIds].filter((id) => typeof id === 'number');
    if (!ids.length) {
      setCampaignMessage('Select at least one loaded contact to remove from the database.');
      return;
    }
    if (
      !window.confirm(
        `Permanently delete ${ids.length} contact(s) from the database? Related campaign rows and notes are removed too. This cannot be undone.`
      )
    ) {
      return;
    }
    setStudioListDeleting(true);
    setCampaignMessage(null);
    try {
      const res = await api.contacts.bulkDelete(ids);
      const page = await api.contacts.list(contactListParams());
      setContacts(page.items);
      api.contacts.companiesSummary({ q: companySearch, limit: COMPANY_PAGE }).then(setCompaniesSummary).catch(() => setCompaniesSummary([]));
      await refreshStudioCampaignContacts();
      if (res.skipped > 0) {
        window.alert(
          `Deleted ${res.deleted}. ${res.skipped} could not be removed (permission or not found).`
        );
      } else {
        setCampaignMessage(`Deleted ${res.deleted} contact(s) from the database.`);
      }
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setCampaignMessage(eMessage || 'Delete failed');
    } finally {
      setStudioListDeleting(false);
    }
  };

  const buildCampaignFromStudio = async () => {
    if (!email?.subject?.trim() || !email?.body?.trim()) {
      setCampaignMessage('Compose subject and body in the editor first.');
      return;
    }
    const ids = [...selectedCampaignContactIds];
    if (!ids.length) {
      setCampaignMessage('Load contacts and keep at least one selected.');
      return;
    }
    const name = campaignName.trim() || `Studio ${new Date().toLocaleDateString()}`;
    setCampaignBusy(true);
    setCampaignMessage(null);
    try {
      const subjects: Record<string, string> = {};
      const bodies: Record<string, string> = {};
      const isTemplate = /{{\s*(name|first_name|company|title|email)\s*}}/i.test(`${email.subject}\n${email.body}`);
      for (const id of ids) {
        const contact = studioCampaignContacts.find(item => item.id === id);
        if (contact && isTemplate) {
          subjects[String(id)] = renderCampaignTemplate(email.subject, contact);
          bodies[String(id)] = renderCampaignTemplate(email.body, contact, true);
        } else if (id === selected?.id) {
          subjects[String(id)] = email.subject;
          bodies[String(id)] = email.body;
        }
      }
      const camp = await api.campaigns.create(name);
      await api.campaigns.addContacts(camp.id, {
        contact_ids: ids,
        email_subjects: subjects,
        email_bodies: bodies,
      });
      if (campaignSequenceId || selectedAttachmentIds.size > 0) {
        await api.campaigns.update(camp.id, {
          ...(campaignSequenceId ? { sequence_id: Number(campaignSequenceId) } : {}),
          ...(selectedAttachmentIds.size > 0 ? { attachment_ids: [...selectedAttachmentIds] } : {}),
        });
      }
      setCreatedCampaignId(camp.id);
      const personalized = Object.keys(subjects).length;
      const draftNote = personalized < ids.length
        ? ` ${ids.length - personalized} recipient(s) use their own latest saved draft and must be completed before release.`
        : '';
      setCampaignMessage(`Draft campaign #${camp.id} saved.${draftNote}`);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setCampaignMessage(eMessage || 'Campaign failed');
    } finally {
      setCampaignBusy(false);
    }
  };

  const startNewEmail = () => {
    setSelected(null);
    setEmail({ subject: '', body: '' });
    setMobileStep('contacts');
    setQuickCompose({ name: '', company: '', title: '', email: '' });
    setDraftDescription('');
    setDraftTargetAudience('');
    setDraftCompany('');
    setSelectedDraftId(null);
    setSelectedAttachmentIds(new Set());
    setActiveTab('editor');
    document.getElementById('email-generator-section')?.scrollIntoView({ behavior: 'smooth' });
  };

  const toggleAttachment = (id: number) => {
    setSelectedAttachmentIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const addAttachmentFiles = async (files: File[]) => {
    if (files.length === 0) return;
    setAttachmentUploading(true);
    try {
      const uploaded = await Promise.all(files.map((file) => api.attachments.upload(file, file.name)));
      setAttachmentLibrary((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        uploaded.forEach((item) => byId.set(item.id, item));
        return [...byId.values()];
      });
      setSelectedAttachmentIds((current) => {
        const next = new Set(current);
        uploaded.forEach((item) => next.add(item.id));
        return next;
      });
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Attachment upload failed');
    } finally {
      setAttachmentUploading(false);
    }
  };

  const toggleSidebarBulk = (id: number) => {
    setSidebarBulkIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSidebarBulkForCompany = (companyContacts: Contact[]) => {
    const ids = companyContacts.map((c) => c.id).filter((id): id is number => typeof id === 'number');
    if (!ids.length) return;
    setSidebarBulkIds((prev) => {
      const allOn = ids.every((id) => prev.has(id));
      const next = new Set(prev);
      if (allOn) {
        ids.forEach((id) => next.delete(id));
      } else {
        ids.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const selectAllSidebarContacts = () => {
    setSidebarBulkIds(new Set(contacts.map((c) => c.id)));
  };

  const bulkDeleteSidebarContacts = async () => {
    const ids = [...sidebarSelectedIds];
    if (!ids.length) return;
    if (!window.confirm(`Are you sure? Delete ${ids.length} contact(s) from the database? This cannot be undone.`)) return;
    setSidebarDeleting(true);
    try {
      const res = await api.contacts.bulkDelete(ids);
      setSidebarBulkIds(new Set());
      if (selected?.id && ids.includes(selected.id)) {
        setSelected(null);
        setEmail(null);
      }
      setCompaniesSummary([]);
      const page = await api.contacts.list(contactListParams());
      setContacts(page.items);
      api.contacts.companiesSummary({ q: companySearch, limit: COMPANY_PAGE }).then(setCompaniesSummary).catch(() => setCompaniesSummary([]));
      if (res.skipped > 0) {
        window.alert(`Deleted ${res.deleted}. ${res.skipped} could not be removed (permission or not found).`);
      }
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      window.alert(eMessage || 'Bulk delete failed');
    } finally {
      setSidebarDeleting(false);
    }
  };

  const clearGeneratedEmailCache = async () => {
    if (!window.confirm('Clear all generated email history from the studio list? Contacts are not deleted.')) return;
    setGeneratedClearBusy(true);
    try {
      const res = await api.emails.clearGeneratedCache();
      api.emails.generated({ sort: sortBy }).then(setGeneratedEmails).catch(() => setGeneratedEmails([]));
      if (res.deleted === 0) {
        window.alert('No cached generated emails to clear.');
      }
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      window.alert(eMessage || 'Failed to clear cache');
    } finally {
      setGeneratedClearBusy(false);
    }
  };

  const previewBody = email?.body || '';
  const selectedAttachments = attachmentLibrary.filter((item) => selectedAttachmentIds.has(item.id));
  const bodyRef = useRef<HTMLDivElement>(null);

  return (
    <div className={`email-studio app-workspace w-full max-w-[1920px] ${focusWriting ? 'studio-focus' : ''} ${previewOpen ? 'studio-preview-open' : ''}`}>
      <PageHeader title="Drafts" />
      <div className="studio-workbench-bar">
        <span>Writing as <strong>{user?.email}</strong></span>
        <div>
          <button type="button" aria-pressed={focusWriting} onClick={() => setFocusWriting(value => !value)}>Focus on writing</button>
          <button type="button" aria-pressed={previewOpen} onClick={() => { setPreviewOpen(value => !value); setMobileStep('edit'); }}>Preview email</button>
        </div>
      </div>
      <nav className="studio-mobile-steps" aria-label="Draft workflow">
        {([
          ['contacts', '1. Contact'],
          ['generate', '2. AI assistance'],
          ['edit', '3. Write'],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            aria-current={mobileStep === id ? 'step' : undefined}
            onClick={() => setMobileStep(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      {handoff && (
        <AdvisoryBatch
          companies={handoff.companies}
          contactIds={handoff.contactIds}
          tone={tone}
          model={modelId}
          valueProp={valueProp}
          goal={draftDescription}
          onOpen={(contact) => void openHandoffContact(contact)}
          onDrafted={() => api.emails.generated({ sort: sortBy }).then(setGeneratedEmails).catch(() => {})}
          onDismiss={() => {
            setHandoff(null);
            navigate('/studio', { replace: true });
          }}
        />
      )}
      <div className="email-studio-layout">
        <div
          data-collapsed={!contactsPanelExpanded}
          className={`studio-step studio-panel email-studio-contacts w-full ${mobileStep === 'contacts' ? 'is-active' : ''} surface-card shadow-sm rounded-xl flex-shrink-0`}
        >
          {contactsPanelExpanded ? (
            <>
              {/* Two rows of one control height. Collapse, New and the tab
                  pair used to share a wrapping row, so at sidebar width the
                  pills stacked and stretched the buttons beside them. */}
              <div className="px-4 py-3 border-b border-[var(--border)] space-y-2 bg-white dark:bg-[var(--bg-card)]">
                <div className="flex gap-2 items-stretch">
                  <button
                    onClick={() => setContactsPanelExpanded(false)}
                    className="app-nav-util-btn w-9 shrink-0 grid place-items-center p-0"
                    title="Collapse panel"
                    aria-label="Collapse contacts panel"
                  >
                    ◀
                  </button>
                  <AppSubnav
                    className="app-subnav--stretch min-w-0"
                    items={[
                      { id: 'editor', label: 'Contacts' },
                      { id: 'cache', label: 'Drafts' },
                    ]}
                    active={activeTab}
                    onChange={(id) => setActiveTab(id as 'editor' | 'cache')}
                    label="Draft contact sources"
                  />
                </div>
                <button
                  onClick={startNewEmail}
                  className="ui-button ui-button--primary ui-button--sm w-full"
                >
                  + New draft
                </button>
              </div>
              <div className="min-w-0 email-studio-contacts-body">
            {activeTab === 'editor' ? (
              <>
                <div className="px-4 py-2 border-b border-slate-200 space-y-2">
                  <select aria-label="Target list"
                    value={releaseFilter}
                    onChange={(e) => setReleaseFilter(e.target.value)}
                    className="w-full px-3 py-2 rounded border border-slate-200 dark:border-slate-600 text-sm bg-white dark:bg-slate-700 text-deep-navy dark:text-slate-200"
                  >
                    <option value="">All contacts</option>
                    {releases.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} (#{r.id})
                      </option>
                    ))}
                  </select>
                  <input
                    type="search"
                    placeholder="Search contacts... (press /)"
                    value={contactSearch}
                    onChange={(e) => setContactSearch(e.target.value)}
                    className="w-full px-3 py-2 rounded border border-slate-200 dark:border-slate-600 text-sm bg-white dark:bg-slate-700 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 caret-deep-navy dark:caret-slate-200"
                    aria-label="Search contacts"
                    data-search-input
                  />
                </div>
                {contacts.length > 0 && (
                  // Text actions on one line, the one real action below it:
                  // four stock buttons wrapped into a ragged stack in this
                  // narrow column, each indented by its own padding.
                  <div className="px-4 py-2 border-b border-[var(--border)] space-y-2 bg-white dark:bg-[var(--bg-card)]">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="font-semibold text-deep-navy dark:text-[var(--text-primary)]">
                        {sidebarSelectedIds.size} selected
                      </span>
                      <button type="button" onClick={selectAllSidebarContacts} className="underline text-deep-navy dark:text-[var(--text-primary)]">
                        Select all
                      </button>
                      <button type="button" onClick={() => setSidebarBulkIds(new Set())} className="underline text-[var(--text-muted)]">
                        Clear
                      </button>
                      <button
                        type="button"
                        disabled={sidebarSelectedIds.size === 0 || sidebarDeleting}
                        onClick={bulkDeleteSidebarContacts}
                        className="ml-auto underline text-red-700 disabled:text-[var(--text-muted)] disabled:no-underline"
                      >
                        {sidebarDeleting ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                    {/* Ticking several people opens the per-person panel
                        above: an advisory draft each, then one campaign. */}
                    {sidebarSelectedIds.size > 1 && (
                      <button
                        type="button"
                        onClick={() => {
                          const chosen = contacts.filter((c) => sidebarSelectedIds.has(c.id));
                          const next = {
                            companies: [...new Set(chosen.map((c) => (c.company || '').trim()).filter(Boolean))],
                            contactIds: chosen.map((c) => c.id),
                          };
                          setHandoff(next);
                          navigate(`/studio?${new URLSearchParams({
                            companies: next.companies.join(','), contact_ids: next.contactIds.join(','),
                          })}`, { replace: true });
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                        }}
                        className="ui-button ui-button--primary ui-button--sm w-full"
                      >
                        Write to each of these {sidebarSelectedIds.size} →
                      </button>
                    )}
                  </div>
                )}
              {contacts.length === 0 ? (
                <div className="p-4">
                  <p className="text-slate-600 text-sm mb-3">{contactSearch.trim() ? 'No contacts match your search.' : 'No contacts yet. Use Quick Compose in the generator to create emails.'}</p>
                </div>
              ) : (
                <>
                  <label className="px-4 py-2 border-b border-[var(--border)] flex items-center gap-2 bg-white dark:bg-[var(--bg-card)] text-xs text-[var(--text-muted)] cursor-pointer">
                    <input
                      type="checkbox"
                      checked={groupByCompany}
                      onChange={(e) => setGroupByCompany(e.target.checked)}
                      className="rounded"
                    />
                    Group by company
                  </label>
                  {groupByCompany ? (
                    <div className="p-2 space-y-2">
                      {groupContactsByCompany(contacts).map(({ company, contacts: companyContacts }) => (
                        <CompanyFolder
                          key={company}
                          company={company}
                          contacts={companyContacts}
                          selected={selected}
                          bulkSelectedIds={sidebarSelectedIds}
                          onToggleBulk={toggleSidebarBulk}
                          onToggleAllInCompany={toggleSidebarBulkForCompany}
                          onSelect={(c) => {
                            setSelected(c);
                            setEmail(null);
                            setSelectedDraftId(null);
                          }}
                        />
                      ))}
                    </div>
                  ) : (
                    contacts.map((c) => (
                      <div
                        key={c.id}
                        className={`flex items-stretch gap-1 px-1 border-b border-slate-200/50 dark:border-slate-600/50 hover:bg-pale-sky/[0.08] dark:hover:bg-slate-700/40 ${
                          selected?.id === c.id ? 'bg-white dark:bg-slate-600/50 border-l-4 border-l-[var(--accent)] shadow-sm' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={sidebarSelectedIds.has(c.id)}
                          onChange={() => toggleSidebarBulk(c.id)}
                          aria-label={`Select ${c.name || c.email}`}
                          className="mt-2.5 ml-1 rounded border-slate-400 dark:border-slate-500 text-[var(--accent)] shrink-0"
                        />
                        <button
                          type="button"
                          onClick={() => {
                            setSelected(c);
                            setEmail(null);
                            setSelectedDraftId(null);
                          }}
                          className="flex-1 min-w-0 text-left py-2 pr-2"
                        >
                          <div className="font-medium text-deep-navy dark:text-[var(--text-primary)] text-xs truncate">{c.name || c.email}</div>
                          <div className="text-xs text-[var(--text-muted)] truncate">{c.title}{c.company ? ` • ${c.company}` : ''}</div>
                          {lastSendHint(c) && <div className="text-[11px] text-[var(--text-muted)] truncate">{lastSendHint(c)}</div>}
                        </button>
                      </div>
                    ))
                  )}
                </>
              )}
              </>
            ) : (
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <label className="text-sm text-[var(--text-muted)]">Sort:</label>
                  <select aria-label="Draft order"
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value)}
                    className="text-sm px-2 py-1 rounded bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-100"
                  >
                    <option value="created_desc">Newest First</option>
                    <option value="created_asc">Oldest First</option>
                    <option value="contact">By Contact</option>
                  </select>
                  <button
                    type="button"
                    onClick={clearGeneratedEmailCache}
                    disabled={generatedClearBusy}
                    className="ui-button ui-button--secondary ui-button--sm"
                  >
                    {generatedClearBusy ? 'Clearing…' : 'Delete all drafts'}
                  </button>
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mb-2">
                  Drafts are stored in the shared app database and remain private to your account.
                </p>
                {generatedEmails.length === 0 ? (
                  <p className="text-slate-600 text-sm">No saved drafts yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {generatedEmails.map((ge) => (
                      <li
                        key={ge.id}
                        className="p-2 rounded border border-pale-sky bg-white dark:bg-transparent hover:bg-pale-sky/15 dark:hover:bg-slate-700/50 cursor-pointer"
                        onClick={() => loadDraftIntoEditor(ge)}
                      >
                        <div className="font-medium text-sm">{ge.name || ge.email}</div>
                        <div className="text-xs text-slate-500 truncate">{ge.subject}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center py-4 xl:py-6 gap-2">
              <button
                onClick={() => setContactsPanelExpanded(true)}
                className="p-2 rounded text-slate-500 hover:bg-pale-sky/30 hover:text-deep-navy"
                title="Expand panel"
                aria-label="Expand contacts panel"
              >
                ▶
              </button>
              <span className="text-xs text-slate-500 hidden xl:block" style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}>
                Contacts
              </span>
            </div>
          )}
        </div>
        <div className="email-studio-main">
          <div
            id="email-generator-section"
            data-collapsed={!aiGeneratorExpanded}
            className={`studio-step studio-panel email-studio-generator w-full ${mobileStep === 'generate' ? 'is-active' : ''} surface-card shadow-sm rounded-xl flex flex-col min-w-0`}
          >
            {aiGeneratorExpanded ? (
            <>
            <div className="email-generator-header px-4 py-3 border-b border-pale-sky dark:border-slate-600 flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => setAiGeneratorExpanded(false)}
                className="p-1.5 rounded text-slate-500 hover:bg-pale-sky/20 dark:hover:bg-slate-600 shrink-0"
                title="Collapse panel"
                aria-label="Collapse AI generator panel"
              >
                ◀
              </button>
              <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)]">AI assistance</h2>
            </div>
            <div className="email-studio-generator-body">
            {generationError && <p className="ui-notice ui-notice--danger" role="alert">{generationError}</p>}
            <div className="email-studio-field email-studio-field--grow">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Email goal</label>
                <textarea
                  value={draftDescription}
                  onChange={(e) => setDraftDescription(e.target.value)}
                  placeholder="For example: ask for a 20-minute call about a spring market research project"
                  rows={3}
                  className="email-studio-grow-field w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
            </div>
            <div className="email-studio-brief-grid">
            <div className="email-studio-field">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Audience context</label>
                <textarea
                  value={draftTargetAudience}
                  onChange={(e) => setDraftTargetAudience(e.target.value)}
                  placeholder="For example: strategy leaders at growing healthcare companies"
                  rows={2}
                  className="email-studio-grow-field w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
            </div>
            <div className="email-studio-field">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Company</label>
                <input
                  type="text"
                  value={draftCompany}
                  onChange={(e) => setDraftCompany(e.target.value)}
                  placeholder="e.g. Acme Corp"
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
            </div>
            <div className="email-studio-field">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <label className="block text-sm text-slate-600 dark:text-slate-400">Relevant YUCG capability or proof</label>
                  <button
                    type="button"
                    onClick={handleSuggestCitations}
                    disabled={citeSuggestLoading || !selected?.company}
                    title={selected?.company ? 'Suggest real past projects and team experience to cite' : 'Choose a contact with a company on file first'}
                    className="ui-button ui-button--secondary ui-button--sm shrink-0"
                  >
                    {citeSuggestLoading ? 'Looking…' : 'Suggest what to cite'}
                  </button>
                </div>
                {citeSuggestMessage && <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{citeSuggestMessage}</p>}
                <textarea
                  value={valueProp}
                  onChange={(e) => setValueProp(e.target.value)}
                  placeholder="Use only verified facts, such as a relevant service or approved case study"
                  rows={2}
                  className="email-studio-grow-field w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
            </div>
            <div className="email-studio-field">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Facts and constraints</label>
                <textarea
                  value={customInstructions}
                  onChange={(e) => setCustomInstructions(e.target.value)}
                  placeholder="For example: refer to the supplied expansion announcement; avoid client names"
                  rows={2}
                  className="email-studio-grow-field w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
            </div>
            </div>
            <div className="border-t border-pale-sky dark:border-slate-600 pt-3">
              <h3 className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Recipient details</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input
                  type="text"
                  placeholder="Name"
                  value={quickCompose.name}
                  onChange={(e) => setQuickCompose((p) => ({ ...p, name: e.target.value }))}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 text-sm placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
                <input
                  type="email"
                  placeholder="Email (for test send)"
                  value={quickCompose.email}
                  onChange={(e) => setQuickCompose((p) => ({ ...p, email: e.target.value }))}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 text-sm placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
                <input
                  type="text"
                  placeholder="Company"
                  value={quickCompose.company}
                  onChange={(e) => setQuickCompose((p) => ({ ...p, company: e.target.value }))}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 text-sm placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
                <input
                  type="text"
                  placeholder="Title"
                  value={quickCompose.title}
                  onChange={(e) => setQuickCompose((p) => ({ ...p, title: e.target.value }))}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200 text-sm placeholder:text-slate-500 dark:placeholder-slate-500 caret-deep-navy dark:caret-slate-200"
                />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
              <div className="min-w-0">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Tone</label>
                <select aria-label="Tone"
                  value={tone}
                  onChange={(e) => setTone(e.target.value)}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200"
                >
                  {['professional', 'conversational', 'bold', 'empathetic', 'authority'].map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div className="min-w-0">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Length</label>
                <select aria-label="Email length"
                  value={length}
                  onChange={(e) => setLength(e.target.value)}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200"
                >
                  {['ultra-short', 'short', 'standard'].map((l) => (
                    <option key={l} value={l}>{l}</option>
                  ))}
                </select>
              </div>
              <div className="min-w-0">
                <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Angle</label>
                <select aria-label="Message angle"
                  value={angle}
                  onChange={(e) => setAngle(e.target.value)}
                  className="w-full min-w-0 px-3 py-2 rounded-lg bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 text-deep-navy dark:text-slate-200"
                >
                  {['pain_point', 'social_proof', 'case_study', 'compliment'].map((a) => (
                    <option key={a} value={a}>{a.replace('_', ' ')}</option>
                  ))}
                </select>
              </div>
            </div>
            </div>
            <div className="studio-generate-row">
              <AiModelSelect id="studio-ai-model" compact />
              <button
                onClick={generateEmail}
                disabled={loading}
                className="ui-button ui-button--primary studio-generate-row__go active:scale-[0.98] transition-all"
              >
                {loading ? 'Generating…' : 'Generate email'}
              </button>
            </div>
            </>
            ) : (
            <div className="flex flex-col items-center py-4 xl:py-6 gap-2">
              <button
                type="button"
                onClick={() => setAiGeneratorExpanded(true)}
                className="p-2 rounded text-slate-500 hover:bg-pale-sky/30 dark:hover:bg-slate-600/50"
                title="Expand panel"
                aria-label="Expand AI generator panel"
              >
                ▶
              </button>
              <span className="text-xs text-slate-500 dark:text-slate-400 hidden xl:block" style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}>
                AI Generator
              </span>
            </div>
            )}
          </div>
          <div id="email-editor-section" className={`studio-step ${mobileStep === 'edit' ? 'is-active' : ''} flex-1 min-w-0 surface-card shadow-sm rounded-xl min-h-0 flex flex-col`}>
            <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)] p-4 border-b border-pale-sky dark:border-slate-600 truncate" title={`Email for ${selected?.name || quickCompose.name || 'Recipient'} (${selected?.email || quickCompose.email || 'enter email for test send'})`}>
              Email for {selected?.name || quickCompose.name || 'Recipient'} ({selected?.email || quickCompose.email || 'enter email for test send'})
            </h2>
            <AddressCheck email={selected?.email || quickCompose.email || ''} />
            <div className="email-studio-campaign border-b border-[var(--border)]">
              <button
                type="button"
                onClick={() => setCampaignPanelOpen((v) => !v)}
                className="w-full px-4 py-2.5 flex items-center justify-between text-left text-sm font-semibold text-deep-navy dark:text-[var(--text-primary)] bg-white dark:bg-[var(--bg-card)] hover:bg-pale-sky/10 dark:hover:bg-slate-700/40"
              >
                <span>Add this draft to a campaign</span>
                <span className="ui-disclosure-chevron text-[var(--text-muted)]" data-open={campaignPanelOpen} aria-hidden>▼</span>
              </button>
              <div className="ui-disclosure" data-open={campaignPanelOpen}>
                <div className="overflow-hidden min-h-0">
                <div className="px-4 py-3 space-y-2 text-sm border-t border-[var(--border)] bg-white dark:bg-[var(--bg-card)]">
                  <p className="text-xs text-[var(--text-muted)] leading-relaxed">
                    Load employees for the companies you tick (generic inboxes like info@ are skipped). Write below, then save or send. Follow-ups run on the daily job only for people who have not replied — sync Gmail on Pipeline.
                  </p>
                  {/* The picker asks the server for a page of companies that
                      match what is typed. Loading every distinct company was
                      fine at a few dozen and unusable at five figures. */}
                  <div className="rounded-lg border border-slate-200 dark:border-slate-600 overflow-hidden bg-white dark:bg-[var(--bg-card)]">
                      <button
                        type="button"
                        onClick={() => setStudioCompanyListOpen((v) => !v)}
                        className="w-full px-3 py-2.5 flex items-center justify-between gap-2 text-left bg-white dark:bg-[var(--bg-card)] border-b border-slate-200 dark:border-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700/40"
                        aria-expanded={studioCompanyListOpen}
                      >
                        <span className="text-sm font-semibold text-deep-navy dark:text-[var(--text-primary)] min-w-0">
                          Companies to include{' '}
                          <span className="font-normal text-[var(--text-muted)]">
                            ({companiesSummary.length}{companiesSummary.length >= COMPANY_PAGE ? '+' : ''})
                            {selectedCompanyNames.size > 0 ? ` · ${selectedCompanyNames.size} selected` : ''}
                          </span>
                        </span>
                        <span className="ui-disclosure-chevron text-[var(--text-muted)] shrink-0" data-open={studioCompanyListOpen} aria-hidden>▼</span>
                      </button>
                      <div className="ui-disclosure" data-open={studioCompanyListOpen}>
                        <div className="overflow-hidden min-h-0">
                          <div className="p-2 border-t border-slate-100 dark:border-slate-700/60 bg-white dark:bg-slate-800/40">
                            <input
                              type="search"
                              value={companySearch}
                              onChange={(e) => setCompanySearch(e.target.value)}
                              placeholder="Search companies"
                              aria-label="Search companies"
                              className="ui-input ui-input--sm w-full"
                            />
                          </div>
                          <div className="max-h-48 overflow-y-auto p-2 space-y-1.5 bg-white dark:bg-slate-800/40">
                            {companiesSummary.length === 0 && (
                              <p className="text-xs text-[var(--text-muted)] px-1 py-2">
                                {companySearch.trim() ? 'No company matches that search.' : 'No companies in the database yet — find or import contacts first.'}
                              </p>
                            )}
                            {companiesSummary.map((row) => (
                              <label
                                key={`${row.company}-${row.company_domain || ''}`}
                                className="flex items-center gap-2 cursor-pointer text-deep-navy dark:text-[var(--text-primary)] rounded-md px-1 py-0.5 hover:bg-slate-50 dark:hover:bg-slate-700/50"
                              >
                                <input
                                  type="checkbox"
                                  checked={selectedCompanyNames.has(row.company)}
                                  onChange={() => toggleCompanyPick(row.company)}
                                  className="rounded border-slate-400 dark:border-slate-500 text-[var(--accent)] focus:ring-[var(--accent)] shrink-0"
                                />
                                <span className="truncate min-w-0">{row.company}</span>
                                <span className="text-xs text-[var(--text-muted)] shrink-0">({row.contact_count})</span>
                              </label>
                            ))}
                            {companiesSummary.length >= COMPANY_PAGE && (
                              <p className="text-xs text-[var(--text-muted)] px-1 pt-1">
                                Showing the first {COMPANY_PAGE}. Search to narrow.
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                  </div>
                  <div className="flex flex-wrap gap-2 items-center">
                    <button
                      type="button"
                      onClick={loadStudioContactsForCompanies}
                      disabled={campaignBusy}
                      className="ui-button ui-button--primary ui-button--sm"
                    >
                      Load employee contacts
                    </button>
                    <span className="text-xs text-[var(--text-muted)]">{studioCampaignContacts.length} loaded</span>
                    {studioCampaignContacts.length > 0 && (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <button
                          type="button"
                          onClick={() => setSelectedCampaignContactIds(new Set(studioCampaignContacts.map((c) => c.id)))}
                          className="ui-button ui-button--ghost ui-button--sm"
                        >
                          Select all
                        </button>
                        <button
                          type="button"
                          title="Clear ticked selection for this loaded list only"
                          onClick={() => setSelectedCampaignContactIds(new Set())}
                          className="ui-button ui-button--ghost ui-button--sm"
                        >
                          Clear
                        </button>
                        <button
                          type="button"
                          disabled={studioListDeleting || selectedCampaignContactIds.size === 0}
                          title="Permanently remove selected contacts from the database"
                          onClick={deleteSelectedStudioContactsFromDb}
                          className="ui-button ui-button--danger ui-button--sm"
                        >
                          {studioListDeleting ? 'Deleting…' : 'Delete from database'}
                        </button>
                      </div>
                    )}
                  </div>
                  {studioCampaignContacts.length > 0 && (
                    <div className="rounded-lg border border-slate-200 dark:border-slate-600 overflow-hidden bg-white dark:bg-[var(--bg-card)]">
                      <button
                        type="button"
                        onClick={() => setStudioContactsListOpen((v) => !v)}
                        className="w-full px-3 py-2.5 flex items-center justify-between gap-2 text-left bg-white border-b border-slate-200 hover:bg-slate-50"
                        aria-expanded={studioContactsListOpen}
                      >
                        <span className="text-sm font-semibold text-deep-navy">
                          Loaded contacts{' '}
                          <span className="font-normal text-slate-600">({studioCampaignContacts.length})</span>
                        </span>
                        <span className="ui-disclosure-chevron text-slate-500 shrink-0" data-open={studioContactsListOpen} aria-hidden>▼</span>
                      </button>
                      <div className="ui-disclosure" data-open={studioContactsListOpen}>
                        <div className="overflow-hidden min-h-0">
                          <div className="max-h-64 overflow-y-auto bg-white divide-y divide-slate-100">
                            {studioCampaignContacts.map((c) => (
                              <label
                                key={c.id}
                                className="flex items-center gap-2 px-3 py-2 cursor-pointer bg-white hover:bg-slate-50"
                              >
                                <input
                                  type="checkbox"
                                  checked={selectedCampaignContactIds.has(c.id)}
                                  onChange={() => {
                                    setSelectedCampaignContactIds((prev) => {
                                      const n = new Set(prev);
                                      if (n.has(c.id)) n.delete(c.id);
                                      else n.add(c.id);
                                      return n;
                                    });
                                  }}
                                  className="rounded border-slate-400 text-[var(--accent)] focus:ring-[var(--accent)] shrink-0"
                                  aria-label={`Select ${c.name || c.email}`}
                                />
                                <span className="truncate text-sm font-medium text-deep-navy min-w-0">
                                  {c.name || c.email}
                                </span>
                                <span className="text-xs text-slate-600 truncate min-w-0">{c.title}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                  {selectedCampaignContactIds.size > 1 && (
                    <p className="rounded-lg bg-pale-sky/25 p-3 text-xs text-slate-700">
                      For a shared template, use <code>{'{{first_name}}'}</code>, <code>{'{{name}}'}</code>, <code>{'{{company}}'}</code>, <code>{'{{title}}'}</code>, or <code>{'{{email}}'}</code>.
                      Without placeholders, this editor applies only to the person currently open; every other recipient keeps their own saved draft for review.
                    </p>
                  )}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="block text-xs font-medium text-deep-navy dark:text-slate-400 mb-1">Campaign name</label>
                      <input
                        value={campaignName}
                        onChange={(e) => setCampaignName(e.target.value)}
                        placeholder="e.g. Spring outreach — Acme"
                        className="w-full px-2 py-1.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm text-deep-navy dark:text-slate-100 placeholder:text-slate-500 dark:placeholder:text-slate-500 caret-deep-navy dark:caret-slate-200"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-deep-navy dark:text-slate-400 mb-1">Follow-up sequence (optional)</label>
                      <select aria-label="Follow-up sequence"
                        value={campaignSequenceId}
                        onChange={(e) => setCampaignSequenceId(e.target.value)}
                        className="w-full px-2 py-1.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm text-deep-navy dark:text-slate-100"
                      >
                        <option value="">None</option>
                        {sequences.map((s) => (
                          <option key={s.id} value={String(s.id)}>{s.name} ({(s.steps || []).length} steps)</option>
                        ))}
                      </select>
                      <p className="text-[10px] text-[var(--text-muted)] mt-1 leading-snug">
                        After the campaign is sent, the daily job emails the next step when due. Recipients marked as replied are skipped.
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={campaignBusy}
                      onClick={buildCampaignFromStudio}
                      className="ui-button ui-button--primary"
                    >
                      Save campaign draft
                    </button>
                    <Link
                      to={createdCampaignId ? `/campaigns/${createdCampaignId}` : '/'}
                      className="inline-flex items-center px-3 py-2 text-sm font-medium text-[var(--accent)] hover:text-[var(--accent-hover)] underline underline-offset-2"
                    >
                      {createdCampaignId ? 'Review this campaign' : 'Go to Home'}
                    </Link>
                  </div>
                  {campaignMessage && (
                    <p className="text-xs text-deep-navy dark:text-slate-300 whitespace-pre-wrap">{campaignMessage}</p>
                  )}
                </div>
                </div>
              </div>
            </div>
            <div className="email-studio-compose divide-x divide-pale-sky dark:divide-slate-600">
              <div className="p-4 min-w-0 email-studio-editor-column">
                <h3 className="text-sm font-medium text-deep-navy dark:text-slate-400 mb-2">Your draft</h3>
                <EmailToolbar
                  editorRef={bodyRef}
                  onChange={(html) => setEmail((prev) => ({ ...(prev || { subject: '', body: '' }), body: sanitizeRichText(html) }))}
                />
                <div className="email-studio-editor-stack">
                  <div className="min-w-0">
                    <label htmlFor="studio-subject" className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Subject</label>
                    <input
                      id="studio-subject"
                      type="text"
                      value={email?.subject ?? ''}
                      onChange={(e) => setEmail((prev) => ({ ...(prev || { subject: '', body: '' }), subject: e.target.value }))}
                      placeholder="Enter subject line..."
                      className="email-studio-input w-full min-w-0 px-3 py-2 rounded-lg border border-slate-300 bg-white text-deep-navy placeholder:text-slate-500 caret-deep-navy dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200 dark:placeholder-slate-500 dark:caret-slate-200"
                    />
                  </div>
                  <div className="email-studio-body-wrap">
                    <label id="studio-body-label" className="block text-sm text-slate-600 dark:text-slate-400 mb-1">Message</label>
                    <div className="relative min-w-0 flex-1 min-h-0">
                    <div
                      ref={bodyRef}
                      contentEditable
                      role="textbox"
                      aria-labelledby="studio-body-label"
                      aria-multiline="true"
                      suppressContentEditableWarning
                      onPaste={event => { event.preventDefault(); insertSafeTransfer(event.clipboardData); }}
                      onDrop={event => {
                        event.preventDefault();
                        const files = Array.from(event.dataTransfer.files || []);
                        if (files.length) void addAttachmentFiles(files);
                        else insertSafeTransfer(event.dataTransfer);
                      }}
                      onInput={(e) => setEmail((prev) => ({ ...(prev || { subject: '', body: '' }), body: sanitizeRichText((e.target as HTMLDivElement).innerHTML) }))}
                      style={{ fontFamily: "'Lato', system-ui, sans-serif", fontSize: BASE_FONT_SIZE }}
                      className="email-studio-body min-h-[280px] h-full w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-deep-navy caret-deep-navy resize-y overflow-auto focus:outline-none focus:ring-2 focus:ring-[var(--accent)] focus:ring-offset-0 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200 dark:caret-slate-200 dark:focus:ring-offset-transparent"
                    />
                    {(!email?.body || email.body === '' || (email.body.replace(/<[^>]*>/g, '').trim() === '')) && (
                      <span className="absolute left-3 top-2 text-slate-600 dark:text-slate-300 pointer-events-none text-sm">
                        Write your email here or use AI assistance to create a starting draft.
                      </span>
                    )}
                  </div>
                  </div>
                    <div className="email-studio-block w-full rounded-lg border border-dashed border-slate-300 dark:border-slate-600 bg-slate-50/60 dark:bg-slate-800/30 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-deep-navy dark:text-[var(--text-primary)]">Attachments</span>
                        <label className="ui-button ui-button--secondary ui-button--sm cursor-pointer">
                          {attachmentUploading ? 'Uploading…' : 'Add files'}
                          <input
                            type="file"
                            multiple
                            className="sr-only"
                            disabled={attachmentUploading}
                            accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif"
                            onChange={(event) => {
                              void addAttachmentFiles(Array.from(event.target.files || []));
                              event.target.value = '';
                            }}
                          />
                        </label>
                        <span className="text-xs text-[var(--text-muted)]">or drop files into the message</span>
                      </div>
                      {attachmentLibrary.length > selectedAttachments.length && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-xs text-[var(--accent)]">Choose from saved files</summary>
                          <div className="flex flex-wrap gap-2 mt-2">
                            {attachmentLibrary.filter((item) => !selectedAttachmentIds.has(item.id)).map((item) => (
                              <button key={item.id} type="button" onClick={() => toggleAttachment(item.id)} className="ui-button ui-button--ghost ui-button--sm">
                                + {item.display_name || item.filename}
                              </button>
                            ))}
                          </div>
                        </details>
                      )}
                      {selectedAttachments.length > 0 && (
                        <ul className="mt-3 border-t border-slate-200 dark:border-slate-600 pt-2 space-y-1" aria-label="Attached files">
                          {selectedAttachments.map((item) => (
                            <li key={item.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                              <span className="text-xs font-medium text-[var(--text-muted)]">File</span>
                              <span className="truncate">{item.display_name || item.filename}</span>
                              <button type="button" onClick={() => toggleAttachment(item.id)} className="ml-auto text-xs text-[var(--text-muted)] hover:text-red-700">Remove</button>
                            </li>
                          ))}
                        </ul>
                      )}
                      {user.role === 'admin' && (
                        <button
                          type="button"
                          disabled={onedriveBusy}
                          onClick={async () => {
                            setOnedriveBusy(true);
                            try {
                              const response = await api.attachments.onedrive.list();
                              setOnedriveConfigured(response.configured);
                              setOnedriveItems(response.items || []);
                              setOnedriveOpen(true);
                            } catch (error) {
                              setCampaignMessage(error instanceof Error ? error.message : 'OneDrive list failed');
                            } finally {
                              setOnedriveBusy(false);
                            }
                          }}
                          className="mt-2 text-xs text-[var(--accent)] hover:underline"
                        >
                          Import from club OneDrive
                        </button>
                      )}
                    </div>
                  {(signOff.name || signOff.role) && (
                    <div className="w-full border-t border-slate-200 dark:border-slate-600 pt-3 flex items-start gap-3 text-xs text-slate-600 dark:text-slate-300">
                      {signOff.logoUrl && <img src={signOff.logoUrl} alt="YUCG" className="w-14 h-auto object-contain" />}
                      <div>
                        <div className="font-bold text-deep-navy dark:text-slate-100">{signOff.name} {signOff.pronouns && <em className="font-normal">({signOff.pronouns})</em>}</div>
                        {signOff.role && <div>{signOff.role}</div>}
                        <div>{signOff.organization}</div>
                        {(signOff.linkedin || signOff.phone) && <div>{signOff.linkedin ? 'LinkedIn' : ''}{signOff.linkedin && signOff.phone ? ' | ' : ''}{signOff.phone}</div>}
                      </div>
                    </div>
                  )}
                  {/* One row, one height. The delivery target used to sit in a
                      sentence beside the buttons, which wrapped the row onto a
                      second line; it belongs on the button that uses it. */}
                  <div className="studio-draft-actions flex items-center gap-2 min-w-0">
                    <button
                      onClick={saveCurrentAsDraft}
                      disabled={draftSaving || (!email?.subject && !email?.body)}
                      className="ui-button ui-button--primary shrink-0"
                    >
                      {draftSaving ? 'Saving…' : selectedDraftId ? 'Update draft' : 'Save draft'}
                    </button>
                    <button
                      onClick={testSend}
                      disabled={testSending || !email?.body}
                      title={`Sends this draft to ${user?.email || 'your inbox'} so you can check delivery`}
                      className="ui-button ui-button--secondary min-w-0"
                    >
                      <span className="truncate">
                        {testSending ? 'Sending…' : `Send test to ${user?.email || 'myself'}`}
                      </span>
                    </button>
                    <input
                      type="text"
                      value={sentimentIndustry}
                      onChange={(e) => setSentimentIndustry(e.target.value)}
                      placeholder="Industry"
                      aria-label="Industry for sentiment analysis"
                      style={{ width: '6.5rem' }}
                      className="ui-input ml-auto shrink-0"
                    />
                    <button
                      onClick={analyzeSentiment}
                      disabled={sentimentLoading || (!email?.subject && !email?.body)}
                      className="ui-button ui-button--secondary shrink-0"
                    >
                      {sentimentLoading ? 'Analyzing…' : 'Analyze'}
                    </button>
                  </div>
                  {draftMessage && <p role="status" className="text-sm text-slate-600 dark:text-slate-300">{draftMessage}</p>}
                  {sentimentAnalysis && (
                    <div className="mt-4 p-4 rounded-lg border border-pale-sky dark:border-slate-600 bg-white dark:bg-slate-700/30">
                      <h4 className="font-medium text-deep-navy dark:text-[var(--text-primary)] mb-2">Sentiment Analysis</h4>
                      {sentimentAnalysis.error ? (
                        <p className="text-red-600 dark:text-red-400 text-sm">{sentimentAnalysis.error}</p>
                      ) : (
                        <div className="space-y-2 text-sm">
                          <div>
                            <span className="text-slate-600 dark:text-slate-400">Score:</span>{' '}
                            <span className="font-medium">{(sentimentAnalysis.sentiment_score ?? 0).toFixed(2)}</span>
                            <span className="text-slate-500 ml-2">({sentimentAnalysis.sentiment_label})</span>
                          </div>
                          {sentimentAnalysis.industry_fit && (
                            <div>
                              <span className="text-slate-600">Industry fit:</span>{' '}
                              <span className="text-deep-navy dark:text-slate-200">{sentimentAnalysis.industry_fit}</span>
                            </div>
                          )}
                          {sentimentAnalysis.suggested_improvements && (
                            <div>
                              <span className="text-slate-600">Suggestions:</span>
                              <p className="text-deep-navy dark:text-slate-200 whitespace-pre-wrap mt-1">{sentimentAnalysis.suggested_improvements}</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div className="email-studio-gmail-rail p-4 min-w-0 border-l border-[var(--border)]">
                <h3 className="text-sm font-medium text-deep-navy dark:text-[var(--text-primary)] mb-2 flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-steel-blue animate-pulse" />
                  Email preview
                </h3>
                <div className="email-studio-gmail-card rounded-lg overflow-hidden min-h-[280px]">
                  <div className="email-studio-gmail-toolbar px-4 py-2 flex items-center gap-3 flex-wrap">
                    <span>Recipient view · appearance varies by email app</span>
                  </div>
                  <div className="email-studio-gmail-body p-4">
                    <div className="flex items-start gap-3 mb-4">
                      <div className="w-10 h-10 rounded-full bg-pale-sky flex items-center justify-center text-deep-navy font-bold text-sm shrink-0">
                        Y
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-deep-navy dark:text-slate-800">{user.name || user.email}</span>
                          <span className="text-slate-500 text-sm">&lt;{user.email}&gt;</span>
                        </div>
                        <div className="text-slate-500 text-sm mt-0.5">To: {selected?.email || quickCompose.email || 'Choose a recipient'}</div>
                      </div>
                      <div className="text-slate-400 text-xs shrink-0">
                        {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}
                      </div>
                    </div>
                    <div className="text-lg font-medium text-deep-navy dark:text-slate-800 mb-3 border-b border-slate-100 pb-2">
                      {email?.subject || 'No subject'}
                    </div>
                    <div
                      className="text-slate-700 leading-relaxed prose prose-sm max-w-none"
                      style={{
                        fontFamily: "'Lato', system-ui, sans-serif",
                        fontSize: `${BASE_FONT_SIZE}px`,
                      }}
                    >
                      {email?.body && /<[a-z][\s\S]*>/i.test(email.body) ? (
                        <div dangerouslySetInnerHTML={{ __html: sanitizeRichText(previewBody) }} />
                      ) : (
                        <span className="whitespace-pre-wrap">{previewBody || 'Start typing above or generate a draft.'}</span>
                      )}
                    </div>
                    {selectedAttachments.length > 0 && (
                      <div className="mt-4 text-xs text-slate-500">
                        <strong>Attached</strong>
                        <ul className="list-disc ml-5 mt-1">{selectedAttachments.map((item) => <li key={item.id}>{item.display_name || item.filename}</li>)}</ul>
                      </div>
                    )}
                    {(signOff.name || signOff.role) && (
                      <div className="mt-5 border-t border-slate-200 pt-3 flex items-start gap-3 text-xs text-slate-700">
                        {signOff.logoUrl && <img src={signOff.logoUrl} alt="YUCG" className="w-[72px] h-auto object-contain" />}
                        <div>
                          <div className="font-bold text-deep-navy">{signOff.name} {signOff.pronouns && <em className="font-normal">({signOff.pronouns})</em>}</div>
                          {signOff.role && <div>{signOff.role}</div>}
                          <div>{signOff.organization}</div>
                          {(signOff.linkedin || signOff.phone) && <div className="mt-0.5 text-deep-navy">{signOff.linkedin ? 'LinkedIn' : ''}{signOff.linkedin && signOff.phone ? ' | ' : ''}{signOff.phone}</div>}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      {onedriveOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-label="OneDrive files">
          <div className="surface-card w-full max-w-lg rounded-xl p-5 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold text-deep-navy">OneDrive</h2>
              <button type="button" className="ui-button ui-button--ghost" onClick={() => setOnedriveOpen(false)}>
                Close
              </button>
            </div>
            {!onedriveConfigured ? (
              <p className="text-sm text-slate-600">Set GRAPH_ACCESS_TOKEN on the API to list the club folder.</p>
            ) : onedriveItems.length === 0 ? (
              <p className="text-sm text-slate-600">No files in GRAPH_DRIVE_FOLDER.</p>
            ) : (
              <ul className="max-h-72 overflow-y-auto divide-y divide-pale-sky">
                {onedriveItems.filter((i) => !i.folder).map((item) => (
                  <li key={item.id} className="py-2 flex items-center justify-between gap-2">
                    <span className="text-sm truncate" title={item.name}>{item.name}</span>
                    <button
                      type="button"
                      disabled={onedriveBusy}
                      className="ui-button ui-button--secondary ui-button--sm"
                      onClick={async () => {
                        setOnedriveBusy(true);
                        try {
                          await api.attachments.onedrive.attach(item.id);
                          const lib = await api.attachments.list();
                          setAttachmentLibrary(lib);
                          setOnedriveOpen(false);
                        } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
                          setCampaignMessage(eMessage || 'Attach failed');
                        } finally {
                          setOnedriveBusy(false);
                        }
                      }}
                    >
                      Attach
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
