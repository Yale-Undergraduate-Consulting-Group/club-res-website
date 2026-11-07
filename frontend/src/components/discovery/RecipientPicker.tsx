import { useMemo, useState } from 'react';
import { type Contact } from '../../api';
import {
  LEVEL_RANK, type Level,
  companyKey, defaultSelection, isWritable, levelOf,
} from '../../lib/recipients';
import {
  fitRank, foundAsPerson, foundNotOnFile, matchesWanted, type CompanyStatus, type Wanted,
} from '../../lib/peopleSheet';
import type { ChosenCompany, CompanyRun, FoundPerson } from '../../lib/useDiscoveryRuns';
import { PersonHeadline, PersonWhy } from './PersonDetails';

/**
 * Who gets the email - chosen, not inherited.
 *
 * This step used to list everyone found at the chosen companies and write to
 * all of them unless you clicked "Drop" one at a time. That is the wrong
 * default for this club: a filing names boards, a crawl names whoever the
 * website mentions, and the member's judgement about which of them is worth an
 * email was the one thing the step would not let them express. So recipients
 * are ticked, the tick is the state that matters, and the count is always on
 * screen.
 *
 * This is the only list of people on the page. A search's results used to be
 * a second table in another panel, imported wholesale the moment the search
 * finished; here they sit under the company they were found at, marked found
 * and not yet on file, and each one is added on purpose.
 *
 * The same sheet serves every step: before people are chosen it previews
 * what the companies come with, while they are chosen it is where the
 * choosing happens, and afterwards it shows exactly who the message goes to.
 */

export type PickerMode = 'preview' | 'select' | 'review';

function shortDate(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : date.toISOString().slice(0, 10);
}

/** Why a finished search stopped short of its target, in plain words. */
const SHORTFALL_REASON: Record<string, string> = {
  exhausted: 'that is everyone the sources list for them right now',
  time_budget: 'the search ran out of time; Find more people carries on',
  round_cap: 'the search used its rounds for this pass; Find more people carries on',
  search_failed: 'web search was failing, so this is not everyone; try Find more people again shortly',
};

export default function RecipientPicker({
  mode, companies, focused, people, selected, onSelectedChange, found, runs, statuses, wanted, onFind, onFindEverywhere, onAddFound, busy,
}: {
  mode: PickerMode;
  companies: ChosenCompany[];
  /** The company the sheet's search action is for: the lane last pointed at. */
  focused: ChosenCompany | null;
  people: Contact[];
  selected: number[];
  onSelectedChange: (ids: number[]) => void;
  /** What each company's search found, keyed by canonical company. */
  found: Record<string, FoundPerson[]>;
  runs: Record<string, CompanyRun>;
  onFind: (company: ChosenCompany) => void;
  /** Search the same company again with no country limit. */
  onFindEverywhere?: (company: ChosenCompany) => void;
  onAddFound: (companyKey: string, ids: number[] | 'all') => Promise<void>;
  /** What each company's search amounts to, keyed by canonical company. */
  statuses: Record<string, CompanyStatus>;
  /** Who to look for: the people at this level are shown first, the rest fold. */
  wanted: Wanted;
  busy: boolean;
}) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState<'all' | Level>('all');
  const [hideWritten, setHideWritten] = useState(true);
  const [adding, setAdding] = useState<Record<string, true>>({});
  // Companies whose people at other levels are unfolded, by key.
  const [showOthers, setShowOthers] = useState<Record<string, boolean>>({});
  // Folded companies, by key. A fold hides the rows only - the header keeps
  // the count, the tick-all box and the search, so nothing is decided blind.
  const [folded, setFolded] = useState<Record<string, true>>({});
  const toggleFold = (key: string) => setFolded((prev) => {
    const next = { ...prev };
    if (next[key]) delete next[key]; else next[key] = true;
    return next;
  });

  const chosen = useMemo(() => new Set(selected), [selected]);

  const visible = useMemo(() => {
    if (mode === 'review') {
      return people.filter((p) => isWritable(p) && chosen.has(p.id))
        .sort((a, b) => LEVEL_RANK[levelOf(a)] - LEVEL_RANK[levelOf(b)]
          || (a.name || a.email || '').localeCompare(b.name || b.email || ''));
    }
    const filtering = mode === 'select';
    const term = filtering ? query.trim().toLowerCase() : '';
    return people
      .filter((p) => (!filtering || level === 'all' ? true : levelOf(p) === level))
      // A person already written to is hidden, never silently unselectable:
      // the toggle says how many are behind it.
      .filter((p) => (filtering && hideWritten ? !p.last_sent_at : true))
      .filter((p) => !term
        || `${p.name || ''} ${p.title || ''} ${p.email || ''} ${p.company || ''}`.toLowerCase().includes(term))
      .sort((a, b) => fitRank(a, wanted) - fitRank(b, wanted)
        || (a.name || a.email || '').localeCompare(b.name || b.email || ''));
  }, [mode, people, chosen, query, level, hideWritten, wanted]);

  const groups = useMemo(() => {
    const map = new Map<string, { company: ChosenCompany; rows: Contact[]; found: FoundPerson[] }>();
    // Every chosen company keeps a group even with nobody on file, because
    // that group is where its search lives. What a search found leaves its
    // list once the same person is on file, by address or by name.
    for (const company of companies) {
      const key = companyKey(company.name);
      if (!map.has(key)) {
        const fresh = mode === 'review' || !(found[key] || []).length ? [] : foundNotOnFile(people, found[key]);
        map.set(key, {
          company,
          rows: [],
          found: [...fresh].sort((a, b) => fitRank(foundAsPerson(a), wanted) - fitRank(foundAsPerson(b), wanted)),
        });
      }
    }
    for (const person of visible) {
      const key = companyKey(person.company) || 'no company';
      if (!map.has(key)) map.set(key, { company: { name: person.company || 'No company' }, rows: [], found: [] });
      map.get(key)!.rows.push(person);
    }
    const entries = [...map.entries()];
    // In review, a company with nobody going to it is not a recipient group.
    return mode === 'review' ? entries.filter(([, g]) => g.rows.length > 0) : entries;
  }, [visible, companies, found, people, mode, wanted]);

  const writableVisible = visible.filter(isWritable);
  const hiddenWritten = people.filter((p) => p.last_sent_at).length;
  const noAddress = people.filter((p) => !isWritable(p)).length;
  const selectable = mode === 'select';
  // The sheet's one search action, for the focused company. It used to sit
  // in every company's group header, small and among the counts, where
  // members looking at the people a search just brought back missed it.
  const focusKey = focused ? companyKey(focused.name) : '';
  const focusRun = focused ? runs[focusKey] : undefined;
  const focusSearching = focusRun && (focusRun.state === 'searching' || focusRun.state === 'queued');
  const focusHasPeople = Boolean(focused) && (
    people.some((p) => companyKey(p.company) === focusKey) || (found[focusKey] || []).length > 0);

  const setMany = (ids: number[], on: boolean) => {
    const next = new Set(chosen);
    for (const id of ids) {
      if (on) next.add(id); else next.delete(id);
    }
    onSelectedChange([...next]);
  };

  const toggleOne = (person: Contact) => {
    if (!isWritable(person)) return;
    setMany([person.id], !chosen.has(person.id));
  };

  const add = async (key: string, ids: number[] | 'all') => {
    setAdding((prev) => ({ ...prev, [key]: true }));
    try {
      await onAddFound(key, ids);
    } finally {
      setAdding((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  return (
    <div className="space-y-3" data-testid="recipient-picker" data-mode={mode}>
      {/* The count is the first thing on the sheet, because it is the answer
          to the only question it asks. Sticky against the surface, which is
          the one thing that scrolls. */}
      <div className="sticky top-0 z-10 -mx-4 border-b border-pale-sky bg-white/95 px-4 py-2 backdrop-blur"
           data-testid="sheet-header">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-sm font-semibold text-deep-navy" data-testid="selected-count">
            {mode === 'review'
              ? `${visible.length} recipient${visible.length === 1 ? '' : 's'}`
              : `${selected.length} of ${people.filter(isWritable).length} selected`}
          </span>
          {mode === 'preview' && (
            <span className="text-xs text-slate-500">
              {people.length} on file at {companies.length} compan{companies.length === 1 ? 'y' : 'ies'}
            </span>
          )}
          {selectable && (
            <>
              <button type="button" className="text-xs underline text-deep-navy"
                      onClick={() => setMany(writableVisible.map((p) => p.id), true)}>
                Select all shown{visible.length !== people.length ? ` (${writableVisible.length})` : ''}
              </button>
              <button type="button" className="text-xs underline text-slate-500"
                      onClick={() => onSelectedChange([])}>
                Clear
              </button>
              <button type="button" className="text-xs underline text-slate-500"
                      onClick={() => onSelectedChange(defaultSelection(people))}>
                Reset to suggested
              </button>
            </>
          )}
          {busy && <span className="text-xs text-slate-500">Loading…</span>}
          {focused && mode !== 'review' && (
            <button
              type="button"
              disabled={Boolean(focusSearching)}
              onClick={() => onFind(focused)}
              className="ui-button ui-button--primary ui-button--sm ml-auto max-w-full truncate"
              data-testid="sheet-find"
              title={focusHasPeople ? `Search ${focused.name} again for people not on file yet` : `Search ${focused.name} for people`}
            >
              {focusSearching
                ? (focusRun?.state === 'queued' ? `Queued: ${focused.name}` : `Searching ${focused.name}…`)
                : `${focusHasPeople ? 'Find more people' : 'Find people'} at ${focused.name}`}
            </button>
          )}
        </div>
      </div>

      {selectable && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-slate-600">
            Search these people
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name, title or address"
              className="mt-1 block w-56 rounded-lg border border-pale-sky px-3 py-2 text-sm text-deep-navy"
            />
          </label>
          <label className="text-xs text-slate-600">
            Seniority
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value as 'all' | Level)}
              className="mt-1 block rounded-lg border border-pale-sky bg-white px-3 py-2 text-sm text-deep-navy"
            >
              <option value="all">Anyone found</option>
              <option value="working">Working level (the people who answer)</option>
              <option value="executive">Executives</option>
              <option value="board">Board seats</option>
              <option value="unknown">Role unclear</option>
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-xs text-slate-600">
            <input type="checkbox" checked={hideWritten} onChange={(e) => setHideWritten(e.target.checked)} />
            Hide the {hiddenWritten} already written to
          </label>
          {noAddress > 0 && (
            <span className="pb-2 text-xs text-amber-800">
              {noAddress} without an address cannot be written to
            </span>
          )}
        </div>
      )}

      {groups.map(([key, group]) => {
        const { company, rows } = group;
        const writable = rows.filter(isWritable);
        const allOn = writable.length > 0 && writable.every((p) => chosen.has(p.id));
        const run = runs[key];
        const searching = run && (run.state === 'searching' || run.state === 'queued');
        // Who matches what the member is looking for comes first; everyone
        // else - another level, the very senior, a name that is page text -
        // folds behind one count. A search at a large company otherwise opens
        // on its CEO, CFO and COO. A narrowed list or a typed search is the
        // member asking to see more, so nothing folds then.
        const collapsing = mode !== 'review' && level === 'all' && !query.trim();
        const open = Boolean(showOthers[key]);
        const restRows = collapsing ? rows.filter((p) => !matchesWanted(p, wanted)) : [];
        const restFound = collapsing ? group.found.filter((p) => !matchesWanted(foundAsPerson(p), wanted)) : [];
        const foldedAway = new Set<unknown>([...restRows, ...restFound]);
        const shownRows = open ? rows : rows.filter((p) => !foldedAway.has(p));
        const shownFound = open ? group.found : group.found.filter((p) => !foldedAway.has(p));
        const restTicked = restRows.filter((p) => chosen.has(p.id)).length;
        const addable = group.found.filter((p) => !p.skippedReason && matchesWanted(foundAsPerson(p), wanted));
        const status = statuses[key];
        const peopleHere = rows.length + group.found.length;
        // The run's own words are progress while it works, and the skipped
        // note afterwards; once it is over, the status line says what it came to.
        const showRunMessage = Boolean(run) && (searching
          || (run?.state === 'done' && !run.paused && (peopleHere > 0 || /skipped/i.test(run.message || ''))));
        const isFolded = Boolean(folded[key]);
        const bodyId = `group-${key.replace(/[^a-z0-9]+/gi, '-')}`;
        return (
          <div key={key} data-company={company.name} data-folded={isFolded || undefined}
               className="overflow-hidden rounded-xl border border-pale-sky">
            <div className="flex flex-wrap items-center justify-between gap-2 bg-pale-sky/30 px-3 py-2">
              <span className="flex min-w-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => toggleFold(key)}
                  aria-expanded={!isFolded}
                  aria-controls={bodyId}
                  aria-label={`${isFolded ? 'Show' : 'Hide'} people at ${company.name}`}
                  className="ui-button ui-button--ghost ui-button--sm ui-button--icon"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"
                       className={`transition-transform ${isFolded ? '-rotate-90' : ''}`}>
                    <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {selectable ? (
                  <label className="flex items-center gap-2 text-sm font-semibold text-deep-navy">
                    <input
                      type="checkbox"
                      checked={allOn}
                      disabled={writable.length === 0}
                      ref={(el) => {
                        if (el) el.indeterminate = !allOn && writable.some((p) => chosen.has(p.id));
                      }}
                      onChange={() => setMany(writable.map((p) => p.id), !allOn)}
                      aria-label={`Select everyone at ${company.name}`}
                    />
                    {company.name}
                  </label>
                ) : (
                  <span className="text-sm font-semibold text-deep-navy">{company.name}</span>
                )}
              </span>
              <span className="text-xs text-slate-500">
                {mode === 'review'
                  ? `${rows.length} recipient${rows.length === 1 ? '' : 's'}`
                  : `${rows.length} on file · ${writable.filter((p) => chosen.has(p.id)).length} selected${
                    group.found.length ? ` · ${group.found.length} found` : ''}`}
              </span>
            </div>

            {mode !== 'review' && status && (
              <p className={`border-t border-pale-sky px-3 py-1.5 text-xs ${status.tone === 'paused' ? 'text-amber-900' : 'text-slate-600'}`}
                 data-testid="company-status" data-tone={status.tone}>
                {status.long}
              </p>
            )}

            {mode !== 'review' && run && run.state !== 'idle'
              && (showRunMessage || (run.state === 'done' && run.shortfall)) && (
              <div className="border-t border-pale-sky px-3 py-2" role="status">
                {showRunMessage && (
                  <p className="text-xs text-slate-600">
                    {run.message}
                    {/* A search that skipped people for being elsewhere offers
                        them back in one click, rather than hiding a choice. */}
                    {selectable && onFindEverywhere && run.state === 'done' && /skipped/i.test(run.message || '')
                      && company.targetCountry !== '*' && (
                      <button type="button" onClick={() => onFindEverywhere(company)}
                              className="ml-2 underline text-deep-navy">
                        Search all countries
                      </button>
                    )}
                  </p>
                )}
                {run.state === 'done' && run.shortfall && (
                  <p className="mt-1 text-xs text-amber-900" data-testid="shortfall">
                    Only {run.shortfall.found} qualified {run.shortfall.found === 1 ? 'person' : 'people'} found
                    at {company.name}
                    {SHORTFALL_REASON[run.shortfall.reason] ? ` — ${SHORTFALL_REASON[run.shortfall.reason]}.` : '.'}
                  </p>
                )}
                {searching && (
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-pale-sky/70"
                       role="progressbar" aria-valuenow={Math.round(run.pct)} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-full bg-[var(--btn-primary-bg)] transition-[width] duration-300"
                         style={{ width: `${Math.min(100, Math.max(4, run.pct))}%` }} />
                  </div>
                )}
              </div>
            )}

            {/* Hidden, not unmounted: unfolding is instant and loses nothing. */}
            <div id={bodyId} hidden={isFolded}>
              {peopleHere === 0 && !searching && mode !== 'review' && (
                <p className="px-3 py-2 text-sm text-slate-500">
                  {selectable && (query.trim() || level !== 'all')
                    ? 'Nobody matches your filters. Widen them above.'
                    : 'Nobody on file here yet. Use Find people to search, or import a list.'}
                </p>
              )}

              <ul className="divide-y divide-pale-sky">
                {shownRows.map((person) => {
                  const writableRow = isWritable(person);
                  const on = chosen.has(person.id);
                  return (
                    <li key={person.id} className="flex items-start gap-3 px-3 py-2 text-sm">
                      {mode !== 'review' && (
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={on}
                          disabled={!selectable || !writableRow}
                          onChange={() => toggleOne(person)}
                          aria-label={`Write to ${person.name || person.email}`}
                        />
                      )}
                      <span className="min-w-0 flex-1">
                        <PersonHeadline name={person.name || person.email} person={person} company={company.name} />
                        <PersonWhy person={person} wanted={wanted}>
                          {writableRow
                            ? <span className="truncate">{person.email}</span>
                            : <span className="text-amber-800">no address yet</span>}
                          {person.last_sent_at && (
                            <span>written {shortDate(person.last_sent_at)}
                              {person.last_campaign_name ? ` · ${person.last_campaign_name}` : ''}</span>
                          )}
                        </PersonWhy>
                      </span>
                    </li>
                  );
                })}
              </ul>

              {/* Found, not on file: what an earlier visit's search found, and
                  anyone a search left for a deliberate add. */}
              {group.found.length > 0 && (
                <div className="border-t border-pale-sky" data-testid="found-rows">
                  <div className="flex items-center justify-between gap-2 bg-amber-50/60 px-3 py-1.5 text-xs text-amber-900">
                    <span>{group.found.length} found · not on file yet</span>
                    {selectable && addable.length > 1 && (
                      <button
                        type="button"
                        disabled={Boolean(adding[key])}
                        onClick={() => void add(key, addable.map((p) => p.id))}
                        className="ui-button ui-button--secondary ui-button--sm"
                      >
                        {adding[key] ? 'Adding…' : `Add all ${addable.length} found`}
                      </button>
                    )}
                  </div>
                  <ul className="divide-y divide-pale-sky">
                    {shownFound.map((person) => {
                      const name = [person.first_name, person.last_name].filter(Boolean).join(' ') || person.email;
                      return (
                        <li key={person.id} className="flex items-start gap-3 px-3 py-2 text-sm" data-found={person.id}>
                          <span className="min-w-0 flex-1">
                            <PersonHeadline name={name || ''} person={foundAsPerson(person)} company={company.name} />
                            <PersonWhy person={foundAsPerson(person)} wanted={wanted}>
                              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-800">found</span>
                              {person.groupUnit && (
                                <span className="rounded-full bg-pale-sky/60 px-2 py-0.5 text-slate-600" data-testid="group-unit"
                                      title={`Works at ${person.groupUnit}, part of ${company.name}`}>
                                  {person.groupUnit}
                                </span>
                              )}
                              <span className="truncate">{person.email}</span>
                              {person.skippedReason && (
                                <span className="text-amber-900">not added: {person.skippedReason}</span>
                              )}
                            </PersonWhy>
                          </span>
                          {selectable && !person.skippedReason && (
                            <button
                              type="button"
                              disabled={Boolean(adding[key])}
                              onClick={() => void add(key, [person.id])}
                              className="ui-button ui-button--secondary ui-button--sm shrink-0"
                              aria-label={`Add ${name}`}
                            >
                              Add
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {restRows.length + restFound.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowOthers((prev) => ({ ...prev, [key]: !prev[key] }))}
                  aria-expanded={open}
                  className="w-full border-t border-pale-sky px-3 py-2 text-left text-xs text-slate-600 underline"
                  data-testid="show-others"
                >
                  {open
                    ? 'Show fewer: only the people who match'
                    : `Show ${restRows.length + restFound.length} more: other levels, very senior or unclear names${
                      restTicked ? ` (${restTicked} ticked)` : ''}`}
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
