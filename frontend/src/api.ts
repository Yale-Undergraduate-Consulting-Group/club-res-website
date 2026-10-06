export type MemberProfile = { projects?: string; experience?: string; role_title?: string; linkedin_url?: string; slack_handle?: string; other_handles?: string };
export type Project = { id: number; name: string; semester?: string; description?: string; role_in_project?: string; client_name?: string; discussable?: boolean };
export type Attachment = { id: number; filename: string; display_name?: string; file_size: number };
export type Template = { id: number; name: string; subject: string; body: string; industry?: string; use_case?: string };
export type Sequence = { id: number; name: string; steps?: { days_after: number; subject: string; body: string }[] };
export type ContactNote = { id: number; note: string; created_at: string; user_id?: number };
export type ContactActivity = { id: number; activity_type: string; details?: string; created_at: string; user_id?: number };
export type ContactProfile = { value_proposition?: string; role_summary?: string; online_sentiment?: string; receptiveness_notes?: string };
export type Sentiment = { error?: string; sentiment_score?: number; sentiment_label?: string; industry_fit?: string; suggested_improvements?: string };
export type Worklist = { owner_email?: string; owner_name?: string; id: number; name: string; type: string; description?: string; contact_count?: number; contacts?: Contact[] };
export type ReleasePerson = { id: number; full_name?: string; title?: string; email?: string; company?: string; company_domain?: string; kept: number; email_status?: string; vendor_check?: string };
export type Release = { id: number; name: string; status: string; people?: ReleasePerson[]; targets?: { id: number; company: string; company_domain?: string }[] };
export type InboxItem = { name?: string; email?: string; status?: string; email_verification_status?: string; id: number; subject?: string; body?: string; from_email?: string; received_at?: string };
export type CitationSuggestions = {
  projects: { id: number; client_name?: string; description?: string; semester?: string }[];
  team_experience: { user_name?: string; role_in_project?: string; client_name?: string; semester?: string }[];
};
export type Member = { id: number; email: string; name?: string; role: string; is_active: number; last_login?: string };
export type LogEntry = { user_id?: number; name?: string; id: number; created_at: string; email?: string; user_email?: string; action?: string; details?: string; ip_address?: string; event_type?: string; resource_type?: string };
export type ApiKey = { key_prefix?: string; id: number; name: string; scopes?: string; created_at?: string; last_used_at?: string };
export type StoredObject = { byte_size?: number; source?: string; id: number; kind: string; s3_key: string; bytes?: number; created_at?: string };
export type CustomFormat = { id: number; name: string; pattern: string; priority?: number };
export type Settings = {
  sign_off_name?: string;
  sign_off_pronouns?: string;
  sign_off_role?: string;
  sign_off_organization?: string;
  sign_off_linkedin?: string;
  sign_off_phone?: string;
  sign_off_logo_url?: string;
  daily_send_limit?: number;
  daily_send_warn_at?: number | string;
};

/** Pacing for a release: the drain claims at most daily_limit per day. */
export type SendAllowance = {
  daily_limit: number;
  queued: number;
  days_to_drain: number;
  warn_at?: number | null;
  warning?: string;
};
export type PipelineMetrics = { by_status: { pipeline_status: string; count: number }[] };
export type OneDriveItem = { id: string; name: string; folder?: object; size?: number };

export type LeaderboardRow = {
  user_id: number;
  name: string | null;
  picture: string | null;
  sent: number;
  replied: number;
  penalized_bounces: number;
  forgiven_bounces: number;
  companies_reached: number;
  quality_score: number;
};
export type CompanyReached = {
  company: string;
  company_domain?: string | null;
  contacts_reached: number;
  replies: number;
  last_sent_at?: string | null;
};


export type WebProbeResult = { ok: boolean; duration_s?: number; content_chars?: number; result_count?: number; error?: string; note?: string | null };
export type BackendProbeStatus = { configured: boolean; fetch?: WebProbeResult; search?: WebProbeResult };
export type FirecrawlStatus = {
  tinyfish: BackendProbeStatus;
  firecrawl: BackendProbeStatus;
  effective: { fetch: WebProbeResult; search: WebProbeResult; note: string };
};


export type RoleSuggestion = { title: string; count: number; source: 'roster' | 'run' | 'catalog' | 'jobs' | 'search' };
export type RoleEquivalent = { asked: string; at_company: string[]; note: string };
export type RoleSuggestions = {
  company: string;
  roles: RoleSuggestion[];
  equivalents: RoleEquivalent[];
  note?: string | null;
  sources: { run: number; roster: number; catalog: number; search: number; jobs: number };
};

export type DiscoveryRunStatus = 'queued' | 'running' | 'completed' | 'failed';
/** One durable Find people search, as the runs endpoints return it. */
export type DiscoveryRun = {
  id: number;
  company_name: string;
  company_domain?: string | null;
  status: DiscoveryRunStatus | string;
  progress_pct?: number | null;
  progress_message?: string | null;
  prospects_count?: number | null;
  max_prospects?: number | null;
  error_message?: string | null;
  created_at?: string | null;
};
/** A person a run found, before anyone chose to put them on file. */
export type DiscoveryProspect = {
  id: number;
  run_id: number;
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  title?: string | null;
  company?: string | null;
  linkedin_url?: string | null;
  contact_source?: string | null;
  ai_verdict?: string | null;
  ai_reason?: string | null;
  score?: number | null;
};
export type ImportOutcome = {
  prospect_id: number;
  contact_id: number | null;
  outcome: 'created' | 'updated' | 'skipped';
  reason?: string;
};
export type ImportOutcomes = {
  created: number;
  updated: number;
  skipped: number;
  results: ImportOutcome[];
};

export type CompanySummaryRow = {
  company: string;
  company_domain?: string;
  contact_count: number;
  last_sent_at?: string | null;
  campaign_count?: number;
  mailed_count?: number;
  replied_count?: number;
  bounced_count?: number;
  queued_count?: number;
};

export type FollowUpScheduleRow = {
  campaign_contact_id: number;
  contact_name?: string | null;
  email: string;
  company?: string | null;
  campaign_id: number;
  campaign_name?: string | null;
  campaign_status?: string | null;
  sequence_name?: string | null;
  next_subject?: string | null;
  due_on?: string | null;
  overdue?: boolean;
  reason?: string | null;
};

/** A measured section of the breakdown. The page renders whatever arrives,
 *  so a new measurement on the server needs no frontend change. */
export type BreakdownSection = {
  id: string;
  title: string;
  note?: string;
  chart: 'bar' | 'pie';
  rows: { label: string; value: number; secondary: number | null }[];
};

export type OutcomeSplit = {
  mailed: number; replied: number; bounced: number; queued: number;
  awaiting: number; reply_rate: number;
};

export type RegisterCompany = {
  id: number;
  source: string;
  tier: 'us_public' | 'us_private' | 'us_employer' | 'us_nonprofit' | 'uk';
  country: string;
  company_name: string;
  company_domain?: string | null;
  sector_code?: string | null;
  sector_label?: string | null;
  region?: string | null;
  employees?: number | null;
  employees_source?: string | null;
  last_event_at?: string | null;
  last_event_amount?: number | null;
  last_event_kind?: string | null;
  officer_count: number;
  working_count?: number;
  claimed_by?: string | null;
  metadata?: { revenue_range?: string | null; ticker?: string | null; city?: string | null; year_of_inc?: string | null; size_band?: string | null; account_category?: string | null; incorporated?: string | null; buys_outside_advice?: string | null; why_attractive?: string | null; target_role_title?: string | null; first_message_angle?: string | null; yale_hook?: string | null };
};
export type RegisterSummary = {
  tiers: { tier: string; country: string; n: number; with_officers: number }[];
  sectors: { sector: string; n: number }[];
  recent_ingests: { source: string; batch_key: string; rows_written: number; status: string; detail?: string | null; completed_at?: string }[];
};

export type PersonIdentity = 'unreviewed' | 'plausible' | 'corroborated' | 'conflicted' | 'rejected';
export type EmploymentEvidence = 'current_source_observed' | 'current_inferred' | 'stale' | 'former' | 'unknown';
export type AddressOrigin = 'published_by_company' | 'published_by_independent_source' | 'inferred_from_published_pattern' | 'user_supplied' | 'imported_without_evidence';
export type MailboxAssessment = 'not_checked' | 'bad_syntax' | 'domain_has_no_mail_route' | 'mail_route_available' | 'provider_high_confidence' | 'provider_medium_confidence' | 'accept_all_or_risky' | 'recipient_rejected' | 'inconclusive' | 'previously_delivered' | 'human_reply_observed' | 'permanent_failure_observed';
export type ProjectFit = 'strong' | 'possible' | 'weak' | 'excluded';
export type RecommendationState = 'ready_to_review' | 'needs_evidence' | 'excluded';
export type EvidenceSource = { id?: number | string; url: string; excerpt?: string; observed_at?: string | number | null };
export type ContactEvidence = {
  person_id?: number; candidate_id?: number; identity: PersonIdentity; employment: EmploymentEvidence;
  address_origin: AddressOrigin; mailbox: MailboxAssessment; project_fit: ProjectFit;
  checked_at?: string | number | null; method?: string; source_ids?: Array<number | string>; reason?: string;
  expires_at?: string | number | null; sources: EvidenceSource[]; conflicts: string[];
  recommendation_state?: RecommendationState;
};
export type AudienceSpec = {
  industries: string[]; companies: string[]; geography: string[]; size: string[];
  roles: string[]; seniority: string[]; people_per_company: number; exclusions: string[]; reason: string;
};
export type ResearchBrief = { id: number; project_id: number | null; name: string; spec: AudienceSpec; created_at?: string | number };
export type ResearchCompany = {
  id: number; brief_id: number; name: string; domain: string; reason: string; sources: EvidenceSource[];
  match_state: string; disposition?: string | null; warnings: string[]; observed_at?: string | number;
};
export type ResearchJob = {
  id: number; brief_id: number; status: string; completed_tasks: number; total_tasks: number; people_count: number;
  provider_state?: string | null; error?: string | null; created_at?: string | number;
};
export type ContactRecommendation = {
  id: number; brief_id: number; person: { name: string; title?: string; company?: string }; email?: string | null;
  evidence: ContactEvidence; state: RecommendationState; explanation: string; disposition?: string | null; contact_id?: number | null;
};

export type Contact = {
  id: number;
  name?: string | null;
  email: string;
  title?: string | null;
  company?: string | null;
  company_domain?: string | null;
  linkedin_url?: string | null;
  confidence?: string | null;
  department?: string | null;
  pipeline_status?: string | null;
  contact_source?: string | null;
  scrape_source?: string;
  source_url?: string;
  scrape_source_url?: string;
  ai_rejected?: boolean;
  email_verification_status?: string | null;
  evidence?: ContactEvidence | null;
  ai_verdict?: string | null;
  ai_reason?: string | null;
  last_sent_at?: string | null;
  last_send_status?: string | null;
  last_campaign_id?: number | null;
  last_campaign_name?: string | null;
  [key: string]: unknown;
};

export type ContactPage = {
  items: Contact[];
  total: number;
  limit: number;
  offset: number;
};

export type CampaignContact = Contact & {
  id: number;
  contact_id: number;
  campaign_id: number;
  email_subject?: string | null;
  email_body?: string | null;
  status: string;
  last_error?: string | null;
  sent_at?: string | null;
  opened_at?: string | null;
  replied_at?: string | null;
  messages?: Array<{
    id: number;
    sent_at?: string | null;
    events: Array<{ kind: string; occurred_at: string; detail?: string | null }>;
  }>;
};

export type Campaign = {
  id: number;
  name: string;
  status: 'draft' | 'releasing' | 'paused' | 'needs_attention' | 'sent' | string;
  owner_user_id?: number | null;
  sender_user_id?: number | null;
  sequence_id?: number | null;
  contact_count?: number;
  sent_count?: number;
  pending_count?: number;
  failed_count?: number;
  readiness?: { ready: boolean; issues: string[]; unproven_companies?: string[]; mailbox_proof_note?: string };
  counts?: Record<string, number>;
  contacts?: CampaignContact[];
  created_at?: string;
  updated_at?: string;
};

/** Which company a search means: the legal entity, its home country, the
 *  domain its staff use, and the other group entities to leave out. */
export type EntityProfile = {
  legal_name: string;
  display_name: string;
  brand_words: string[];
  hq_country: string | null;
  hq_city: string | null;
  mail_domain: string | null;
  mail_domain_evidence: number;
  alt_mail_domains: { domain: string; country: string | null }[];
  exclude: { name: string; country: string | null; domains: string[]; kind: 'subsidiary' | 'captive' | 'region' }[];
  target_country: string | null;
  source: string;
};

export type ResolvedAlternative = {
  name: string;
  domain: string | null;
  country?: string | null;
  kind?: 'subsidiary' | 'captive' | 'region';
  entity?: EntityProfile;
};

export type ResolvedCompany = {
  name: string;
  domain: string | null;
  domain_verified: boolean;
  linkedin_url: string | null;
  source: 'linkedin' | 'register' | 'club' | 'typed';
  alternatives: ResolvedAlternative[];
  country?: string | null;
  entity?: EntityProfile;
};

export type GeneratedEmail = {
  id: number;
  user_id: number;
  contact_id: number;
  campaign_id?: number | null;
  subject: string;
  body: string;
  created_at: string;
  name?: string | null;
  email?: string | null;
  company?: string | null;
};

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export type DiscoveryLogEntry = {
  email?: string;
  name?: string;
  title?: string;
  contact_source?: string;
  source_url?: string;
  ai_verdict?: string;
  ai_reason?: string;
  ai_source_note?: string;
  discovery_context?: string;
};

export type ScrapeResult = {
  contacts: Contact[];
  count: number;
  found_total?: number;
  duplicates_skipped?: number;
  ai_junk_skipped?: number;
  scrape_run_id?: string;
  discovery_log?: DiscoveryLogEntry[];
  cancelled?: boolean;
};

export type EmailPatternRow = {
  company_domain: string;
  company_name?: string;
  pattern_key: string;
  pattern_template: string;
  confidence: number;
  sample_count: number;
  verified_samples: number;
  failed_samples?: number;
  sources?: string[];
  updated_at?: string;
};

export type EmailPatternRegistry = {
  items: (EmailPatternRow & { member_asserted?: boolean })[];
  total: number;
  limit: number;
  offset: number;
};

export type EmailPrediction = {
  name: string;
  domain: string;
  candidates: string[];
  best: string | null;
  /** learned_pattern = a stored company format produced it; unknown_domain = we declined to guess. */
  basis: 'learned_pattern' | 'generic_fallback' | 'unknown_domain';
  pattern: EmailPatternRow | null;
  patterns_known: number;
};

// Empty = same origin. Local Vite proxies /api → :8000. Hosted box serves SPA + API together.
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

/**
 * Full-page navigations (Google OAuth) must use the real API origin. In dev, `API_BASE` is '' so
 * `fetch` goes through the Vite proxy — but `window.location.href = '/api/auth/google'` hits :5173
 * and can 404 or trigger Firefox OpaqueResponseBlocking on the redirect chain. Use this for OAuth URLs only.
 */
export function getBackendOriginForOAuth(): string {
  const explicit = (import.meta.env.VITE_API_URL || '').trim();
  if (explicit) return explicit.replace(/\/$/, '');
  if (import.meta.env.DEV) return 'http://localhost:8000';
  if (typeof window !== 'undefined') return window.location.origin.replace(/\/$/, '');
  return '';
}

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('yucg_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function isParseFailure(e: unknown): boolean {
  return e instanceof SyntaxError || (e instanceof Error && e.name === 'SyntaxError');
}

/** FastAPI reports a refusal as {"detail": "..."}; keep the reason, not the JSON. */
export function problemDetail(text: string): string {
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed && typeof parsed.detail === 'string') {
      return parsed.detail;
    }
  } catch (error) {
    if (!isParseFailure(error)) throw error;
  }
  return text.replace(/\s+/g, ' ').trim().slice(0, 320) || 'Request failed';
}

export async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
      ...options?.headers,
    },
  });
  const text = await res.text();

  if (!res.ok) {
    let message = `Request failed: ${res.status} ${res.statusText || ''}`.trim();
    if (text.trim()) {
      try {
        const parsed = JSON.parse(text) as {
          detail?: string | { msg?: string }[] | { message?: string; issues?: string[] };
        };
        const detail = parsed.detail;
        message = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? String(detail[0]?.msg || message)
            : detail?.message
              ? [detail.message, ...(detail.issues || [])].join(' ')
              : text.replace(/\s+/g, ' ').trim().slice(0, 320);
      } catch (error) {
        if (!isParseFailure(error)) throw error;
        message = text.replace(/\s+/g, ' ').trim().slice(0, 320) || message;
      }
    }
    if (res.status === 401 && path !== '/api/auth/me' && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('yucg:unauthorized'));
    }
    throw new ApiError(message, res.status);
  }

  if (!text.trim()) {
    throw new Error('Empty response from server (expected JSON).');
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 240);
    throw new Error(`Invalid JSON from server: ${snippet || '(empty)'}`);
  }
}

export const api = {
  projects: {
    list: () => fetchApi<Project[]>('/api/workspace/projects'),
    suggestCitations: (company: string) =>
      fetchApi<CitationSuggestions>(`/api/projects/suggest-citations?company=${encodeURIComponent(company)}`),
  },
  health: () => fetchApi<{ status: string }>('/api/health'),
  ai: {
    models: () =>
      fetchApi<{
        provider: string;
        default: string;
        groups: { id: string; label: string; models: { id: string; label: string; tier: string; blurb: string }[] }[];
      }>('/api/ai/models'),
  },
  telemetry: {
    event: (data: { event_type: string; resource_type?: string; details?: Record<string, unknown> }) =>
      fetchApi<unknown>('/api/telemetry/event', { method: 'POST', body: JSON.stringify(data) }).catch(() => {}),
    batch: (events: { event_type: string; resource_type?: string; details?: Record<string, unknown> }[]) =>
      fetchApi<{ ok: boolean; count: number }>('/api/telemetry/batch', {
        method: 'POST',
        body: JSON.stringify({ events: events.slice(0, 50) }),
      }).catch(() => ({ ok: false, count: 0 })),
  },
  contacts: {
    companiesSummary: (opts?: { q?: string; limit?: number; offset?: number }, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (opts?.q?.trim()) params.set('q', opts.q.trim());
      params.set('limit', String(opts?.limit ?? 200));
      if (opts?.offset) params.set('offset', String(opts.offset));
      return fetchApi<CompanySummaryRow[]>(`/api/contacts/companies/summary?${params}`, { signal });
    },
    list: (opts?: {
      company?: string;
      companies?: string;
      mine_only?: boolean;
      q?: string;
      pipeline_status?: string;
      employee_only?: boolean;
      release_id?: number;
      limit?: number;
      offset?: number;
    }, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (opts?.company) params.set('company', opts.company);
      if (opts?.companies) params.set('companies', opts.companies);
      if (opts?.mine_only) params.set('mine_only', 'true');
      if (opts?.q?.trim()) params.set('q', opts.q.trim());
      if (opts?.pipeline_status) params.set('pipeline_status', opts.pipeline_status);
      if (opts?.employee_only) params.set('employee_only', 'true');
      if (opts?.release_id != null) params.set('release_id', String(opts.release_id));
      if (opts?.limit != null) params.set('limit', String(opts.limit));
      if (opts?.offset != null) params.set('offset', String(opts.offset));
      return fetchApi<ContactPage>(`/api/contacts${params.toString() ? '?' + params : ''}`, { signal });
    },
    get: (id: number) => fetchApi<Contact>(`/api/contacts/${id}`),
    create: (data: Partial<Contact> & { email: string }) =>
      fetchApi<Contact>('/api/contacts', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: number) =>
      fetchApi<{ ok: boolean }>(`/api/contacts/${id}`, { method: 'DELETE' }),
    importFile: (file: File, skipDuplicates = true) => {
      const form = new FormData();
      form.append('file', file);
      return fetch(`${API_BASE}/api/contacts/import?skip_duplicates=${skipDuplicates}`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: form,
      }).then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }) as Promise<{ contacts: Contact[]; count: number; duplicates_skipped?: number }>;
    },
    scrape: (data: { company_name?: string; domain?: string; max_people?: number }) =>
      fetchApi<{ contacts: Contact[]; count: number; duplicates_skipped?: number }>('/api/contacts/scrape', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    /**
     * NDJSON stream: progress events, then complete | cancelled | error.
     */
    scrapeStream: async (
      data: { company_name?: string; domain?: string; max_people?: number },
      onEvent: (ev: Record<string, unknown>) => void,
      opts?: { signal?: AbortSignal }
    ): Promise<ScrapeResult> => {
      let res: Response;
      try {
        res = await fetch(`${API_BASE}/api/contacts/scrape-stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify(data),
          signal: opts?.signal,
        });
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          return { contacts: [], count: 0, duplicates_skipped: 0, cancelled: true };
        }
        throw e;
      }
      if (!res.ok) {
        const text = await res.text();
        try {
          const j = JSON.parse(text);
          const d = j.detail;
          throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
        } catch (e) {
          if (e instanceof Error && e.message !== text) throw e;
          throw new Error(text);
        }
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');
      const dec = new TextDecoder();
      let buffer = '';
      let result: ScrapeResult | null = null;

      const handleLine = (line: string) => {
        if (!line.trim()) return;
        const ev = JSON.parse(line) as Record<string, unknown>;
        onEvent(ev);
        if (ev.type === 'complete') {
          result = {
            contacts: (ev.contacts as Contact[]) || [],
            count: Number(ev.count) || 0,
            found_total: Number(ev.found_total) || Number(ev.count) || 0,
            duplicates_skipped: Number(ev.duplicates_skipped) || 0,
            ai_junk_skipped: Number(ev.ai_junk_skipped) || 0,
            scrape_run_id: ev.scrape_run_id as string | undefined,
            discovery_log: (ev.discovery_log as DiscoveryLogEntry[]) || [],
          };
        }
        if (ev.type === 'cancelled') {
          result = {
            contacts: (ev.contacts as Contact[]) || [],
            count: Number(ev.count) || 0,
            found_total: Number(ev.found_total) || Number(ev.count) || 0,
            duplicates_skipped: Number(ev.duplicates_skipped) || 0,
            cancelled: true,
          };
        }
        if (ev.type === 'error') throw new Error(String(ev.message || 'Scrape failed'));
      };

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += dec.decode(value, { stream: true });
          let nl: number;
          while ((nl = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, nl);
            buffer = buffer.slice(nl + 1);
            handleLine(line);
          }
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          return result ?? { contacts: [], count: 0, duplicates_skipped: 0, cancelled: true };
        }
        throw e;
      }
      buffer += dec.decode();
      const tail = buffer.trim();
      if (tail) handleLine(tail);

      if (!result) throw new Error('Stream ended without a complete result');
      return result;
    },
    bulkDelete: (contact_ids: number[]) =>
      fetchApi<{ deleted: number; skipped: number }>('/api/contacts/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ contact_ids }),
      }),
    clearAll: (data: {
      confirm: boolean;
      domain?: string;
      clear_pattern_cache?: boolean;
      clear_discovery_logs?: boolean;
    }) =>
      fetchApi<{
        contacts_deleted: number;
        patterns_deleted: number;
        discovery_logs_deleted: number;
        domain: string | null;
      }>('/api/contacts/clear-all', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    emailPatterns: (domain: string) =>
      fetchApi<{ domain: string; patterns: EmailPatternRow[]; count: number }>(
        `/api/contacts/email-patterns?domain=${encodeURIComponent(domain)}`
      ),
    /** Browse every company format on record. Omit a domain to list them all. */
    emailPatternRegistry: (params: { q?: string; limit?: number; offset?: number } = {}) => {
      const query = new URLSearchParams();
      if (params.q) query.set('q', params.q);
      if (params.limit != null) query.set('limit', String(params.limit));
      if (params.offset != null) query.set('offset', String(params.offset));
      const suffix = query.toString() ? `?${query}` : '';
      return fetchApi<EmailPatternRegistry>(`/api/contacts/email-patterns${suffix}`);
    },
    predictEmail: (params: { name: string; domain?: string; company?: string }) => {
      const query = new URLSearchParams({ name: params.name });
      if (params.domain) query.set('domain', params.domain);
      if (params.company) query.set('company', params.company);
      return fetchApi<EmailPrediction>(`/api/contacts/predict-email?${query}`);
    },
    assertEmailPattern: (data: { domain: string; pattern_template: string; company_name?: string }) =>
      fetchApi<{ ok: boolean; company_domain: string; pattern_key: string; pattern_template: string }>(
        '/api/contacts/email-patterns',
        { method: 'POST', body: JSON.stringify(data) }
      ),
    reconcileIdentity: (domain?: string) =>
      fetchApi<{ fixed: number; removed: number; unchanged: number }>(
        `/api/contacts/reconcile-identity${domain ? '?domain=' + encodeURIComponent(domain) : ''}`,
        { method: 'POST' }
      ),
    purgeJunkContacts: (domain?: string) =>
      fetchApi<{ removed: number }>(
        `/api/contacts/purge-junk-contacts${domain ? '?domain=' + encodeURIComponent(domain) : ''}`,
        { method: 'POST' }
      ),
    discoveryLog: (scrapeRunId: string) =>
      fetchApi<{ scrape_run_id: string; entries: DiscoveryLogEntry[]; count: number }>(
        `/api/contacts/discovery-log?scrape_run_id=${encodeURIComponent(scrapeRunId)}`
      ),
  },
  emails: {
    generate: (data: {
      contact_id: number;
      tone?: string;
      length?: string;
      angle?: string;
      custom_instructions?: string;
      value_proposition?: string;
      model?: string;
    }) =>
      fetchApi<{ subject: string; body: string; contact_id: number }>('/api/emails/generate', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    testSend: (data: { to_email: string; subject: string; body: string; attachment_ids?: number[] }) =>
      fetchApi<unknown>('/api/emails/test-send', { method: 'POST', body: JSON.stringify(data) }),
    generated: (params?: { contact_id?: number; sort?: string }) =>
      fetchApi<GeneratedEmail[]>(`/api/emails/generated${params && Object.keys(params).length ? '?' + new URLSearchParams(params as Record<string, string>) : ''}`),
    saveDraft: (data: { contact_id: number; subject: string; body: string }) =>
      fetchApi<GeneratedEmail>('/api/emails/generated', { method: 'POST', body: JSON.stringify(data) }),
    updateDraft: (id: number, data: { subject: string; body: string }) =>
      fetchApi<{ ok: boolean; id: number }>(`/api/emails/generated/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    deleteDraft: (id: number) =>
      fetchApi<{ ok: boolean; id: number }>(`/api/emails/generated/${id}`, { method: 'DELETE' }),
    clearGeneratedCache: () =>
      fetchApi<{ ok: boolean; deleted: number }>('/api/emails/generated', { method: 'DELETE' }),
    generateTemplate: (data: {
      name?: string;
      company?: string;
      title?: string;
      email?: string;
      tone?: string;
      length?: string;
      angle?: string;
      custom_instructions?: string;
      value_proposition?: string;
      model?: string;
    }) =>
      fetchApi<{ subject: string; body: string; contact_id?: number | null }>('/api/emails/generate-template', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },
  campaigns: {
    /** One AI draft for a whole group, or one per company when asked. */
    draftTemplate: (data: { companies: string[]; roles?: string; goal: string; proof?: string; length?: string; per_company?: boolean }) =>
      fetchApi<{
        subject?: string;
        body?: string;
        messages?: Record<string, { subject: string; body: string }>;
        grounded?: string[];
      }>('/api/campaigns/draft-template', {
        method: 'POST', body: JSON.stringify(data),
      }),
    /** Render one written message per recipient; preview_only stops before creating. */
    build: (data: { name?: string; companies?: string[]; contact_ids?: number[]; subject?: string; body?: string; messages?: Record<string, { subject: string; body: string }>; preview_only?: boolean }) =>
      fetchApi<{ campaign_id: number; created: number; name: string; recipients?: number; ready?: number; sample?: { subject: string; body: string; email?: string } | null; held: { contact_id?: number; email?: string; name?: string; reason: string }[] }>(
        '/api/campaigns/build', { method: 'POST', body: JSON.stringify(data) }),
    list: () => fetchApi<Campaign[]>('/api/campaigns'),
    get: (id: number) => fetchApi<Campaign>(`/api/campaigns/${id}`),
    delete: (id: number) => fetchApi<{ ok: boolean }>(`/api/campaigns/${id}`, { method: 'DELETE' }),
    create: (name: string) =>
      fetchApi<Campaign>('/api/campaigns', { method: 'POST', body: JSON.stringify({ name }) }),
    addContacts: (id: number, data: { contact_ids: number[]; email_subjects?: Record<string, string>; email_bodies?: Record<string, string> }) =>
      fetchApi<unknown>(`/api/campaigns/${id}/contacts`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    send: (id: number) =>
      fetchApi<{ ok: boolean; sent: number; failed: number; pending_left: number; status: string }>(`/api/campaigns/${id}/send`, { method: 'POST' }),
    release: (id: number) =>
      fetchApi<{ ok: boolean; status: string; counts: Record<string, number>; allowance?: SendAllowance }>(
        `/api/campaigns/${id}/release`,
        { method: 'POST' }
      ),
    pause: (id: number) =>
      fetchApi<{ ok: boolean; status: string }>(`/api/campaigns/${id}/pause`, { method: 'POST' }),
    resume: (id: number) =>
      fetchApi<{ ok: boolean; status: string; counts: Record<string, number> }>(`/api/campaigns/${id}/resume`, { method: 'POST' }),
    retryFailed: (id: number) =>
      fetchApi<{ ok: boolean; status: string; retried: number }>(`/api/campaigns/${id}/retry-failed`, { method: 'POST' }),
    updateContactEmail: (campaignId: number, ccId: number, subject?: string, body?: string) =>
      fetchApi<unknown>(`/api/campaigns/${campaignId}/contact/${ccId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...(subject != null && { subject }),
          ...(body != null && { body }),
        }),
      }),
    removeContact: (campaignId: number, ccId: number) =>
      fetchApi<{ ok: boolean }>(`/api/campaigns/${campaignId}/contact/${ccId}`, { method: 'DELETE' }),
    update: (id: number, data: { sequence_id?: number | null; attachment_ids?: number[] }) =>
      fetchApi<unknown>(`/api/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  },
  analytics: {
    breakdown: () => fetchApi<{
      sections: BreakdownSection[];
      mine?: OutcomeSplit | null;
      club?: OutcomeSplit;
    }>('/api/analytics/breakdown'),
    dashboard: () => fetchApi<{
      contacts_discovered_today: number; emails_in_queue: number; active_campaigns: number;
      total_sent: number; opened: number; open_rate: number; reply_rate: number;
      mine?: OutcomeSplit | null; club?: OutcomeSplit;
      my_sectors?: { sector: string; count: number }[];
      club_sectors?: { sector: string; count: number }[];
    }>('/api/analytics/dashboard'),
    leaderboard: () =>
      fetchApi<{ leaderboard: LeaderboardRow[] }>('/api/analytics/leaderboard'),
    companiesReached: (userId?: number) =>
      fetchApi<{ companies: CompanyReached[] }>(
        `/api/analytics/companies-reached${userId != null ? `?user_id=${userId}` : ''}`
      ),
    campaignMetrics: (id: number) => fetchApi<unknown>(`/api/analytics/campaigns/${id}/metrics`),
    insights: () => fetchApi<{ insights: string[] }>('/api/analytics/insights'),
    dueFollowUps: () => fetchApi<{ count: number }>('/api/analytics/due-follow-ups'),
    timeSeries: (days?: number) =>
      fetchApi<{ labels: string[]; sent: number[]; opened: number[]; replied: number[] }>(
        `/api/analytics/time-series${days != null ? `?days=${days}` : ''}`
      ),
    exportCsv: async () => {
      const res = await fetch(`${API_BASE}/api/analytics/export`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'analytics_export.csv';
      a.click();
      URL.revokeObjectURL(url);
    },
  },
  auth: {
    profile: {
      get: () => fetchApi<MemberProfile>('/api/auth/profile'),
      update: (data: { projects?: string; experience?: string; role_title?: string; linkedin_url?: string; slack_handle?: string; other_handles?: string }) =>
        fetchApi<MemberProfile>('/api/auth/profile', { method: 'PUT', body: JSON.stringify(data) }),
    },
    team: () => fetchApi<Member[]>('/api/auth/team'),
    myProjects: () => fetchApi<Project[]>('/api/auth/my-projects'),
    notificationPrefs: {
      get: () => fetchApi<{ admin_digest: boolean; campaign_summary: boolean }>('/api/auth/notification-preferences'),
      update: (data: { admin_digest?: boolean; campaign_summary?: boolean }) =>
        fetchApi<{ admin_digest: boolean; campaign_summary: boolean }>('/api/auth/notification-preferences', { method: 'PUT', body: JSON.stringify(data) }),
    },
    slack: {
      connectUrl: () => fetchApi<{ redirect_url: string }>('/api/auth/slack/connect'),
      status: () => fetchApi<{ connected: boolean; team_name?: string }>('/api/auth/slack/status'),
      disconnect: () => fetchApi<unknown>('/api/auth/slack/disconnect', { method: 'DELETE' }),
    },
    complete2fa: (code: string) =>
      fetchApi<{ ok: boolean }>('/api/auth/2fa/login', {
        method: 'POST',
        body: JSON.stringify({ code }),
      }),
  },
  admin: {
    catalog: () =>
      fetchApi<{
        bucket: string | null;
        objects: StoredObject[];
        prefixes: { prefix: string; objects: StoredObject[] }[];
      }>('/api/admin/catalog'),
    loginLog: () => fetchApi<LogEntry[]>('/api/admin/login-log'),
    users: {
      list: () => fetchApi<Member[]>('/api/admin/users'),
      invite: (email: string) =>
        fetchApi<unknown>('/api/admin/users/invite', { method: 'POST', body: JSON.stringify({ email }) }),
      updateRole: (userId: number, role: string) =>
        fetchApi<unknown>(`/api/admin/users/${userId}/role`, { method: 'PATCH', body: JSON.stringify({ role }) }),
      updateStatus: (userId: number, isActive: boolean) =>
        fetchApi<unknown>(`/api/admin/users/${userId}/status`, { method: 'PATCH', body: JSON.stringify({ is_active: isActive }) }),
      exportExcel: async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/export`, { headers: getAuthHeaders() });
        if (!res.ok) {
          const text = await res.text();
          try {
            const j = JSON.parse(text);
            const d = j.detail;
            throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
          } catch (e) {
            if (e instanceof Error && e.message !== text) throw e;
            throw new Error(text);
          }
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'YUCG_users_export.xlsx';
        a.click();
        URL.revokeObjectURL(url);
      },
    },
    auditLog: (limit?: number) =>
      fetchApi<LogEntry[]>(`/api/admin/audit-log?limit=${limit || 100}`),
    exportAuditLogExcel: async () => {
      const res = await fetch(`${API_BASE}/api/admin/audit-log/export`, { headers: getAuthHeaders() });
      if (!res.ok) {
        const text = await res.text();
        try {
          const j = JSON.parse(text);
          const d = j.detail;
          throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
        } catch (e) {
          if (e instanceof Error && e.message !== text) throw e;
          throw new Error(text);
        }
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'YUCG_audit_log.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    },
    exportAllZip: async () => {
      const res = await fetch(`${API_BASE}/api/admin/export/all`, { headers: getAuthHeaders() });
      if (!res.ok) {
        const text = await res.text();
        try {
          const j = JSON.parse(text);
          const d = j.detail;
          throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
        } catch (e) {
          if (e instanceof Error && e.message !== text) throw e;
          throw new Error(text);
        }
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'YUCG_admin_export_all.zip';
      a.click();
      URL.revokeObjectURL(url);
    },
    apiKeys: {
      list: () => fetchApi<ApiKey[]>('/api/admin/api-keys'),
      create: (name: string, scopes?: string) =>
        fetchApi<{ key: string }>('/api/admin/api-keys', { method: 'POST', body: JSON.stringify({ name, scopes }) }),
      revoke: (id: number) =>
        fetchApi<unknown>(`/api/admin/api-keys/${id}`, { method: 'DELETE' }),
    },
    twoFactor: {
      status: () => fetchApi<{ status: 'enabled' | 'pending' | 'not_setup' }>('/api/admin/2fa/status'),
      setup: () => fetchApi<{ provisioning_uri: string; secret: string }>('/api/admin/2fa/setup', { method: 'POST' }),
      verify: (code: string) =>
        fetchApi<unknown>('/api/admin/2fa/verify', { method: 'POST', body: JSON.stringify({ code }) }),
      disable: (code: string) =>
        fetchApi<unknown>('/api/admin/2fa/disable', { method: 'POST', body: JSON.stringify({ code }) }),
      reset: () => fetchApi<unknown>('/api/admin/2fa/reset', { method: 'POST' }),
    },
    projects: {
      list: () => fetchApi<Project[]>('/api/admin/projects'),
      create: (data: { name: string; semester?: string; description?: string; client_name?: string; discussable?: boolean }) =>
        fetchApi<Project>('/api/admin/projects', { method: 'POST', body: JSON.stringify(data) }),
      update: (id: number, data: { name?: string; semester?: string; description?: string; client_name?: string; discussable?: boolean }) =>
        fetchApi<Project>(`/api/admin/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
      delete: (id: number) => fetchApi<unknown>(`/api/admin/projects/${id}`, { method: 'DELETE' }),
      assignments: (projectId: number) => fetchApi<(Member & { user_id: number; role_in_project?: string })[]>(`/api/admin/projects/${projectId}/assignments`),
      assignUser: (userId: number, data: { project_id: number; role_in_project?: string }) =>
        fetchApi<unknown>(`/api/admin/users/${userId}/project`, { method: 'PUT', body: JSON.stringify(data) }),
      unassignUser: (userId: number, projectId: number) =>
        fetchApi<unknown>(`/api/admin/users/${userId}/project/${projectId}`, { method: 'DELETE' }),
      userProjects: (userId: number) => fetchApi<Project[]>(`/api/admin/users/${userId}/projects`),
    },
    operations: {
      events: (params?: { limit?: number; event_type?: string; days?: number }) =>
        fetchApi<LogEntry[]>(`/api/admin/operations/events?${new URLSearchParams(Object.entries(params || {}).map(([key, value]) => [key, String(value)]))}`),
      heatmap: (params?: { days?: number; group_by?: string }) =>
        fetchApi<{
          group_by: string;
          days: number;
          grid: Record<string, Record<string, number>>;
          rows: Record<string, unknown>[];
          matrix_2d?: { row_labels: string[]; col_labels: string[]; values: number[][] };
        }>(`/api/admin/operations/heatmap?${new URLSearchParams(Object.entries(params || {}).map(([key, value]) => [key, String(value)]))}`),
      aggregates: (days?: number) =>
        fetchApi<{ by_event_type: { event_type: string; count: number }[]; by_resource_type: { resource_type: string; count: number }[]; days: number }>(
          `/api/admin/operations/aggregates?days=${days ?? 30}`
        ),
      resources: {
        list: () => fetchApi<{ id: number; name: string; content_text: string; content_type?: string; content_length?: number }[]>('/api/admin/operations/resources'),
        create: (data: { name: string; content_text: string; content_type?: string }) =>
          fetchApi<unknown>('/api/admin/operations/resources', { method: 'POST', body: JSON.stringify(data) }),
        upload: async (file: File) => {
          const form = new FormData();
          form.append('file', file);
          const res = await fetch(`${API_BASE}/api/admin/operations/resources/upload`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: form,
          });
          if (!res.ok) throw new Error(await res.text());
          return res.json();
        },
      },
      exportInsightsExcel: async (days?: number) => {
        const res = await fetch(
          `${API_BASE}/api/admin/operations/export/insights?days=${days ?? 30}`,
          { headers: getAuthHeaders() }
        );
        if (!res.ok) {
          const text = await res.text();
          try {
            const j = JSON.parse(text);
            const d = j.detail;
            throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
          } catch (e) {
            if (e instanceof Error && e.message !== text) throw e;
            throw new Error(text);
          }
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `YUCG_operations_insights_${days ?? 30}d.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      },
      exportChartsZip: async (days?: number) => {
        const res = await fetch(
          `${API_BASE}/api/admin/operations/export/charts?days=${days ?? 30}`,
          { headers: getAuthHeaders() }
        );
        if (!res.ok) {
          const text = await res.text();
          try {
            const j = JSON.parse(text);
            const d = j.detail;
            throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
          } catch (e) {
            if (e instanceof Error && e.message !== text) throw e;
            throw new Error(text);
          }
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `YUCG_operations_charts_${days ?? 30}d.zip`;
        a.click();
        URL.revokeObjectURL(url);
      },
      exportFullZip: async (days?: number) => {
        const res = await fetch(
          `${API_BASE}/api/admin/operations/export/full?days=${days ?? 30}`,
          { headers: getAuthHeaders() }
        );
        if (!res.ok) {
          const text = await res.text();
          try {
            const j = JSON.parse(text);
            const d = j.detail;
            throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
          } catch (e) {
            if (e instanceof Error && e.message !== text) throw e;
            throw new Error(text);
          }
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `YUCG_operations_full_export_${days ?? 30}d.zip`;
        a.click();
        URL.revokeObjectURL(url);
      },
      firecrawlStatus: () => fetchApi<FirecrawlStatus>('/api/admin/operations/firecrawl-status'),
    },
  },
  outreach: {
    updatePipeline: (contactId: number, status: string) =>
      fetchApi<unknown>(`/api/outreach/contacts/${contactId}/pipeline`, {
        method: 'PATCH',
        body: JSON.stringify({ pipeline_status: status }),
      }),
    notes: {
      list: (contactId: number) => fetchApi<ContactNote[]>(`/api/outreach/contacts/${contactId}/notes`),
      create: (contactId: number, note: string) =>
        fetchApi<unknown>('/api/outreach/notes', {
          method: 'POST',
          body: JSON.stringify({ contact_id: contactId, note }),
        }),
    },
    activities: {
      list: (contactId: number) => fetchApi<ContactActivity[]>(`/api/outreach/contacts/${contactId}/activities`),
      create: (contactId: number, type: string, details?: string) =>
        fetchApi<unknown>('/api/outreach/activities', {
          method: 'POST',
          body: JSON.stringify({ contact_id: contactId, activity_type: type, details }),
        }),
    },
    templates: {
      list: (industry?: string) =>
        fetchApi<Template[]>(industry ? `/api/outreach/templates?industry=${encodeURIComponent(industry)}` : '/api/outreach/templates'),
      create: (data: { name: string; subject: string; body: string; industry?: string; use_case?: string }) =>
        fetchApi<unknown>('/api/outreach/templates', { method: 'POST', body: JSON.stringify(data) }),
      delete: (id: number) => fetchApi<unknown>(`/api/outreach/templates/${id}`, { method: 'DELETE' }),
    },
    followUps: {
      schedule: () => fetchApi<{ scheduled: FollowUpScheduleRow[]; stopped: FollowUpScheduleRow[] }>(
        '/api/outreach/follow-ups/schedule'),
    },
    sequences: {
      list: () => fetchApi<Sequence[]>('/api/outreach/sequences'),
      create: (name: string, steps: { days_after: number; subject: string; body: string }[]) =>
        fetchApi<unknown>('/api/outreach/sequences', {
          method: 'POST',
          body: JSON.stringify({ name, steps }),
        }),
    },
    profile: {
      get: (contactId: number) => fetchApi<ContactProfile>(`/api/outreach/contacts/${contactId}/profile`),
      refresh: (contactId: number) =>
        fetchApi<ContactProfile>(`/api/outreach/contacts/${contactId}/profile/refresh`, { method: 'POST' }),
    },
    sentiment: {
      analyze: (data: { subject: string; body: string; industry?: string; target_role?: string }) =>
        fetchApi<Sentiment>('/api/outreach/sentiment/analyze', { method: 'POST', body: JSON.stringify(data) }),
    },
    markReplied: (ccId: number) =>
      fetchApi<unknown>(`/api/outreach/campaign-contacts/${ccId}/mark-replied`, { method: 'POST' }),
    /** Gmail inbox scan: mark campaign replies and optionally cold → contacted. Requires gmail.readonly (re-auth if needed). */
    syncStatus: () => fetchApi<{ last_success_at?: string; error?: string; in_progress?: boolean }>('/api/outreach/sync-status'),
    syncInboxReplies: (autoSortContacted = true) =>
      fetchApi<{ ok: boolean; error?: string; in_progress?: boolean; marked_replied?: number; promoted?: number; message?: string; errors?: string[]; pipeline_promoted_contacted?: number }>(`/api/outreach/sync-inbox-replies?auto_sort_contacted=${autoSortContacted ? 'true' : 'false'}`, {
        method: 'POST',
      }),
    /** Promote cold → contacted from sent campaign rows (no Gmail). */
    autoSortPipeline: () =>
      fetchApi<{ ok: boolean; promoted: number }>('/api/outreach/auto-sort-pipeline', { method: 'POST' }),
    verifyEmail: (email: string) =>
      fetchApi<{ valid: boolean }>(`/api/outreach/verify-email?email=${encodeURIComponent(email)}`),
    pipelineMetrics: () => fetchApi<PipelineMetrics>('/api/outreach/metrics/pipeline'),
    campaigns: {
      list: () => fetchApi<Worklist[]>('/api/outreach/campaigns'),
      get: (id: number) => fetchApi<Worklist>(`/api/outreach/campaigns/${id}`),
      create: (data: { name: string; type: 'community' | 'individual'; description?: string; priority?: number }) =>
        fetchApi<Worklist>('/api/outreach/campaigns', { method: 'POST', body: JSON.stringify(data) }),
      addContacts: (id: number, contactIds: number[]) =>
        fetchApi<unknown>(`/api/outreach/campaigns/${id}/contacts`, {
          method: 'POST',
          body: JSON.stringify({ contact_ids: contactIds }),
        }),
      removeContact: (campaignId: number, contactId: number) =>
        fetchApi<unknown>(`/api/outreach/campaigns/${campaignId}/contacts/${contactId}`, { method: 'DELETE' }),
      delete: (id: number) =>
        fetchApi<unknown>(`/api/outreach/campaigns/${id}`, { method: 'DELETE' }),
    },
    sendTiming: (industry?: string) =>
      fetchApi<unknown>(industry ? `/api/outreach/send-timing?industry=${encodeURIComponent(industry)}` : '/api/outreach/send-timing'),
  },
  attachments: {
    list: () => fetchApi<Attachment[]>('/api/attachments'),
    upload: (file: File, displayName?: string) => {
      const form = new FormData();
      form.append('file', file);
      if (displayName) form.append('display_name', displayName);
      return fetch(`${API_BASE}/api/attachments`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: form,
      }).then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }) as Promise<{ id: number; filename: string; display_name?: string; file_size: number }>;
    },
    delete: (id: number) =>
      fetchApi<unknown>(`/api/attachments/${id}`, { method: 'DELETE' }),
    downloadUrl: (id: number) => `${API_BASE}/api/attachments/${id}/download`,
    onedrive: {
      list: () => fetchApi<{ configured: boolean; items: OneDriveItem[] }>('/api/attachments/onedrive'),
      attach: (item_id: string) =>
        fetchApi<unknown>('/api/attachments/onedrive/attach', {
          method: 'POST',
          body: JSON.stringify({ item_id }),
        }),
    },
  },
  yucg: {
    listReleases: () => fetchApi<Release[]>('/api/yucg/releases'),
    getRelease: (id: number) => fetchApi<Release>(`/api/yucg/releases/${id}`),
    createRelease: (data: { name: string; register_ids?: number[]; notes?: string }) =>
      fetchApi<{ id: number; status: string; targets: number }>('/api/yucg/releases', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    mintPerson: (
      releaseId: number,
      targetId: number,
      data: { full_name: string; title?: string; source_url?: string; blurb?: string },
    ) =>
      fetchApi<unknown>(`/api/yucg/releases/${releaseId}/targets/${targetId}/mint`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    keepPerson: (releaseId: number, personId: number, keep = true) =>
      fetchApi<unknown>(`/api/yucg/releases/${releaseId}/people/${personId}/keep`, {
        method: 'POST',
        body: JSON.stringify({ keep }),
      }),
    rebuildPack: (releaseId: number) =>
      fetchApi<unknown>(`/api/yucg/releases/${releaseId}/pack`, { method: 'POST' }),
    releaseInbox: (releaseId: number) => fetchApi<InboxItem[]>(`/api/yucg/releases/${releaseId}/inbox`),
  },
  yucgoutreach: {
    register: (params: { q?: string; tier?: string; country?: string; sector?: string; min_amount?: number; with_officers?: boolean; limit?: number; offset?: number }, signal?: AbortSignal) => {
      const search = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== '') search.set(key, String(value)); });
      return fetchApi<{ items: RegisterCompany[]; total: number; limit: number; offset: number }>(`/api/yucgoutreach/register?${search.toString()}`, signal ? { signal } : undefined);
    },
    registerSummary: () => fetchApi<RegisterSummary>('/api/yucgoutreach/register/summary'),
    fetchRegisterPeople: (id: number) => fetchApi<{ ok: boolean; attached?: number; officer_count?: number; cached?: boolean; note?: string; error?: string }>(`/api/yucgoutreach/register/${id}/people`, { method: 'POST' }),
    registerPeople: (id: number) => fetchApi<{ full_name: string; relationship?: string | null; observed_at?: string | null; source_url?: string | null }[]>(`/api/yucgoutreach/register/${id}/people`),
    roleSuggestions: (params: { company: string; domain?: string; hints?: string }, signal?: AbortSignal) => {
      const q = new URLSearchParams({ company: params.company });
      if (params.domain) q.set('domain', params.domain);
      if (params.hints) q.set('hints', params.hints);
      return fetchApi<RoleSuggestions>(`/api/yucgoutreach/role-suggestions?${q.toString()}`, signal ? { signal } : undefined);
    },
    /** A typed name or a pasted LinkedIn company page, resolved to one
     *  company and - only when it can be checked - its website. */
    resolveCompany: (q: string, signal?: AbortSignal) =>
      fetchApi<ResolvedCompany>(
        `/api/yucgoutreach/resolve-company?q=${encodeURIComponent(q)}`, signal ? { signal } : undefined
      ),
    domainGuess: (company: string, signal?: AbortSignal) =>
      fetchApi<{ domain: string | null; verified: boolean }>(
        `/api/yucgoutreach/domain-guess?company=${encodeURIComponent(company)}`, signal ? { signal } : undefined
      ),
    listRosters: (q: string, limit = 10) =>
      fetchApi<{ rosters: Record<string, unknown>[] }>(
        `/api/yucg/rosters?q=${encodeURIComponent(q)}&limit=${limit}`
      ),
    rosterStats: () => fetchApi<{ sources: Record<string, unknown>[] }>('/api/yucg/rosters/stats'),
    createRun: (data: {
      company_name: string;
      company_domain?: string;
      title_hints?: string;
      max_prospects?: number;
      worker_concurrency?: number;
      entity?: EntityProfile;
    }) =>
      fetchApi<{ id: number; status: string }>('/api/yucgoutreach/runs', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    listRuns: (limit?: number) =>
      fetchApi<DiscoveryRun[]>(`/api/yucgoutreach/runs?limit=${limit ?? 50}`),
    getRun: (id: number) => fetchApi<DiscoveryRun>(`/api/yucgoutreach/runs/${id}`),
    listProspects: (runId: number, limit = 800) =>
      fetchApi<DiscoveryProspect[]>(`/api/yucgoutreach/runs/${runId}/prospects?limit=${limit}`),
    /** Bring a run's people on file. With ids, exactly those people; without,
     *  everyone eligible. Each outcome names the prospect so the picker can
     *  move only the rows that actually landed. */
    importContacts: (runId: number, prospectIds?: number[]) =>
      fetchApi<ImportOutcomes>(
        `/api/yucgoutreach/runs/${runId}/import-contacts`,
        { method: 'POST', body: prospectIds ? JSON.stringify({ prospect_ids: prospectIds }) : undefined },
      ),
    deleteRun: (id: number) =>
      fetchApi<{ ok: boolean; deleted: number }>(`/api/yucgoutreach/runs/${id}`, { method: 'DELETE' }),
    exportExcel: async (runId: number) => {
      const res = await fetch(`${API_BASE}/api/yucgoutreach/runs/${runId}/export.xlsx`, { headers: getAuthHeaders() });
      if (!res.ok) {
        const text = await res.text();
        try {
          const j = JSON.parse(text);
          const d = j.detail;
          throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d[0]?.msg : text);
        } catch (e) {
          if (e instanceof Error && e.message !== text) throw e;
          throw new Error(text);
        }
      }
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition');
      const match = cd?.match(/filename="([^"]+)"/);
      const filename = match?.[1] || `YUCGoutreach_export_${runId}.xlsx`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },
  },
  settings: {
    get: () => fetchApi<Settings>('/api/settings'),
    update: (data: {
      sign_off_name?: string;
      sign_off_pronouns?: string;
      sign_off_role?: string;
      sign_off_organization?: string;
      sign_off_linkedin?: string;
      sign_off_phone?: string;
      sign_off_logo_url?: string;
      daily_send_limit?: number;
      daily_send_warn_at?: number;
    }) =>
      fetchApi<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(data) }),
    customFormats: {
      list: () => fetchApi<CustomFormat[]>('/api/settings/custom-formats'),
      add: (data: { name: string; pattern: string; priority?: number }) =>
        fetchApi<unknown>('/api/settings/custom-formats', { method: 'POST', body: JSON.stringify(data) }),
      delete: (id: number) =>
        fetchApi<unknown>(`/api/settings/custom-formats/${id}`, { method: 'DELETE' }),
    },
  },
};
