import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError, api, type Contact, type ImportOutcome, type ResolvedAlternative, type ResolvedCompany } from '../../api';
import CompanyAutocomplete from '../CompanyAutocomplete';
import RecipientPicker, { type PickerMode } from './RecipientPicker';
import CompanyLanes, { type Lane } from './CompanyLanes';
import RoleSuggestionBubbles from './RoleSuggestionBubbles';
import { LEVEL_TITLES, type Level, companyKey, defaultSelection } from '../../lib/recipients';
import { type ChosenCompany, type CompanyRun, useDiscoveryRuns } from '../../lib/useDiscoveryRuns';

/**
 * Companies to a reviewable campaign, in the order the work is actually done.
 *
 * Every step of this already existed and lived on a different screen: pick
 * companies on one page, run Find people on another, write copy in Studio,
 * assemble a campaign in a fourth, define follow-ups in a fifth. Nobody
 * presses that many buttons, so the later steps went unused.
 *
 * The steps are numbered rather than tabbed because they are ordered: you
 * cannot write to people you have not found. Each one states what it did, so
 * a member can stop after any of them and still have something real - a
 * campaign here is a draft, and nothing is sent without releasing it.
 *
 * The left rail holds the steps; the right surface is the same at every
 * step - the lanes that say where each company stands, then the sheet of
 * people - so the flowchart and the list it describes are never apart. A
 * step's controls sit in its rail body and end in the one button that
 * advances it. Everything that acts on a company (search it, add who was
 * found) is on the surface, next to the company.
 */

type Step = 1 | 2 | 3;

/** Server-side ceilings, restated so the step can warn before the request. */
const MAX_COMPANIES_PER_CAMPAIGN = 500;
/** Chips drawn before the row collapses to a count. */
const CHIP_WINDOW = 24;
/** People one search collects unless the member says otherwise. Enough for
 *  a team, not the whole payroll. */
const DEFAULT_MAX_PROSPECTS = 60;
const IDLE: CompanyRun = { state: 'idle', pct: 0, message: '' };
/** Searches that start by themselves in one sitting. Past this, a lane
 *  offers the search instead: the hourly limit is shared with everything
 *  else the member does. */
const AUTO_SEARCH_LIMIT = 5;
/** Who to look for, in the member's words, mapped to the titles a search
 *  asks for. Team leads by default: they are the people who answer. */
const WHO: { id: Exclude<Level, 'unknown'>; label: string }[] = [
  { id: 'working', label: 'Team leads' },
  { id: 'executive', label: 'Senior leaders' },
  { id: 'board', label: 'Board' },
];

/** Pasted text as the companies it names: one per line, or several LinkedIn
 *  pages on one line. A single line is one company even with commas in it -
 *  "Meta Platforms, Inc." is one name. */
function splitCompanies(text: string): string[] {
  const lines = text.split(/[\r\n]+/).map((l) => l.trim()).filter(Boolean);
  return lines.flatMap((line) => {
    const links = line.match(/(?:https?:\/\/)?(?:[a-z]+\.)?linkedin\.com\/(?:company|showcase)\/[^\s,;]+/gi);
    return links && links.length > 1 ? links : [line];
  });
}

function fromResolved(r: ResolvedCompany): ChosenCompany {
  return {
    name: r.name,
    domain: r.domain || undefined,
    verified: Boolean(r.domain_verified),
    linkedinUrl: r.linkedin_url || undefined,
    source: r.source,
    alternatives: Array.isArray(r.alternatives) ? r.alternatives : [],
    country: r.country || r.entity?.hq_country || undefined,
    entity: r.entity,
  };
}

/** Another group entity or country picked from "Not this?": it already
 *  carries its own profile, so it needs no second lookup. */
function fromAlternative(alt: ResolvedAlternative): ChosenCompany {
  return {
    name: alt.name,
    domain: alt.domain || alt.entity?.mail_domain || undefined,
    verified: Boolean(alt.domain || alt.entity?.mail_domain),
    source: 'register',
    country: alt.country || alt.entity?.hq_country || undefined,
    entity: alt.entity,
    targetCountry: alt.entity?.target_country || alt.country || undefined,
  };
}

/** A country code as people say it: GB is "UK". */
function countryLabel(code?: string | null): string {
  if (!code) return '';
  if (code === '*') return 'any country';
  return code.toUpperCase() === 'GB' ? 'UK' : code.toUpperCase();
}
const KIND_LABEL: Record<string, string> = { captive: 'service centre', subsidiary: 'subsidiary', region: 'region' };

/** Sure enough to search on its own: the member pasted its LinkedIn page,
 *  the club already works it, or its website was checked. */
function isSure(company: ChosenCompany): boolean {
  return Boolean(company.verified || company.source === 'linkedin' || company.source === 'club');
}

/** One chosen company: its name and website, a way to correct it, and a way
 *  to drop it. Clicking the name no longer removes it - that was a toggle
 *  that lost companies to stray clicks. */
function CompanyChip({ company, onRemove, onReplace, onPick, onWhere }: {
  company: ChosenCompany;
  onRemove: () => void;
  onReplace: (q: string) => void;
  /** Another group entity or country, already resolved. */
  onPick: (alt: ResolvedAlternative) => void;
  /** Look only in the home country, or anywhere. */
  onWhere: (target: string | undefined) => void;
}) {
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState('');
  const sure = isSure(company);
  // The panel closes like any popover - Escape, or a click anywhere else -
  // or it sits over the step's own button with no way out but a choice.
  const wrapRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <span ref={wrapRef} className="relative inline-flex max-w-full" data-company-chip={company.name}>
      <span className={`inline-flex h-10 max-w-full items-center gap-2 rounded-full border pl-4 pr-1 text-sm ${
        sure ? 'border-deep-navy bg-deep-navy text-white' : 'border-amber-300 bg-amber-50 text-amber-950'}`}>
        <span className="truncate font-medium" title={company.name}>{company.name}</span>
        {(company.targetCountry || company.country) && (
          <span className="shrink-0 text-xs opacity-80" data-testid="chip-country">
            · {company.targetCountry && company.targetCountry !== '*'
              ? `${countryLabel(company.targetCountry)} only`
              : countryLabel(company.targetCountry || company.country)}
          </span>
        )}
        {company.domain && (
          <span className="shrink-0 text-xs opacity-80">· {company.domain}{company.verified ? ' ✓' : ''}</span>
        )}
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
                className="shrink-0 text-xs underline opacity-90">
          {sure ? 'Not this?' : 'Best guess · Not this?'}
        </button>
        <button type="button" onClick={onRemove} aria-label={`Remove ${company.name}`}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-base leading-none hover:bg-black/10">
          ×
        </button>
      </span>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-80 max-w-[90vw] space-y-1 rounded-xl border border-pale-sky bg-white p-2 text-sm text-deep-navy shadow-lg">
          {company.country && (
            <div className="flex flex-wrap gap-1 px-2 pb-1">
              <span className="text-xs text-slate-500">Where:</span>
              <button type="button" onClick={() => { setOpen(false); onWhere(undefined); }}
                      aria-pressed={!company.targetCountry}
                      title={(company.entity?.exclude || []).length
                        ? `Everywhere except ${(company.entity?.exclude || []).map((x) => x.name).join(', ')}`
                        : 'Everywhere this company works'}
                      className="rounded-full border border-pale-sky px-2 text-xs aria-pressed:bg-deep-navy aria-pressed:text-white">
                Default
              </button>
              <button type="button" onClick={() => { setOpen(false); onWhere(company.country); }}
                      aria-pressed={Boolean(company.targetCountry) && company.targetCountry === company.country}
                      className="rounded-full border border-pale-sky px-2 text-xs aria-pressed:bg-deep-navy aria-pressed:text-white">
                {countryLabel(company.country)} only
              </button>
              <button type="button" onClick={() => { setOpen(false); onWhere('*'); }}
                      aria-pressed={company.targetCountry === '*'}
                      className="rounded-full border border-pale-sky px-2 text-xs aria-pressed:bg-deep-navy aria-pressed:text-white">
                Any country
              </button>
            </div>
          )}
          {(company.alternatives || []).length > 0 && <p className="px-2 text-xs text-slate-500">Did you mean</p>}
          {(company.alternatives || []).map((alt) => (
            <button key={`${alt.name}-${alt.country || ''}`} type="button"
                    onClick={() => { setOpen(false); if (alt.entity) onPick(alt); else onReplace(alt.name); }}
                    className="block w-full truncate rounded-lg px-2 py-1.5 text-left hover:bg-pale-sky/40">
              {alt.name}
              <span className="text-xs text-slate-500">
                {alt.country ? ` · ${countryLabel(alt.country)}` : ''}
                {alt.kind ? ` · ${KIND_LABEL[alt.kind] || alt.kind}` : ''}
                {alt.domain ? ` · ${alt.domain}` : ''}
              </span>
            </button>
          ))}
          <form className="flex gap-1 px-1 pt-1" onSubmit={(e) => {
            e.preventDefault();
            if (!link.trim()) return;
            setOpen(false);
            onReplace(link.trim());
          }}>
            <input value={link} onChange={(e) => setLink(e.target.value)} aria-label="Their LinkedIn page"
                   placeholder="Paste their LinkedIn page instead"
                   className="min-w-0 flex-1 rounded-lg border border-pale-sky px-2 py-1.5 text-xs" />
            <button type="submit" className="ui-button ui-button--secondary ui-button--sm">Use</button>
          </form>
        </div>
      )}
    </span>
  );
}
const PICKER_MODE: Record<Step, PickerMode> = { 1: 'preview', 2: 'select', 3: 'review' };

/** A numbered step header that is also the way back to that step. Defined
 *  outside the component so React keeps one instance rather than remounting
 *  every header on each keystroke in the message box. */
function StepHeading({ n, title, hint, step, onSelect }: {
  n: Step; title: string; hint: string; step: Step; onSelect: (n: Step) => void;
}) {
  return (
    <button
      type="button"
      data-step={n}
      onClick={() => onSelect(n)}
      className={`w-full text-left px-5 py-3 flex items-center gap-3 ${
        step === n ? 'bg-pale-sky/40' : 'hover:bg-pale-sky/20'}`}
      aria-current={step === n}
    >
      <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${
        step > n ? 'bg-emerald-600 text-white' : step === n ? 'bg-deep-navy text-white' : 'bg-pale-sky text-slate-600'}`}>
        {step > n ? '✓' : n}
      </span>
      <span className="min-w-0">
        <span className="block text-[15px] font-semibold text-deep-navy">{title}</span>
        <span className="block text-xs text-slate-500">{hint}</span>
      </span>
    </button>
  );
}

export type StageState = {
  step: Step;
  lanes: { company: string; found: number; ticked: number }[];
};

function clampProspects(raw: number): number {
  return Number.isFinite(raw) ? Math.min(800, Math.max(25, raw)) : DEFAULT_MAX_PROSPECTS;
}

export default function CampaignPipeline({ onStage }: { onStage?: (state: StageState) => void }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // A link that already carries search terms - the register's "Find people
  // here", the assistant's Fill, a bookmarked search - has made the first
  // choice already, so it opens on the step that acts on it rather than
  // asking again. Titles alone count: the assistant sends those without a
  // company when it has asked what roles to target, and hiding the field
  // behind a step would drop what the member just typed.
  const linkedCompany = (params.get('company') || '').trim();
  const linkedDomain = (params.get('domain') || '').trim();
  // Several companies at once, which is how Studio hands over a ticked
  // selection: there, ticking people only ever offered to delete them.
  const linkedCompanies = (params.get('companies') || '')
    .split(',').map((c) => c.trim()).filter(Boolean);
  const linkedRunId = Number(params.get('run') || '') || undefined;
  const arrivedWithSearch = Boolean(
    linkedCompany || linkedCompanies.length || linkedRunId
    || (params.get('titles') || '').trim() || linkedDomain,
  );
  const [step, setStep] = useState<Step>(arrivedWithSearch ? 2 : 1);

  // A company is a name and, when something already knows it, a domain: the
  // register hands one over so the search does not re-derive a domain it
  // was just given. A typed name has none and the server infers it.
  const [chosen, setChosen] = useState<ChosenCompany[]>(() => (
    linkedCompanies.length
      ? linkedCompanies.map((name) => ({ name }))
      // A company handed over with its website comes from the register,
      // whose websites come from filings: the same trusted record the
      // resolver accepts, so it is sure enough to search on its own.
      : linkedCompany ? [{
        name: linkedCompany,
        domain: linkedDomain || undefined,
        verified: Boolean(linkedDomain),
        source: linkedDomain ? 'register' as const : undefined,
      }] : []
  ));
  const [typed, setTyped] = useState('');

  const [people, setPeople] = useState<Contact[]>([]);
  // Ticked recipients. The old step kept a list of people to *drop*, which
  // meant the default was "write to everyone found" and the member's real
  // choice was invisible.
  const [selected, setSelected] = useState<number[]>([]);
  // Once the member has touched the ticks, a refresh must not re-decide
  // them: only people who have just been added get the default treatment.
  const touchedRef = useRef(false);

  // What the search is told. Shared across companies because it is what the
  // member wants, not what a company calls it; the role bubbles translate it
  // per company and only add to it when clicked.
  // Who to look for is one choice; titles in the member's own words are
  // optional and, when given, win. Nobody has to type a title to search.
  const [level, setLevel] = useState<Exclude<Level, 'unknown'>>('working');
  const [titleHints, setTitleHints] = useState(() => (params.get('titles') || '').trim());
  const [showOtherTitles, setShowOtherTitles] = useState(() => Boolean((params.get('titles') || '').trim()));
  const [maxProspects] = useState(() => (
    params.get('max') ? clampProspects(Number(params.get('max'))) : DEFAULT_MAX_PROSPECTS
  ));
  const searchTitles = titleHints.trim() || LEVEL_TITLES[level];
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [resolving, setResolving] = useState(0);

  const [showAllChips, setShowAllChips] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');


  const chosenNames = useMemo(() => chosen.map((c) => c.name), [chosen]);

  // Adding never removes: Add on a company already chosen used to toggle it
  // off again.
  const addCompany = (company: ChosenCompany) =>
    setChosen((current) => current.some((c) => companyKey(c.name) === companyKey(company.name))
      ? current
      : [...current, company]);

  // Every way a company arrives - typed, picked, pasted, a LinkedIn page -
  // goes through one resolver, which returns the company and a website only
  // when it could check it. The member never picks a domain; a wrong guess
  // is corrected from the chip, not from a settings panel.
  const resolve = async (q: string): Promise<ChosenCompany | null> => {
    try {
      const res = await api.yucgoutreach.resolveCompany(q);
      return res && typeof res.name === 'string' && res.name ? fromResolved(res) : { name: q, source: 'typed' };
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setError(e.message);
        return null;
      }
      // Lookup unavailable: keep the name as typed rather than lose it.
      return { name: q, source: 'typed' };
    }
  };
  const addFromText = async (text: string) => {
    const items = splitCompanies(text);
    if (!items.length) return;
    setError('');
    setResolving((n) => n + items.length);
    await Promise.all(items.map(async (q) => {
      try {
        const company = await resolve(q);
        if (company) addCompany(company);
      } finally {
        setResolving((n) => n - 1);
      }
    }));
  };
  const replaceCompany = async (oldName: string, q: string) => {
    setResolving((n) => n + 1);
    try {
      const company = await resolve(q);
      if (!company) return;
      autoTried.current.delete(companyKey(company.name));
      setChosen((current) => {
        const rest = current.filter((c) => companyKey(c.name) !== companyKey(oldName)
          && companyKey(c.name) !== companyKey(company.name));
        const at = current.findIndex((c) => companyKey(c.name) === companyKey(oldName));
        rest.splice(at < 0 ? rest.length : at, 0, company);
        return rest;
      });
    } finally {
      setResolving((n) => n - 1);
    }
  };
  // Put a different, already-resolved company in this chip's place.
  const swapCompany = (oldName: string, company: ChosenCompany) => {
    autoTried.current.delete(companyKey(company.name));
    setChosen((current) => {
      const at = current.findIndex((c) => companyKey(c.name) === companyKey(oldName));
      const rest = current.filter((c) => companyKey(c.name) !== companyKey(oldName)
        && companyKey(c.name) !== companyKey(company.name));
      rest.splice(at < 0 ? rest.length : at, 0, company);
      return rest;
    });
  };
  // Where a company's search looks. Changing it lets the next search of that
  // company run again: the lane's Find people, or "Search all countries".
  const setWhere = (name: string, target: string | undefined) =>
    setChosen((current) => current.map((c) => (companyKey(c.name) === companyKey(name) ? { ...c, targetCountry: target } : c)));
  const autoTried = useRef<Set<string>>(new Set());
  const autoStarted = useRef(0);

  const visibleChips = showAllChips ? chosenNames : chosenNames.slice(0, CHIP_WINDOW);
  const hiddenChips = chosenNames.length - visibleChips.length;

  // The people on file at the chosen companies follow the companies: choose
  // one and its people appear, at whatever step. Debounced so a run of chip
  // clicks is one request, and stamped so a slow answer to an old choice
  // cannot overwrite the current one.
  const requestRef = useRef(0);
  // Which companies the people list currently answers for: a search that
  // starts by itself must not mistake "not loaded yet" for "nobody on file".
  const [peopleFor, setPeopleFor] = useState<string | null>(null);
  const loadPeople = useCallback(async (names: string[], createdIds: number[] = []) => {
    const requestId = ++requestRef.current;
    if (names.length === 0) {
      setPeople([]);
      setSelected([]);
      setBusy(false);
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await api.contacts.list({ companies: names.join(','), limit: 800 });
      if (requestId !== requestRef.current) return;
      const items = Array.isArray(res?.items) ? res.items : [];
      setPeople(items);
      setPeopleFor(names.map(companyKey).sort().join('\n'));
      setSelected((current) => {
        if (!touchedRef.current) return defaultSelection(items);
        const present = new Set(items.map((p) => p.id));
        const fresh = defaultSelection(items.filter((p) => createdIds.includes(p.id)));
        return [...new Set([...current.filter((id) => present.has(id)), ...fresh])];
      });
    } catch (e) {
      if (requestId !== requestRef.current) return;
      setError(e instanceof Error ? e.message : 'Could not load people');
    } finally {
      if (requestId === requestRef.current) setBusy(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadPeople(chosenNames); }, 250);
    return () => window.clearTimeout(timer);
  }, [chosenNames, loadPeople]);

  const onImported = useCallback(async (outcomes: ImportOutcome[]) => {
    const createdIds = outcomes
      .flatMap((o) => (o.outcome === 'created' && o.contact_id !== null ? [o.contact_id] : []));
    await loadPeople(chosenNames, createdIds);
  }, [loadPeople, chosenNames]);

  const {
    runs, runsReady, found, linkedRun, notice: runNotice, error: runError,
    startRun, addFound, exportRun, deleteRun,
  } = useDiscoveryRuns({ chosen, linkedRunId, onImported });

  // A link to a run means that run's company: make sure it has a lane, and
  // put it in focus. Once per run, deferred out of the effect body.
  const linkedApplied = useRef<number | null>(null);
  useEffect(() => {
    if (!linkedRun || linkedApplied.current === linkedRun.id) return;
    const timer = window.setTimeout(() => {
      linkedApplied.current = linkedRun.id;
      const key = companyKey(linkedRun.company_name);
      setChosen((current) => current.some((c) => companyKey(c.name) === key)
        ? current
        : [...current, { name: linkedRun.company_name, domain: linkedRun.company_domain || undefined }]);
      setFocusedKey(key);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [linkedRun]);

  const changeSelection = (ids: number[]) => {
    touchedRef.current = true;
    setSelected(ids);
  };

  // Only what was ticked, and only what can be reached: a selection kept from
  // before an address was removed must not become a silent send failure.
  const recipients = useMemo(
    () => people.filter((p) => p.email && selected.includes(p.id)),
    [people, selected],
  );

  // One lane per company: how many people it has (on file, plus found and
  // not yet on file), how many of them are ticked, and what its search is
  // doing.
  const lanes = useMemo<Lane[]>(() => {
    const byKey: Record<string, Contact[]> = {};
    for (const person of people) (byKey[companyKey(person.company)] ||= []).push(person);
    return chosen.map((company) => {
      const key = companyKey(company.name);
      const rows = byKey[key] || [];
      const onFile = new Set(rows.map((p) => (p.email || '').trim().toLowerCase()).filter(Boolean));
      const extra = (found[key] || []).filter((p) => !onFile.has((p.email || '').trim().toLowerCase())).length;
      return {
        company: company.name,
        found: rows.length + extra,
        ticked: rows.filter((p) => selected.includes(p.id) && p.email).length,
        run: runs[key] || IDLE,
      };
    });
  }, [chosen, people, selected, found, runs]);

  useEffect(() => {
    onStage?.({ step, lanes });
  }, [onStage, step, lanes]);

  // Moving to a step scrolls it to the top of the column, so the work is
  // where the eye already is instead of below the fold.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const heading = rootRef.current?.querySelector(`[data-step="${step}"]`);
    if (!heading) return;
    const timer = window.setTimeout(
      () => heading.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
    return () => window.clearTimeout(timer);
  }, [step]);


  // The company whose vocabulary the search controls are about: the lane
  // last pointed at, else the first chosen.
  const focused = chosen.find((c) => companyKey(c.name) === focusedKey) ?? chosen[0] ?? null;
  // What a search is told, read when it starts rather than when it was
  // queued: changing who to look for while companies wait changes what all
  // of them look for.
  const searchOptionsRef = useRef({ titleHints: searchTitles, maxProspects });
  useEffect(() => { searchOptionsRef.current = { titleHints: searchTitles, maxProspects }; }, [searchTitles, maxProspects]);

  // One search, from wherever it is asked for: the lane, the group header in
  // the sheet, or on its own when a company arrives with nobody on file.
  const findPeople = useCallback((company: ChosenCompany) => {
    setFocusedKey(companyKey(company.name));
    void startRun(company, () => searchOptionsRef.current);
  }, [startRun]);

  // A company the member is sure of, with nobody on file and no search yet,
  // is searched as soon as it is added - the search is the reason it was
  // added. Waits until both the people and the earlier searches are known,
  // and stops starting searches on its own after AUTO_SEARCH_LIMIT.
  const chosenKeyString = chosen.map((c) => companyKey(c.name)).sort().join('\n');
  useEffect(() => {
    if (busy || !runsReady || peopleFor !== chosenKeyString) return;
    const due = chosen.filter((c) => {
      const key = companyKey(c.name);
      if (autoTried.current.has(key) || !isSure(c)) return false;
      const lane = lanes.find((l) => companyKey(l.company) === key);
      return Boolean(lane && lane.found === 0 && lane.run.state === 'idle');
    });
    if (!due.length) return;
    const timer = window.setTimeout(() => {
      for (const company of due) {
        autoTried.current.add(companyKey(company.name));
        if (autoStarted.current >= AUTO_SEARCH_LIMIT) continue;
        autoStarted.current += 1;
        findPeople(company);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [busy, runsReady, peopleFor, chosenKeyString, chosen, lanes, findPeople]);

  const findByName = useCallback((name: string) => {
    const company = chosen.find((c) => companyKey(c.name) === companyKey(name));
    if (company) findPeople(company);
  }, [chosen, findPeople]);

  return (
    // 18.25rem is the shell around this grid at xl: header, main padding,
    // page header and subnav above; workspace and main padding below. The
    // grid fills the rest exactly so the page never grows a scrollbar of
    // its own beside the rail's and the surface's.
    <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 xl:h-[calc(100vh-18.25rem)]"
         data-section="campaign-pipeline">
      <div ref={rootRef}
           data-rail
           className="xl:col-span-2 min-h-0 xl:max-h-full xl:overflow-y-auto surface-card rounded-2xl border border-[var(--border)] shadow-sm overflow-hidden self-start">
      <StepHeading n={1} title="Add companies" hint={chosen.length ? `${chosen.length} added` : 'A name or its LinkedIn page'} step={step} onSelect={setStep} />
      {step === 1 && (
        <div className="px-5 pb-5 space-y-3 border-b border-pale-sky">
          <div className="flex gap-2 items-end">
            <div className="flex-1">
              <CompanyAutocomplete
                id="pipeline-company"
                label="Company"
                value={typed}
                placeholder="Type a company, or paste its LinkedIn page"
                onChange={(name) => setTyped(name)}
                onSelect={(option) => { setTyped(''); void addFromText(option.name); }}
                onSubmitText={(text) => { setTyped(''); void addFromText(text); }}
              />
            </div>
            <button
              type="button"
              disabled={!typed.trim()}
              onClick={() => { const text = typed; setTyped(''); void addFromText(text); }}
              className="ui-button ui-button--secondary shrink-0"
            >
              Add
            </button>
          </div>
          <p className="text-xs text-slate-500">
            {resolving > 0
              ? 'Looking it up…'
              : 'Paste a LinkedIn company page for an exact match. Several at once work too, one per line.'}
          </p>

          {chosen.length > 0 && (
            <p className="text-xs text-slate-500" data-testid="chosen-count">
              {chosen.length} compan{chosen.length === 1 ? 'y' : 'ies'} added
              {chosen.length > MAX_COMPANIES_PER_CAMPAIGN
                ? ` — a campaign takes at most ${MAX_COMPANIES_PER_CAMPAIGN}; drop some or split the release.`
                : ''}
              {' · '}
              <button type="button" className="underline" onClick={() => setChosen([])}>Clear all</button>
            </p>
          )}

          {/* Only a screenful of chips is drawn: a selection of several hundred
              companies is a count plus the ones being worked on, not eight
              hundred DOM nodes. */}
          <div className="flex flex-wrap gap-2" data-testid="company-chips">
            {chosen.slice(0, showAllChips ? undefined : CHIP_WINDOW).map((company) => (
              <CompanyChip
                key={company.name}
                company={company}
                onRemove={() => setChosen((current) => current.filter((c) => companyKey(c.name) !== companyKey(company.name)))}
                onReplace={(q) => { void replaceCompany(company.name, q); }}
                onPick={(alt) => swapCompany(company.name, fromAlternative(alt))}
                onWhere={(target) => setWhere(company.name, target)}
              />
            ))}
            {hiddenChips > 0 && (
              <button
                type="button"
                onClick={() => setShowAllChips((v) => !v)}
                className="inline-flex h-10 items-center rounded-full border border-dashed border-steel-blue px-4 text-sm text-deep-navy"
              >
                {showAllChips ? 'Show fewer' : `+${hiddenChips} more`}
              </button>
            )}
          </div>

          <button
            type="button"
            disabled={chosen.length === 0 || resolving > 0}
            onClick={() => setStep(2)}
            className="ui-button ui-button--primary w-full py-3 text-[15px]"
          >
            {chosen.length === 0 ? 'Add a company to start' : 'Next: tick who gets it'}
          </button>
        </div>
      )}

      <StepHeading n={2} title="Tick who gets it" hint={people.length ? `${recipients.length} of ${people.length} ticked` : 'Searches start by themselves'} step={step} onSelect={setStep} />
      {step === 2 && (
        <div className="px-5 pb-5 space-y-3 border-b border-pale-sky">
          <p className="text-sm text-deep-navy">
            <strong>{recipients.length}</strong> of {people.filter((p) => p.email).length} reachable
            people ticked, at {chosen.length} compan{chosen.length === 1 ? 'y' : 'ies'}. Tick them on
            the right.
          </p>
          {people.length === 0 && !busy && (
            <p className="text-sm text-slate-500">
              Nobody on record at these companies yet. Searches start by themselves for companies we
              could confirm; press Find people on any other lane.
            </p>
          )}

          {/* Who a search looks for is one choice, not a title the member has
              to guess. Titles in their own words are there for anyone who
              wants them, and the company's own titles are one click each. */}
          <div>
            <p className="text-xs font-medium text-slate-600" id="who-to-look-for">Who to look for</p>
            <div role="radiogroup" aria-labelledby="who-to-look-for" className="mt-1 inline-flex rounded-lg border border-pale-sky p-0.5">
              {WHO.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={level === option.id}
                  onClick={() => setLevel(option.id)}
                  className={`rounded-md px-3 py-1.5 text-sm ${level === option.id ? 'bg-deep-navy text-white' : 'text-deep-navy hover:bg-pale-sky/40'}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Searching for: {searchTitles}{' · '}
              <button type="button" className="underline" onClick={() => setShowOtherTitles((v) => !v)}>
                {showOtherTitles ? 'Hide other titles' : 'Other titles…'}
              </button>
            </p>
            {showOtherTitles && (
              <input
                className="mt-1 w-full rounded-lg border border-pale-sky px-3 py-2 text-sm text-deep-navy"
                aria-label="Other titles"
                value={titleHints}
                onChange={(e) => setTitleHints(e.target.value)}
                placeholder="In your words, e.g. Head of Partnerships, Producer"
              />
            )}
          </div>
          {focused && (
            <div data-testid="focused-company" data-company={focused.name}>
              <RoleSuggestionBubbles
                company={focused.name}
                domain={focused.domain || ''}
                hints={titleHints}
                onAdd={(title) => {
                  setShowOtherTitles(true);
                  setTitleHints((prev) => {
                    const parts = prev.split(',').map((p) => p.trim()).filter(Boolean);
                    if (parts.some((p) => p.toLowerCase() === title.toLowerCase())) return prev;
                    return [...parts, title].join(', ');
                  });
                }}
              />
            </div>
          )}

          {/* The other way people arrive: the spreadsheet you already have. */}
          <div className="flex flex-wrap items-center gap-4 text-xs">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                setImporting(true);
                setError('');
                try {
                  const res = await api.contacts.importFile(file);
                  await loadPeople(chosenNames);
                  setError(res.duplicates_skipped
                    ? `Imported ${res.count}; ${res.duplicates_skipped} already on record.`
                    : '');
                } catch (e) {
                  setError(e instanceof Error ? e.message : 'Could not import that file');
                } finally {
                  setImporting(false);
                  event.target.value = '';
                }
              }}
            />
            <button
              type="button"
              disabled={importing}
              onClick={() => fileRef.current?.click()}
              className="ui-button ui-button--ghost ui-button--sm"
            >
              {importing ? 'Importing…' : 'Or import a spreadsheet'}
            </button>
          </div>

          <button
            type="button"
            disabled={recipients.length === 0}
            onClick={() => setStep(3)}
            className="ui-button ui-button--primary"
          >
            Write to these {recipients.length}
          </button>
        </div>
      )}

      <StepHeading n={3} title="Write to each of them" hint="An advisory email per person, in Drafts" step={step} onSelect={setStep} />
      {step === 3 && (
        <div className="px-5 pb-5 space-y-3 border-b border-pale-sky">
          {/* This step used to write one template for everybody, with
              {first} and {company} as the only parts that changed - which is
              exactly how it read. Drafts writes to one person at a time, so
              the chosen people go there and each gets a draft of their own. */}
          <p className="text-sm text-slate-700">
            Each of the {recipients.length} gets their own email in Drafts: an advisory note on two
            or three projects a YUCG team could build for their company, chosen for their role.
            Every draft is yours to read and edit before anything is sent.
          </p>
          <button
            type="button"
            disabled={recipients.length === 0}
            onClick={() => navigate(`/studio?${new URLSearchParams({
              companies: [...new Set(recipients.map((r) => (r.company || '').trim()).filter(Boolean))].join(','),
              contact_ids: recipients.map((r) => r.id).join(','),
            })}`)}
            className="ui-button ui-button--primary"
          >
            Write to these {recipients.length} in Drafts →
          </button>
        </div>
      )}

      {error && <p className="px-5 pb-4 text-sm text-red-700">{error}</p>}
      </div>

      {/* The surface: the same two things at every step, in the same order,
          so the flowchart and the list it describes are never apart. The
          aside is the only thing that scrolls; the lanes stay short and the
          sheet takes whatever is left. */}
      <aside className="xl:col-span-3 min-h-0 xl:overflow-y-auto space-y-4" data-surface>
        {runNotice && (
          <p className="ui-notice ui-notice--info text-sm" role="status">{runNotice}</p>
        )}
        {runError && (
          <p className="ui-notice ui-notice--danger text-sm" role="alert">{runError}</p>
        )}
        <CompanyLanes
          lanes={lanes}
          focused={focused?.name ?? null}
          onFocus={(name) => setFocusedKey(companyKey(name))}
          onFind={findByName}
          onExport={(runId) => void exportRun(runId)}
          onDelete={(runId) => {
            if (window.confirm('Delete this search and everything it found?')) void deleteRun(runId);
          }}
        />

        {chosen.length > 0 ? (
          <section className="surface-card rounded-2xl border border-pale-sky px-4 pb-4" aria-label="People" data-testid="sheet">
            <RecipientPicker
              mode={PICKER_MODE[step]}
              companies={chosen}
              people={people}
              selected={selected}
              onSelectedChange={changeSelection}
              found={found}
              runs={runs}
              onFind={findPeople}
              onFindEverywhere={(company) => {
                setWhere(company.name, '*');
                findPeople({ ...company, targetCountry: '*' });
              }}
              onAddFound={addFound}
              busy={busy}
            />
          </section>
        ) : (
          <section className="surface-card rounded-2xl border border-pale-sky p-4 text-sm text-slate-500" aria-label="People">
            Choose a company and the people on file there appear here.
          </section>
        )}
      </aside>
    </div>
  );
}
