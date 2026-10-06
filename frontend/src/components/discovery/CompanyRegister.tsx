import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type RegisterCompany, type RegisterSummary } from '../../api';

const TIERS: { id: string; label: string; hint: string }[] = [
  { id: '', label: 'All', hint: 'Everything on record' },
  { id: 'us_nonprofit', label: 'Nonprofits that buy advice', hint: 'US nonprofits with $5M+ revenue that already pay outside firms for management, legal or accounting work (IRS Form 990), with the officers they named on the same return' },
  { id: 'us_employer', label: 'US employers', hint: 'US companies that file a benefit plan for their own staff (DOL Form 5500), with the headcount they reported' },
  { id: 'us_private', label: 'Recently funded', hint: 'US companies that filed a Reg D raise — the startup pool' },
  { id: 'us_public', label: 'US listed', hint: 'Every SEC-registered public company' },
  { id: 'uk', label: 'UK', hint: 'Companies House: active companies above the small-company accounts thresholds' },
];

function money(value?: number | null): string {
  if (!value) return '';
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `$${Math.round(value / 1_000_000)}M`;
  return `$${Math.round(value / 1000)}k`;
}

/**
 * Browse the bulk public register (SEC listed, SEC Form D filers, Companies
 * House) and start Find people on any row. This is the pool the club picks
 * targets from; no paid provider feeds it.
 */
export default function CompanyRegister() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [tier, setTier] = useState('');
  const [sector, setSector] = useState('');
  const [withOfficers, setWithOfficers] = useState(false);
  const [items, setItems] = useState<RegisterCompany[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<RegisterSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [people, setPeople] = useState<Record<number, { full_name: string; relationship?: string | null; source_url?: string | null }[]>>({});
  const [fetching, setFetching] = useState<Record<number, boolean>>({});
  // Companies picked here become a target list, which is what the rest of the
  // app already works from. Selection survives filter and tier changes, so a
  // list can be assembled from several searches rather than one.
  const [picked, setPicked] = useState<Map<number, string>>(new Map());

  useEffect(() => {
    api.yucgoutreach.registerSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const page = await api.yucgoutreach.register({
          q: q.trim() || undefined,
          tier: tier || undefined,
          sector: sector || undefined,
          with_officers: withOfficers || undefined,
          limit: 40,
        }, controller.signal);
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
  }, [q, tier, sector, withOfficers]);

  const loadPeople = useCallback(async (id: number) => {
    if (people[id]) return;
    try {
      const rows = await api.yucgoutreach.registerPeople(id);
      setPeople((current) => ({ ...current, [id]: rows }));
    } catch {
      setPeople((current) => ({ ...current, [id]: [] }));
    }
  }, [people]);

  // Listed and UK companies arrive with no people: neither bulk file carries
  // them, and both registers publish them per company instead. Fetching is a
  // request against a rate-limited public API, so it happens when a member
  // asks for one company, never as a sweep.
  const fetchPeople = useCallback(async (id: number) => {
    setFetching((current) => ({ ...current, [id]: true }));
    try {
      const result = await api.yucgoutreach.fetchRegisterPeople(id);
      if (!result.ok) {
        setError(result.error || 'Could not read that register.');
        return;
      }
      setError('');
      const rows = await api.yucgoutreach.registerPeople(id);
      setPeople((current) => ({ ...current, [id]: rows }));
      setItems((current) => current.map((row) =>
        row.id === id ? { ...row, officer_count: result.officer_count ?? rows.length } : row));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that register.');
    } finally {
      setFetching((current) => ({ ...current, [id]: false }));
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

  const counts = Object.fromEntries((summary?.tiers || []).map((row) => [row.tier, row.n]));

  return (
    <div className="space-y-5" data-section="company-register">
      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm space-y-3">
        <h2 className="text-lg font-semibold text-deep-navy">Company register</h2>
        {summary && Array.isArray(summary.tiers) && (
          <p className="text-[13px] text-slate-500">
            On record: {(counts.us_public || 0).toLocaleString()} listed ·{' '}
            {(counts.us_employer || 0).toLocaleString()} US employers ·{' '}
            {(counts.us_nonprofit || 0).toLocaleString()} nonprofits that buy advice ·{' '}
            {(counts.us_private || 0).toLocaleString()} recently funded
            {counts.uk ? ` · ${counts.uk.toLocaleString()} UK` : ''} ·{' '}
            {((summary.tiers || []).reduce((sum, row) => sum + (row.with_officers || 0), 0)).toLocaleString()} with named officers
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
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={withOfficers} onChange={(event) => setWithOfficers(event.target.checked)} />
          Only companies with officers already named on a filing
        </label>
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
          <p className="text-sm text-slate-500">
            Nothing matches yet. The register fills in on a schedule; listed companies load first,
            then each recent Form D quarter.
          </p>
        )}
        <ul className="divide-y divide-[var(--border)]">
          {items.map((item) => (
            <li key={item.id} className="py-3 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 shrink-0"
                  aria-label={`Select ${item.company_name}`}
                  checked={picked.has(item.id)}
                  onChange={() => setPicked((current) => {
                    const next = new Map(current);
                    if (next.has(item.id)) next.delete(item.id);
                    else next.set(item.id, item.company_name);
                    return next;
                  })}
                />
                <div className="min-w-0">
                <div className="font-medium text-deep-navy">
                  {item.company_name}
                  {item.claimed_by && (
                    <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-900">
                      {item.claimed_by} is on this
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-500">
                  {[item.sector_label, item.region,
                    item.last_event_amount ? `raised ${money(item.last_event_amount)}${item.last_event_at ? ` · ${item.last_event_at}` : ''}` : null,
                    item.employees ? `${item.employees} employees (${item.employees_source})` : null,
                    item.metadata?.size_band || null,
                    item.metadata?.buys_outside_advice || null,
                    item.metadata?.revenue_range || null,
                    item.metadata?.target_role_title ? `aim at ${item.metadata.target_role_title}` : null,
                  ].filter(Boolean).join(' · ')}
                </div>
                {item.metadata?.why_attractive && (
                  <p className="mt-1 text-xs text-slate-600 line-clamp-2">{item.metadata.why_attractive}</p>
                )}
                {item.officer_count === 0 && (item.tier === 'us_public' || item.tier === 'uk') && (
                  <button
                    type="button"
                    className="mt-1 text-xs font-semibold text-steel-blue hover:underline disabled:opacity-50"
                    disabled={!!fetching[item.id]}
                    onClick={() => void fetchPeople(item.id)}
                  >
                    {fetching[item.id] ? 'Reading the register…' : 'Look up officers'}
                  </button>
                )}
                {item.officer_count > 0 && (item.working_count ?? 0) === 0 && (
                  <p className="mt-1 text-xs text-amber-800">
                    Filed officers are board and executive only — nobody at working level.
                    Find people at this company to reach someone who would run the project.
                  </p>
                )}
                {item.officer_count > 0 && (
                  <button
                    type="button"
                    className="mt-1 text-xs font-medium text-steel-blue hover:underline"
                    onClick={() => void loadPeople(item.id)}
                  >
                    {people[item.id] ? 'Officers on file:' : `${item.officer_count} officer(s) on file${(item.working_count ?? 0) > 0 ? `, ${item.working_count} working-level` : ''} — show`}
                  </button>
                )}
                {people[item.id] && (
                  <div className="mt-1 text-xs text-slate-600">
                    {people[item.id].length === 0
                      ? 'None recorded.'
                      : people[item.id].map((person) => `${person.full_name}${person.relationship ? ` (${person.relationship})` : ''}`).join(', ')}
                  </div>
                )}
                </div>
              </div>
              <button
                type="button"
                className="shrink-0 rounded-xl border border-deep-navy px-3 py-1.5 text-xs font-semibold text-deep-navy hover:bg-pale-sky/30"
                onClick={() => {
                  // Carry the domain the register already holds. Without it the
                  // search re-derives a domain it was just handed, and can
                  // resolve a different one for a common company name.
                  const query = new URLSearchParams({ view: 'company', company: item.company_name });
                  if (item.company_domain) query.set('domain', item.company_domain);
                  navigate(`/scraper?${query.toString()}`);
                }}
              >
                Find people here
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
