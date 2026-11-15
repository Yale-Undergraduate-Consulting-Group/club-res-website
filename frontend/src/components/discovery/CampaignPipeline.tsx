import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ApiError, api, type Contact, type ImportOutcome, type ResolvedAlternative, type ResolvedCompany } from '../../api';
import CompanyAutocomplete from '../CompanyAutocomplete';
import RecipientPicker from './RecipientPicker';
import CompanyLanes, { type Lane } from './CompanyLanes';
import SendPanel, { type Held } from './SendPanel';
import { Button, Chip } from '../ui/Primitives';
import { LEVEL_CHOICES, companyKey, defaultSelection, levelTitles, type Level } from '../../lib/recipients';
import {
  WANTED_LABEL, companyStatus, dedupePeople, foundNotOnFile, matchesWanted, wantedLabel,
  type CompanyStatus,
} from '../../lib/peopleSheet';
import { recipientsOf, tallyPeople, tallyReasons, type Tally } from '../../lib/sendPlan';
import { type ChosenCompany, type CompanyRun, type SearchOptions, useDiscoveryRuns } from '../../lib/useDiscoveryRuns';

/**
 * From companies to sent emails on one screen.
 *
 * This used to be three steps that ended in other pages: find people here,
 * write copy in Drafts, assemble and release a campaign in Campaigns - ten
 * clicks for one company and a serial wait for every draft. Nobody pressed
 * that many buttons, so the later steps went unused.
 *
 * Now adding a company is the whole of step one: its search starts at once
 * (its filing's officers and a web search; a few run at once, the rest wait),
 * everyone it finds is on the list as they arrive, ticked by the default
 * rule, and the message and the one Send button are in the left rail under
 * the companies. The campaign page and Drafts stay for members who want
 * them; this flow does not need either.
 *
 * The right surface is the lanes that say where each company stands, then the
 * sheet of people. The count in the sheet's header, in each lane and on the
 * Send button is one number (sendPlan.ts), so they cannot disagree.
 */

/** Server-side ceilings, restated so the page can warn before the request. */
const MAX_COMPANIES_PER_CAMPAIGN = 500;
/** Chips drawn before the row collapses to a count. */
const CHIP_WINDOW = 24;
/** Qualified people one search keeps going for unless the member says
 *  otherwise: the API's own default (YUCG_DEFAULT_PROSPECTS). */
const DEFAULT_MAX_PROSPECTS = 120;
/** The server's ceiling (YUCG_MAX_PROSPECTS), restated for the input. */
const MAX_PROSPECTS = 800;
/** People read from the club's contacts for the chosen companies at once:
 *  the server's page ceiling. */
const CONTACT_PAGE = 2000;
/** Times a refused add is asked again before the people are left listed. */
const IMPORT_TRIES = 3;
const IMPORT_RETRY_MS = 3000;
const IDLE: CompanyRun = { state: 'idle', pct: 0, message: '' };

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

/** Sure enough to say so on its chip: the member pasted its LinkedIn page, the
 *  club already works it, or its website was checked. */
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
      <span className={`company-chip-body inline-flex h-9 max-w-full items-center gap-2 overflow-hidden border pl-4 pr-0 text-sm ${
        sure ? 'border-deep-navy bg-deep-navy text-white' : 'border-pale-sky bg-pale-sky/60 text-deep-navy'}`}>
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
        <Button variant="ghost" size="sm" iconOnly className="shrink-0" onClick={onRemove} aria-label={`Remove ${company.name}`}>
          ×
        </Button>
      </span>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-80 max-w-[90vw] space-y-1 border border-pale-sky bg-white p-2 text-sm text-deep-navy">
          {company.country && (
            <div className="flex flex-wrap items-center gap-2 px-2 pb-1">
              <span className="text-xs text-slate-500">Where:</span>
              <Chip onClick={() => { setOpen(false); onWhere(undefined); }}
                    selected={!company.targetCountry}
                    title={(company.entity?.exclude || []).length
                      ? `Everywhere except ${(company.entity?.exclude || []).map((x) => x.name).join(', ')}`
                      : 'Everywhere this company works'}>
                Default
              </Chip>
              <Chip onClick={() => { setOpen(false); onWhere(company.country); }}
                    selected={Boolean(company.targetCountry) && company.targetCountry === company.country}>
                {countryLabel(company.country)} only
              </Chip>
              <Chip onClick={() => { setOpen(false); onWhere('*'); }}
                    selected={company.targetCountry === '*'}>
                Any country
              </Chip>
            </div>
          )}
          {(company.alternatives || []).length > 0 && <p className="px-2 text-xs text-slate-500">Did you mean</p>}
          {(company.alternatives || []).map((alt) => (
            <button key={`${alt.name}-${alt.country || ''}`} type="button"
                    onClick={() => { setOpen(false); if (alt.entity) onPick(alt); else onReplace(alt.name); }}
                    title={`${alt.name}${alt.country ? ` · ${countryLabel(alt.country)}` : ''}`}
                    className="block w-full truncate px-2 py-1.5 text-left hover:bg-pale-sky/40">
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
                   className="min-w-0 flex-1 border border-pale-sky px-2 py-1.5 text-xs" />
            <button type="submit" className="ui-button ui-button--secondary ui-button--sm">Use</button>
          </form>
        </div>
      )}
    </span>
  );
}

function clampProspects(raw: number): number {
  return Number.isFinite(raw) && raw > 0 ? Math.min(MAX_PROSPECTS, Math.max(1, Math.round(raw))) : DEFAULT_MAX_PROSPECTS;
}

export default function CampaignPipeline() {
  const [params] = useSearchParams();
  // A link that already carries search terms - the register's "Find people
  // here", the assistant's Fill, a bookmarked search - has made the first
  // choice already: its companies are added and searched as the page opens.
  const linkedCompany = (params.get('company') || '').trim();
  const linkedDomain = (params.get('domain') || '').trim();
  // Several companies at once, which is how the register hands over a
  // ticked selection.
  const linkedCompanies = (params.get('companies') || '')
    .split(',').map((c) => c.trim()).filter(Boolean);
  const linkedRunId = Number(params.get('run') || '') || undefined;

  // A company is a name and, when something already knows it, a domain: the
  // register hands one over so the search does not re-derive a domain it
  // was just given. A typed name has none and the server infers it.
  const [chosen, setChosen] = useState<ChosenCompany[]>(() => (
    linkedCompanies.length
      ? linkedCompanies.map((name) => ({ name }))
      // A company handed over with its website comes from the register,
      // whose websites come from filings: the same trusted record the
      // resolver accepts.
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
  // Ticked people the server would not write to, and why: reported by the
  // message panel, which asks. They are counted out of the one number.
  const [held, setHeld] = useState<Map<number, string>>(new Map());

  // What the search is told. Shared across companies because it is what the
  // member wants, not what a company calls it.
  // Who to look for is a choice of levels, at least one; titles in the
  // member's own words are optional and, when given, win. Nobody has to type
  // a title to search.
  const [levels, setLevels] = useState<ReadonlySet<Level>>(() => new Set<Level>(['working', 'executive']));
  const levelsRef = useRef(levels);
  const levelList = useMemo(() => LEVEL_CHOICES.filter((l) => levels.has(l)), [levels]);
  const [titleHints, setTitleHints] = useState(() => (params.get('titles') || '').trim());
  const [showOtherTitles, setShowOtherTitles] = useState(() => Boolean((params.get('titles') || '').trim()));
  // The goal a search keeps going for: it stops at this many qualified
  // people, or sooner only when the company has no more to find. Kept as
  // text while typing so clearing the box to retype is not fought.
  const [maxText, setMaxText] = useState(() => String(
    params.get('max') ? clampProspects(Number(params.get('max'))) : DEFAULT_MAX_PROSPECTS,
  ));
  const maxProspects = clampProspects(Number(maxText));
  const searchTitles = titleHints.trim() || levelTitles(levels);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [resolving, setResolving] = useState(0);

  const [showAllChips, setShowAllChips] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const chosenNames = useMemo(() => chosen.map((c) => c.name), [chosen]);
  // Read by work that outlives a render (a lookup that finishes after the
  // member added another company), so it reloads the people for what is
  // chosen now rather than when it started.
  const chosenNamesRef = useRef(chosenNames);
  useEffect(() => { chosenNamesRef.current = chosenNames; }, [chosenNames]);

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
  // company run again: its "Search again", or "Search all countries".
  const setWhere = (name: string, target: string | undefined) =>
    setChosen((current) => current.map((c) => (companyKey(c.name) === companyKey(name) ? { ...c, targetCountry: target } : c)));
  const autoTried = useRef<Set<string>>(new Set());

  const visibleChips = showAllChips ? chosenNames : chosenNames.slice(0, CHIP_WINDOW);
  const hiddenChips = chosenNames.length - visibleChips.length;

  // The people on file at the chosen companies follow the companies: choose
  // one and its people appear. Debounced so a run of chip clicks is one
  // request, and stamped so a slow answer to an old choice cannot overwrite
  // the current one.
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
      const res = await api.contacts.list({ companies: names.join(','), limit: CONTACT_PAGE });
      if (requestId !== requestRef.current) return;
      // Only people at the chosen companies are listed, so the header, the
      // lanes and what Send writes to count the same people. The same person
      // on file twice (two addresses, one name) is one row; someone already
      // written to is the one kept, so that history shows.
      const rows = Array.isArray(res?.items) ? res.items : [];
      const chosenKeys = new Set(names.map(companyKey));
      const items = dedupePeople(
        rows.filter((p) => chosenKeys.has(companyKey(p.company))), (p) => p, (p) => (p.last_sent_at ? 1 : 0),
      );
      setPeople(items);
      setPeopleFor(names.map(companyKey).sort().join('\n'));
      if (Number(res?.total) > rows.length) {
        setError(`Showing the first ${rows.length} of ${res.total} people at these companies. Search for the rest by company.`);
      }
      setSelected((current) => {
        if (!touchedRef.current) return defaultSelection(items, levelsRef.current);
        const present = new Set(items.map((p) => p.id));
        const fresh = defaultSelection(items.filter((p) => createdIds.includes(p.id)), levelsRef.current);
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
    await loadPeople(chosenNamesRef.current, createdIds);
  }, [loadPeople]);

  const {
    runs, runsReady, found, linkedRun, notice: runNotice, error: runError,
    startRun, addFound, holdFound, exportRun, deleteRun,
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

  // A level is switched on or off; the last one stays on, because a search
  // needs someone to look for. Ticks the member has not touched follow the
  // choice, so the header and the Send count change with it.
  const toggleLevel = (id: Level) => {
    const next = new Set(levels);
    if (!next.delete(id)) next.add(id);
    if (next.size === 0) return;
    levelsRef.current = next;
    setLevels(next);
    if (!touchedRef.current) setSelected(defaultSelection(people, next));
  };

  // One number, counted once: who is ticked, reachable and not held. The
  // header, every lane and the Send button read this and nothing else.
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  // Everyone ticked who has an address: who the server is asked about, and
  // what a selection kept from before an address was removed cannot become.
  const ticked = useMemo(() => people.filter((p) => p.email && selectedSet.has(p.id)), [people, selectedSet]);
  const recipients = useMemo(() => recipientsOf(people, selectedSet, held), [people, selectedSet, held]);

  // One lane per company: how many people can be emailed there, and what
  // its search is doing.
  const { lanes, statuses, tally, tallies, heldList } = useMemo(() => {
    const byKey: Record<string, Contact[]> = {};
    for (const person of people) (byKey[companyKey(person.company)] ||= []).push(person);
    const statusByKey: Record<string, CompanyStatus> = {};
    const tallyByKey: Record<string, Tally> = {};
    const heldNames: Held[] = [];
    const refusedCounts: number[] = [];
    const list = chosen.map<Lane>((company) => {
      const key = companyKey(company.name);
      const rows = byKey[key] || [];
      const refused = (found[key] || []).length ? foundNotOnFile(people, found[key]).filter((p) => p.skippedReason) : [];
      for (const p of refused) {
        heldNames.push({ name: [p.first_name, p.last_name].filter(Boolean).join(' ') || p.email || 'Someone', reason: p.skippedReason! });
      }
      refusedCounts.push(refused.length);
      const companyTally = tallyPeople(rows, selectedSet, held, refused.length);
      tallyByKey[key] = companyTally;
      const run = runs[key] || IDLE;
      const matching = rows.filter((p) => matchesWanted(p, levels)).length;
      const status = companyStatus({ run, total: companyTally.total, ready: companyTally.canEmail, matching, wanted: levels });
      statusByKey[key] = status;
      return { company: company.name, found: companyTally.total, ready: companyTally.canEmail, run, status };
    });
    for (const person of people) {
      const reason = selectedSet.has(person.id) ? held.get(person.id) : undefined;
      if (reason) heldNames.push({ name: person.name || person.email, reason });
    }
    return {
      lanes: list, statuses: statusByKey, tallies: tallyByKey, heldList: heldNames,
      tally: tallyPeople(people, selectedSet, held, refusedCounts.reduce((n, count) => n + count, 0)),
    };
  }, [chosen, people, selectedSet, held, found, runs, levels]);

  // Why a search could not run, once per reason however many companies hit it.
  const pausedNotes = [...new Set(lanes.flatMap((l) => (l.status.problem ? [l.status.problem] : [])))];
  const searchingCount = lanes.filter((l) => l.run.state === 'searching' || l.run.state === 'queued').length;

  // The company the lanes highlight: the one last pointed at, else the first.
  const focusedName = (chosen.find((c) => companyKey(c.name) === focusedKey) ?? chosen[0])?.name ?? null;
  // What a search is told, read when it starts rather than when it was
  // queued: changing who to look for while companies wait changes what all
  // of them look for.
  const searchOptionsRef = useRef<SearchOptions>({ titleHints: searchTitles, maxProspects, levels: levelList });
  useEffect(() => {
    searchOptionsRef.current = { titleHints: searchTitles, maxProspects, levels: levelList };
  }, [searchTitles, maxProspects, levelList]);

  // The one search, from the company's own button in the sheet or on its own
  // when a company is added.
  const findPeople = useCallback((company: ChosenCompany) => {
    setFocusedKey(companyKey(company.name));
    startRun(company, () => searchOptionsRef.current);
  }, [startRun]);

  // A company with no search yet is searched as soon as it is added - the
  // search is the reason it was added - and its filing's officers come with
  // it. Every company is queued, however many: the server runs a few at a
  // time and the rest are shown as queued until there is room. Waits until
  // both the people and the earlier searches are known, or a company searched
  // an hour ago would look unsearched.
  const chosenKeyString = chosen.map((c) => companyKey(c.name)).sort().join('\n');
  useEffect(() => {
    if (busy || !runsReady || peopleFor !== chosenKeyString) return;
    const due = chosen.filter((c) => {
      const key = companyKey(c.name);
      return !autoTried.current.has(key) && (runs[key] || IDLE).state === 'idle';
    });
    if (!due.length) return;
    const timer = window.setTimeout(() => {
      for (const company of due) {
        autoTried.current.add(companyKey(company.name));
        findPeople(company);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [busy, runsReady, peopleFor, chosenKeyString, chosen, runs, findPeople]);

  // What a finished search found goes onto the list by itself: everyone, at
  // their own level, ticked by the default rule. The server decides who it
  // will take and says why it refuses the rest, which stay listed as held.
  // A request that fails is tried again a few times, then left listed with
  // the reason rather than retried forever.
  const importTries = useRef<Map<number, number>>(new Map());
  const importingKeys = useRef<Set<string>>(new Set());
  const [importTick, setImportTick] = useState(0);
  useEffect(() => {
    if (busy || peopleFor !== chosenKeyString) return;
    for (const company of chosen) {
      const key = companyKey(company.name);
      const state = runs[key]?.state;
      if ((state !== 'done' && state !== 'failed') || importingKeys.current.has(key)) continue;
      const pending = foundNotOnFile(people, found[key] || []).filter((p) => !p.skippedReason);
      if (!pending.length) continue;
      const exhausted = pending.filter((p) => (importTries.current.get(p.id) ?? 0) >= IMPORT_TRIES);
      if (exhausted.length) holdFound(key, exhausted.map((p) => p.id), 'could not be added right now; reload the page to try again');
      const ids = pending.filter((p) => !exhausted.includes(p)).map((p) => p.id);
      if (!ids.length) continue;
      for (const id of ids) importTries.current.set(id, (importTries.current.get(id) ?? 0) + 1);
      importingKeys.current.add(key);
      void addFound(key, ids).then((ok) => new Promise<void>((resolve) => {
        // A refused request waits before the next try; a good one goes on at once.
        window.setTimeout(resolve, ok ? 0 : IMPORT_RETRY_MS);
      })).finally(() => {
        importingKeys.current.delete(key);
        setImportTick((n) => n + 1);
      });
    }
  }, [busy, peopleFor, chosenKeyString, chosen, runs, found, people, addFound, holdFound, importTick]);

  const companyLabel = chosen.length <= 2
    ? chosen.map((c) => c.name).join(', ')
    : `${chosen[0].name}, ${chosen[1].name} +${chosen.length - 2} more`;

  return (
    // 21.625rem is the shell around this grid at xl: header, main padding,
    // page header and subnav above; workspace and main padding below. The
    // grid fills the rest exactly so the page never grows a scrollbar of
    // its own beside the rail's and the surface's.
    <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 xl:h-[calc(100vh-21.625rem)]"
         data-section="campaign-pipeline">
      <div data-rail
           className="xl:col-span-2 min-h-0 xl:max-h-full xl:overflow-y-auto surface-card surface-card--flush overflow-hidden self-start">
        <div className="px-5 pb-5 pt-4 space-y-3 border-b border-pale-sky" data-testid="add-companies">
          <div>
            <h2 className="text-[15px] font-semibold text-deep-navy">Add companies</h2>
            <p className="text-xs text-slate-500">
              {chosen.length ? `${chosen.length} added` : 'A name or its LinkedIn page'}
            </p>
          </div>
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
              : 'Each company is searched as soon as it is added. Paste a list, one per line, or several LinkedIn pages at once.'}
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
              <Chip onClick={() => setShowAllChips((v) => !v)}>
                {showAllChips ? 'Show fewer' : `+${hiddenChips} more`}
              </Chip>
            )}
          </div>

          {chosen.length > 0 && people.length === 0 && !busy && (
            <p className="text-sm text-slate-500" data-testid="no-people-note">
              {pausedNotes[0]
                ?? (searchingCount > 0
                  ? `Searching for people at ${searchingCount} compan${searchingCount === 1 ? 'y' : 'ies'}…`
                  : lanes.length > 0 && lanes.every((l) => l.run.state === 'done')
                    ? `Nobody found for ${wantedLabel(levels)} at these companies. Try another level below, or import a spreadsheet.`
                    : 'No people on file at these companies yet.')}
            </p>
          )}

          {/* Who a search looks for is one choice, not a title the member has
              to guess. Titles in their own words are there for anyone who
              wants them. */}
          <div>
            <p className="text-xs font-medium text-slate-600" id="who-to-look-for">Who to look for</p>
            <div role="group" aria-labelledby="who-to-look-for" className="mt-1 flex flex-wrap gap-2">
              {LEVEL_CHOICES.map((id) => (
                <Chip key={id} selected={levels.has(id)} onClick={() => toggleLevel(id)}
                      aria-disabled={levels.size === 1 && levels.has(id) ? true : undefined}
                      title={levels.size === 1 && levels.has(id) ? 'Keep at least one choice' : undefined}>
                  {WANTED_LABEL[id]}
                </Chip>
              ))}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Searching for: {searchTitles || 'any title'}{' · '}
              <button type="button" className="underline" onClick={() => setShowOtherTitles((v) => !v)}>
                {showOtherTitles ? 'Hide other titles' : 'Other titles…'}
              </button>
            </p>
            {showOtherTitles && (
              <input
                className="mt-1 w-full border border-pale-sky px-3 py-2 text-sm text-deep-navy"
                aria-label="Other titles"
                value={titleHints}
                onChange={(e) => setTitleHints(e.target.value)}
                placeholder="In your words, e.g. Head of Partnerships, Producer"
              />
            )}
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600" htmlFor="pipeline-max">How many people</label>
            <div className="mt-1 flex items-center gap-2">
              <input
                id="pipeline-max"
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_PROSPECTS}
                value={maxText}
                onChange={(e) => setMaxText(e.target.value)}
                onBlur={() => setMaxText(String(maxProspects))}
                aria-describedby="pipeline-max-hint"
                className="w-24 border border-pale-sky px-3 py-2 text-sm text-deep-navy"
              />
              <span id="pipeline-max-hint" className="text-xs text-slate-500">
                per company. A search keeps going until it has this many, or the company has no more.
              </span>
            </div>
          </div>
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
        </div>

        <SendPanel
          ticked={ticked}
          recipients={recipients}
          held={heldList}
          heldPeople={tally.held}
          leftOut={tallyReasons({ ...tally, held: 0, canEmail: 0 })}
          searching={searchingCount}
          companyLabel={companyLabel || 'Outreach'}
          draftsHref={recipients.length ? `/studio?${new URLSearchParams({
            companies: [...new Set(recipients.map((r) => (r.company || '').trim()).filter(Boolean))].join(','),
            contact_ids: recipients.map((r) => r.id).join(','),
          })}` : ''}
          onHeld={setHeld}
        />

        {error && <p className="ui-notice ui-notice--danger mx-5 my-4">{error}</p>}
      </div>

      {/* The surface: the lanes, which say where each company's search
          stands, then the one sheet of people they describe. The aside is
          the only thing that scrolls; the lanes stay short and the sheet
          takes whatever is left. */}
      <aside className="xl:col-span-3 min-h-0 xl:overflow-y-auto space-y-4" data-surface>
        {runNotice && (
          <p className="ui-notice ui-notice--info text-sm" role="status">{runNotice}</p>
        )}
        {pausedNotes.map((note) => (
          <p key={note} className="ui-notice ui-notice--warning text-sm" role="status" data-testid="search-paused">{note}</p>
        ))}
        {runError && (
          <p className="ui-notice ui-notice--danger text-sm" role="alert">{runError}</p>
        )}
        <CompanyLanes
          lanes={lanes}
          emailed={tally.canEmail}
          focused={focusedName}
          onFocus={(name) => setFocusedKey(companyKey(name))}
          onExport={(runId) => void exportRun(runId)}
          onDelete={(runId) => {
            if (window.confirm('Delete this search and everything it found?')) void deleteRun(runId);
          }}
        />

        {chosen.length > 0 ? (
          <section className="surface-card surface-card--compact" aria-label="People" data-testid="sheet">
            <RecipientPicker
              companies={chosen}
              people={people}
              selected={selected}
              onSelectedChange={changeSelection}
              found={found}
              runs={runs}
              statuses={statuses}
              tally={tally}
              tallies={tallies}
              held={held}
              wanted={levels}
              onFind={findPeople}
              onFindEverywhere={(company) => {
                setWhere(company.name, '*');
                findPeople({ ...company, targetCountry: '*' });
              }}
              busy={busy}
            />
          </section>
        ) : (
          <section className="surface-card text-sm text-slate-500" aria-label="People">
            Add a company and the people on file there appear here; its search starts at once.
          </section>
        )}
      </aside>
    </div>
  );
}
