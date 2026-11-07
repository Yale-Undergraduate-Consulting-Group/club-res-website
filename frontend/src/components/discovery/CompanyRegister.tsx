import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type RegisterCompany, type RegisterPerson, type RegisterRecords, type RegisterSummary } from '../../api';
import { publicSourceUrl } from '../../lib/contactEvidence';
import { kindLabel, locationText, peopleText, summaryLine } from '../../lib/registerView';

const TIERS: { id: string; label: string; hint: string }[] = [
  { id: '', label: 'All', hint: 'Every company on record' },
  { id: 'us_nonprofit', label: 'Nonprofits', hint: 'Larger US nonprofits' },
  { id: 'us_employer', label: 'US employers', hint: 'US companies with a staff benefit plan' },
  { id: 'us_private', label: 'Recently funded', hint: 'US companies that recently raised money privately' },
  { id: 'us_public', label: 'US listed', hint: 'Companies listed on a US stock exchange' },
  { id: 'uk', label: 'UK', hint: 'Larger active UK companies' },
];

const PAGE = 40;

/**
 * Browse the company register - one entry per company under its public name,
 * with the filings behind it in a log - and start Find people on any of them.
 * This is the pool the club picks targets from; no paid provider feeds it.
 */
export default function CompanyRegister() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [tier, setTier] = useState('');
  const [sector, setSector] = useState('');
  const [withOfficers, setWithOfficers] = useState(false);
  // Browsing without a query: biggest/most reachable companies first, or the
  // newest-filing feed. A typed query always ranks by match, then size.
  const [sort, setSort] = useState<'value' | 'recent'>('value');
  const [items, setItems] = useState<RegisterCompany[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<RegisterSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [people, setPeople] = useState<Record<string, RegisterPerson[]>>({});
  const [showPeople, setShowPeople] = useState<Record<string, boolean>>({});
  const [logs, setLogs] = useState<Record<string, RegisterRecords | null>>({});
  const [showLog, setShowLog] = useState<Record<string, boolean>>({});
  const [fetching, setFetching] = useState<Record<string, boolean>>({});
  // Companies picked here become a target list, which is what the rest of the
  // app already works from. Selection survives filter and tier changes, so a
  // list can be assembled from several searches rather than one.
  const [picked, setPicked] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    api.yucgoutreach.registerSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const filters = useCallback(() => ({
    q: q.trim() || undefined,
    tier: tier || undefined,
    sector: sector || undefined,
    with_officers: withOfficers || undefined,
    sort,
    limit: PAGE,
  }), [q, tier, sector, withOfficers, sort]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const page = await api.yucgoutreach.register(filters(), controller.signal);
        setItems(Array.isArray(page?.items) ? page.items : []);
        setTotal(Number.isFinite(page?.total) ? page.total : 0);
        setError('');
      } catch (e) {
        if (!(e instanceof DOMException && e.name === 'AbortError')) {
          setError(e instanceof Error ? e.message : 'Could not load the register');
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 300);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [filters]);

  const showMore = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.yucgoutreach.register({ ...filters(), offset: items.length });
      const more = Array.isArray(page?.items) ? page.items : [];
      setItems((current) => {
        const seen = new Set(current.map((row) => row.entity_key));
        return [...current, ...more.filter((row) => !seen.has(row.entity_key))];
      });
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load more companies');
    } finally {
      setLoading(false);
    }
  }, [filters, items.length]);

  const togglePeople = useCallback(async (key: string) => {
    setShowPeople((current) => ({ ...current, [key]: !current[key] }));
    if (people[key]) return;
    try {
      const rows = await api.yucgoutreach.registerPeople(key);
      setPeople((current) => ({ ...current, [key]: rows }));
    } catch {
      setPeople((current) => ({ ...current, [key]: [] }));
    }
  }, [people]);

  const toggleLog = useCallback(async (key: string) => {
    setShowLog((current) => ({ ...current, [key]: !current[key] }));
    if (key in logs) return;
    try {
      const log = await api.yucgoutreach.registerRecords(key);
      setLogs((current) => ({ ...current, [key]: log }));
    } catch {
      setLogs((current) => ({ ...current, [key]: null }));
    }
  }, [logs]);

  // Listed and UK companies arrive with no people: neither bulk file carries
  // them, and both registers publish them per company instead. Fetching is a
  // request against a rate-limited public API, so it happens when a member
  // asks for one company, never as a sweep.
  const fetchPeople = useCallback(async (item: RegisterCompany) => {
    if (!item.lookup_id) return;
    const key = item.entity_key;
    setFetching((current) => ({ ...current, [key]: true }));
    try {
      const result = await api.yucgoutreach.fetchRegisterPeople(item.lookup_id);
      if (!result.ok) {
        setError(result.error || 'Could not read that register.');
        return;
      }
      setError('');
      const rows = await api.yucgoutreach.registerPeople(key);
      setPeople((current) => ({ ...current, [key]: rows }));
      setShowPeople((current) => ({ ...current, [key]: true }));
      setItems((current) => current.map((row) =>
        row.entity_key === key ? { ...row, officer_count: rows.length, lookup_id: null } : row));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that register.');
    } finally {
      setFetching((current) => ({ ...current, [key]: false }));
    }
  }, []);

  // One action, and it starts the work rather than filing it. This used to
  // create a "target list" on another page, an object nothing else in the
  // club had ever used - the table was empty in production. The companies now
  // go straight into the campaign pipeline, which is where choosing them
  // leads anyway.
  const workThese = useCallback(() => {
    if (picked.size === 0) return;
    const companies = [...picked.values()].join(',');
    setPicked(new Map());
    navigate(`/scraper?view=company&companies=${encodeURIComponent(companies)}`);
  }, [picked, navigate]);

  return (
    <div className="space-y-5" data-section="company-register">
      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm space-y-3">
        <h2 className="text-lg font-semibold text-deep-navy">Company register</h2>
        {summary && Array.isArray(summary.tiers) && (
          <p className="text-[13px] text-slate-500">
            {summaryLine(summary)}
            {summary.pending > 0 && ` · ${summary.pending.toLocaleString()} records still being sorted`}
          </p>
        )}
      </div>

      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm space-y-4">
        <div className="flex flex-wrap gap-2">
          {TIERS.map((option) => (
            <button
              key={option.id || 'all'}
              type="button"
              title={option.hint}
              onClick={() => setTier(option.id)}
              className={`rounded-full border px-3 py-1.5 text-xs font-medium ${
                tier === option.id ? 'border-deep-navy bg-deep-navy text-white' : 'border-pale-sky bg-white text-deep-navy'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1" htmlFor="register-q">Company or sector</label>
            <input
              id="register-q"
              className="w-full rounded-lg border border-pale-sky px-3 py-2 text-sm"
              value={q}
              onChange={(event) => setQ(event.target.value)}
              placeholder="health, robotics, Acme…"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1" htmlFor="register-sector">Sector</label>
            <select
              id="register-sector"
              className="w-full rounded-lg border border-pale-sky px-3 py-2 text-sm bg-white"
              value={sector}
              onChange={(event) => setSector(event.target.value)}
            >
              <option value="">Any sector</option>
              {(summary?.sectors || []).map((row) => (
                <option key={row.sector} value={row.sector}>{row.sector} ({row.n})</option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={withOfficers} onChange={(event) => setWithOfficers(event.target.checked)} />
            Only companies with named people
          </label>
          {!q.trim() && (
            <div className="flex items-center gap-1 text-xs" role="group" aria-label="Order companies by" data-testid="register-sort">
              <span className="text-slate-500 mr-1">Order:</span>
              {([['value', 'Worth reaching first'], ['recent', 'Newest filings']] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={sort === id}
                  onClick={() => setSort(id)}
                  className={`rounded-full border px-3 py-1 font-medium ${
                    sort === id ? 'border-deep-navy bg-deep-navy text-white' : 'border-pale-sky bg-white text-deep-navy'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      {picked.size > 0 && (
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-3 rounded-2xl border border-deep-navy bg-white px-4 py-3 shadow-lg">
          <span className="text-sm font-semibold text-deep-navy">
            {picked.size} compan{picked.size === 1 ? 'y' : 'ies'} selected
          </span>
          <button
            type="button"
            className="rounded-xl bg-deep-navy px-4 py-2 text-sm font-semibold text-white"
            onClick={workThese}
          >
            Find people and write to them
          </button>
          <button
            type="button"
            className="text-sm font-medium text-slate-600 hover:underline"
            onClick={() => setPicked(new Map())}
          >
            Clear selection
          </button>
        </div>
      )}

      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="font-semibold text-deep-navy">{total.toLocaleString()} match{total === 1 ? '' : 'es'}</h3>
          {loading && <span className="text-xs text-slate-500">Loading…</span>}
        </div>
        {items.length === 0 && !loading && (
          <p className="text-sm text-slate-500">Nothing matches. Try another name, sector or tab.</p>
        )}
        <ul className="divide-y divide-[var(--border)]">
          {items.map((item) => {
            const key = item.entity_key;
            const log = logs[key];
            return (
              <li key={key} className="py-3" data-company={item.display_name}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0"
                      aria-label={`Select ${item.display_name}`}
                      checked={picked.has(key)}
                      onChange={() => setPicked((current) => {
                        const next = new Map(current);
                        if (next.has(key)) next.delete(key);
                        else next.set(key, item.display_name);
                        return next;
                      })}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-medium text-deep-navy">{item.display_name}</span>
                        {item.kinds.map((kind) => (
                          <span key={kind} className="rounded-full bg-pale-sky/60 px-2 py-0.5 text-[11px] font-medium text-deep-navy">
                            {kindLabel(kind)}
                          </span>
                        ))}
                        {item.claimed_by && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-900">
                            {item.claimed_by} is on this
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                        <span>{locationText(item.country, item.locations)}</span>
                        {item.officer_count > 0 && (
                          <button
                            type="button"
                            className="font-medium text-steel-blue hover:underline"
                            aria-expanded={!!showPeople[key]}
                            onClick={() => void togglePeople(key)}
                          >
                            {peopleText(item.officer_count)}
                          </button>
                        )}
                        {item.lookup_id ? (
                          <button
                            type="button"
                            className="font-semibold text-steel-blue hover:underline disabled:opacity-50"
                            disabled={!!fetching[key]}
                            onClick={() => void fetchPeople(item)}
                          >
                            {fetching[key] ? 'Reading the register…' : 'Look up officers'}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="font-medium text-steel-blue hover:underline"
                          aria-expanded={!!showLog[key]}
                          onClick={() => void toggleLog(key)}
                        >
                          {item.record_count} record{item.record_count === 1 ? '' : 's'}
                        </button>
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="shrink-0 rounded-xl border border-deep-navy px-3 py-1.5 text-xs font-semibold text-deep-navy hover:bg-pale-sky/30"
                    onClick={() => {
                      // Carry the domain the register already holds. Without it the
                      // search re-derives a domain it was just handed, and can
                      // resolve a different one for a common company name.
                      const query = new URLSearchParams({ view: 'company', company: item.display_name });
                      if (item.company_domain) query.set('domain', item.company_domain);
                      navigate(`/scraper?${query.toString()}`);
                    }}
                  >
                    Find people here
                  </button>
                </div>
                {showPeople[key] && (
                  <div className="mt-2 ml-7 text-xs text-slate-600" data-testid="register-people">
                    {!people[key]
                      ? 'Loading…'
                      : people[key].length === 0
                        ? 'None recorded.'
                        : people[key].map((person) => `${person.full_name}${person.relationship ? ` (${person.relationship})` : ''}`).join(', ')}
                  </div>
                )}
                {showLog[key] && (
                  <div className="mt-2 ml-7" data-testid="register-log">
                    {!(key in logs) && <p className="text-xs text-slate-500">Loading…</p>}
                    {log === null && <p className="text-xs text-red-700">Could not load the records.</p>}
                    {log && (
                      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] text-xs text-slate-600">
                        {log.records.map((record) => {
                          const href = record.url ? publicSourceUrl(record.url) : null;
                          return (
                            <li key={record.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-1.5" data-testid="register-record">
                              <span className="font-medium text-deep-navy">{record.source_label}</span>
                              {record.date && <span>{record.date}</span>}
                              {record.region && <span>{record.region}</span>}
                              <span>{record.legal_name}</span>
                              {record.detail.length > 0 && <span>{record.detail.join(' · ')}</span>}
                              {href && (
                                <a className="font-medium text-steel-blue hover:underline" href={href} target="_blank" rel="noopener noreferrer">
                                  View
                                </a>
                              )}
                            </li>
                          );
                        })}
                        {log.total > log.records.length && (
                          <li className="px-3 py-1.5 text-slate-500">Showing the latest {log.records.length} of {log.total.toLocaleString()} records.</li>
                        )}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {items.length < total && (
          <div className="mt-3 text-center">
            <button
              type="button"
              className="rounded-xl border border-pale-sky px-4 py-2 text-sm font-medium text-deep-navy disabled:opacity-50"
              disabled={loading}
              onClick={() => void showMore()}
            >
              Show more companies
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
