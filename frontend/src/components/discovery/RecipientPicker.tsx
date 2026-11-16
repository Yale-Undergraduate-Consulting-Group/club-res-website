import { useCallback, useMemo, useState } from 'react';
import { type Contact } from '../../api';
import {
  FUNCTIONS, type Fn, type Level,
  companyKey, defaultSelection, functionOf, isWritable, levelOf,
} from '../../lib/recipients';
import { foundAsPerson, sheetOrder, type CompanyStatus, type Wanted } from '../../lib/peopleSheet';
import { tallyReasons, type Tally } from '../../lib/sendPlan';
import type { ChosenCompany, CompanyRun, FoundPerson } from '../../lib/useDiscoveryRuns';
import { PersonHeadline, PersonWhy } from './PersonDetails';

/**
 * Who gets the email - chosen, not inherited, and all of it on one list.
 *
 * A filing names boards, a crawl names whoever the website mentions, and the
 * member's judgement about which of them is worth an email is the one thing
 * this list must let them express. So recipients are ticked, and the page's
 * one number - how many can be emailed - is the first thing on the sheet,
 * with the reasons for everyone it leaves out beside it.
 *
 * Everyone a search finds is on this list as soon as it arrives: a search's
 * people are added to the club's contacts automatically, ticked by the default
 * rule (people at a level asked for, or a filed officer; not a board seat
 * unless Board is asked for, someone too senior to answer, or a name that is
 * page text). The filters here only narrow what is
 * shown - the number above them never changes with them.
 */

function shortDate(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : date.toISOString().slice(0, 10);
}

/** Why a finished search stopped short of its target, in plain words. */
const SHORTFALL_REASON: Record<string, string> = {
  exhausted: 'that is everyone the sources list for them right now',
  time_budget: 'the search ran out of time; Search again carries on',
  round_cap: 'the search used its rounds for this pass; Search again carries on',
  search_failed: 'web search was failing, so this is not everyone; try Search again shortly',
};

type Shown = { name?: string | null; title?: string | null; email?: string | null; company?: string | null; person_level?: unknown; person_function?: unknown };

export default function RecipientPicker({
  companies, people, selected, onSelectedChange, found, runs, statuses, tally, tallies, held, wanted,
  onFind, onFindEverywhere, busy,
}: {
  companies: ChosenCompany[];
  people: Contact[];
  selected: number[];
  onSelectedChange: (ids: number[]) => void;
  /** Found people not on file yet, keyed by canonical company: still arriving,
   *  or refused by the club's contacts with the reason. */
  found: Record<string, FoundPerson[]>;
  runs: Record<string, CompanyRun>;
  /** Search a company again: the one search action on the page. */
  onFind: (company: ChosenCompany) => void;
  /** Search the same company again with no country limit. */
  onFindEverywhere?: (company: ChosenCompany) => void;
  /** What each company's search amounts to, keyed by canonical company. */
  statuses: Record<string, CompanyStatus>;
  /** The page's one count, overall and per company. */
  tally: Tally;
  tallies: Record<string, Tally>;
  /** Ticked people the build would not write to, with why. */
  held: ReadonlyMap<number, string>;
  /** Who to look for: people at these levels are listed first. */
  wanted: Wanted;
  busy: boolean;
}) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState<'all' | Level>('all');
  const [fn, setFn] = useState<'all' | Fn>('all');
  const [hideWritten, setHideWritten] = useState(false);
  // Folded companies, by key. A fold hides the rows only - the header keeps
  // the count, the tick-all box and the search, so nothing is decided blind.
  const [folded, setFolded] = useState<Record<string, true>>({});
  const toggleFold = (key: string) => setFolded((prev) => {
    const next = { ...prev };
    if (next[key]) delete next[key]; else next[key] = true;
    return next;
  });

  const chosen = useMemo(() => new Set(selected), [selected]);
  const term = query.trim().toLowerCase();
  const filtering = level !== 'all' || fn !== 'all' || Boolean(term) || hideWritten;
  // Filters narrow the view and nothing else: the tally above is of everyone.
  const passes = useCallback((p: Shown) => (level === 'all' || levelOf(p) === level)
    && (fn === 'all' || functionOf(p) === fn)
    && (!term || `${p.name || ''} ${p.title || ''} ${p.email || ''} ${p.company || ''}`.toLowerCase().includes(term)),
  [level, fn, term]);

  const visible = useMemo(() => people
    .filter(passes)
    .filter((p) => (hideWritten ? !p.last_sent_at : true))
    .sort((a, b) => sheetOrder(a, b, wanted)
      || (a.name || a.email || '').localeCompare(b.name || b.email || '')),
  [people, passes, hideWritten, wanted]);

  const groups = useMemo(() => {
    const map = new Map<string, { company: ChosenCompany; rows: Contact[]; found: FoundPerson[] }>();
    // Every chosen company keeps a group even with nobody on file, because
    // that group is where its search lives.
    for (const company of companies) {
      const key = companyKey(company.name);
      if (!map.has(key)) {
        const pending = (found[key] || []).filter((p) => passes(foundAsPerson(p)));
        map.set(key, {
          company,
          rows: [],
          found: [...pending].sort((a, b) => sheetOrder(foundAsPerson(a), foundAsPerson(b), wanted)),
        });
      }
    }
    for (const person of visible) {
      const key = companyKey(person.company) || 'no company';
      if (!map.has(key)) map.set(key, { company: { name: person.company || 'No company' }, rows: [], found: [] });
      map.get(key)!.rows.push(person);
    }
    return [...map.entries()];
  }, [visible, companies, found, wanted, passes]);

  const writableVisible = visible.filter(isWritable);
  const hiddenWritten = people.filter((p) => p.last_sent_at).length;
  const chosenKeys = useMemo(() => new Set(companies.map((c) => companyKey(c.name))), [companies]);

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

  const reasons = tallyReasons(tally);
  const listed = visible.length + groups.reduce((n, [, g]) => n + g.found.length, 0);

  return (
    <div className="space-y-3" data-testid="recipient-picker">
      {/* The count is the first thing on the sheet, because it is the answer
          to the only question it asks. Sticky against the surface, which is
          the one thing that scrolls. */}
      <div className="sticky top-0 z-10 -mx-4 border-b border-pale-sky bg-white/95 px-4 py-2 backdrop-blur"
           data-testid="sheet-header">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-sm font-semibold text-deep-navy" data-testid="selected-count">
            {tally.canEmail} can be emailed
          </span>
          {reasons && (
            <span className="text-xs text-slate-600" data-testid="send-reasons">{reasons}</span>
          )}
          <button type="button" className="text-xs underline text-deep-navy"
                  onClick={() => setMany(writableVisible.map((p) => p.id), true)}>
            Select all shown{filtering ? ` (${writableVisible.length})` : ''}
          </button>
          <button type="button" className="text-xs underline text-slate-500"
                  onClick={() => onSelectedChange([])}>
            Clear
          </button>
          <button type="button" className="text-xs underline text-slate-500"
                  onClick={() => onSelectedChange(defaultSelection(people, wanted))}>
            Reset to suggested
          </button>
          {busy && <span className="text-xs text-slate-500">Loading…</span>}
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-600">
          Search these people
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Name, title or address"
            className="mt-1 block w-56 border border-pale-sky px-3 py-2 text-sm text-deep-navy"
          />
        </label>
        <label className="text-xs text-slate-600">
          Seniority
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as 'all' | Level)}
            className="mt-1 block border border-pale-sky bg-white px-3 py-2 text-sm text-deep-navy"
          >
            <option value="all">Anyone found</option>
            <option value="working">Working level (the people who answer)</option>
            <option value="executive">Executives</option>
            <option value="board">Board seats</option>
            <option value="unknown">Role unclear</option>
          </select>
        </label>
        <label className="text-xs text-slate-600">
          Function
          <select
            value={fn}
            onChange={(e) => setFn(e.target.value as 'all' | Fn)}
            className="mt-1 block border border-pale-sky bg-white px-3 py-2 text-sm text-deep-navy"
            data-testid="function-filter"
          >
            <option value="all">Any function</option>
            {FUNCTIONS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 pb-2 text-xs text-slate-600">
          <input type="checkbox" checked={hideWritten} onChange={(e) => setHideWritten(e.target.checked)} />
          Hide the {hiddenWritten} already written to
        </label>
        {filtering && (
          <span className="pb-2 text-xs text-deep-navy" data-testid="filter-note"
                title="The filters only narrow the list, not who is sent to">
            Showing {listed} of {tally.total}
          </span>
        )}
      </div>

      {groups.map(([key, group]) => {
        const { company, rows } = group;
        const writable = rows.filter(isWritable);
        const allOn = writable.length > 0 && writable.every((p) => chosen.has(p.id));
        const run = runs[key];
        const searching = run && (run.state === 'searching' || run.state === 'queued');
        const settled = run?.state === 'done' || run?.state === 'failed';
        const status = statuses[key];
        const companyTally = tallies[key];
        const peopleHere = rows.length + group.found.length;
        // The run's own words are progress while it works, and the skipped
        // note afterwards; once it is over, the status line says what it came to.
        const showRunMessage = Boolean(run) && (searching
          || (run?.state === 'done' && !run.paused && (peopleHere > 0 || /skipped/i.test(run.message || ''))));
        const isFolded = Boolean(folded[key]);
        const bodyId = `group-${key.replace(/[^a-z0-9]+/gi, '-')}`;
        return (
          <div key={key} data-company={company.name} data-folded={isFolded || undefined}
               className="overflow-hidden border border-pale-sky">
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
              </span>
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-500" data-testid="group-count">
                  {companyTally ? `${companyTally.canEmail} can be emailed` : ''}
                  {companyTally ? ` · ${companyTally.total} listed` : ''}
                </span>
                {chosenKeys.has(key) && (
                  <button
                    type="button"
                    disabled={Boolean(searching)}
                    onClick={() => onFind(company)}
                    className="ui-button ui-button--secondary ui-button--sm"
                    data-testid="company-search"
                    title={`Search ${company.name}${run?.state === 'idle' ? '' : ' again'} for people not on file yet`}
                  >
                    {run?.state === 'queued' ? 'Queued' : run?.state === 'searching' ? 'Searching…' : run?.state === 'idle' ? 'Search' : 'Search again'}
                  </button>
                )}
              </span>
            </div>

            {status && (
              <p className={`border-t border-pale-sky px-3 py-1.5 text-xs ${status.tone === 'paused' ? 'text-deep-navy' : 'text-slate-600'}`}
                 data-testid="company-status" data-tone={status.tone}>
                {status.long}
              </p>
            )}

            {/* A teammate's recent search stands in for a new one: say whose it is. */}
            {run?.state === 'done' && run.sharedFrom && (
              <p className="border-t border-pale-sky px-3 py-1.5 text-xs text-slate-600" data-testid="shared-run">
                Found by {run.sharedFrom.name}
                {shortDate(run.sharedFrom.at) ? ` · ${shortDate(run.sharedFrom.at)}` : ''}
              </p>
            )}

            {run && run.state !== 'idle'
              && (showRunMessage || (run.state === 'done' && run.shortfall)) && (
              <div className="border-t border-pale-sky px-3 py-2" role="status">
                {showRunMessage && (
                  <p className="text-xs text-slate-600">
                    {run.message}
                    {/* A search that skipped people for being elsewhere offers
                        them back in one click, rather than hiding a choice. */}
                    {onFindEverywhere && run.state === 'done' && /skipped/i.test(run.message || '')
                      && company.targetCountry !== '*' && (
                      <button type="button" onClick={() => onFindEverywhere(company)}
                              className="ml-2 underline text-deep-navy">
                        Search all countries
                      </button>
                    )}
                  </p>
                )}
                {run.state === 'done' && run.shortfall && (
                  <p className="mt-1 text-xs text-deep-navy" data-testid="shortfall">
                    Only {run.shortfall.found} qualified {run.shortfall.found === 1 ? 'person' : 'people'} found
                    at {company.name}
                    {SHORTFALL_REASON[run.shortfall.reason] ? ` — ${SHORTFALL_REASON[run.shortfall.reason]}.` : '.'}
                  </p>
                )}
                {searching && (
                  <div className="mt-1 h-1.5 overflow-hidden bg-pale-sky/70"
                       role="progressbar" aria-valuenow={Math.round(run.pct)} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-full bg-[var(--btn-primary-bg)] transition-[width] duration-300"
                         style={{ width: `${Math.min(100, Math.max(4, run.pct))}%` }} />
                  </div>
                )}
              </div>
            )}

            {/* Hidden, not unmounted: unfolding is instant and loses nothing. */}
            <div id={bodyId} hidden={isFolded}>
              {peopleHere === 0 && !searching && (
                <p className="px-3 py-2 text-sm text-slate-500">
                  {filtering ? 'Nobody matches your filters. Widen them above.' : 'Nobody here yet.'}
                </p>
              )}

              <ul className="divide-y divide-pale-sky">
                {rows.map((person) => {
                  const writableRow = isWritable(person);
                  const on = chosen.has(person.id);
                  const holdReason = on ? held.get(person.id) : undefined;
                  return (
                    <li key={person.id} className="flex items-start gap-3 px-3 py-2 text-sm">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={on}
                        disabled={!writableRow}
                        onChange={() => toggleOne(person)}
                        aria-label={`Write to ${person.name || person.email}`}
                      />
                      <span className="min-w-0 flex-1">
                        <PersonHeadline name={person.name || person.email} person={person} company={company.name} />
                        <PersonWhy person={person} wanted={wanted}>
                          {writableRow
                            ? <span className="truncate" title={person.email}>{person.email}</span>
                            : <span className="text-deep-navy">no address yet</span>}
                          {person.last_sent_at && (
                            <span>written {shortDate(person.last_sent_at)}
                              {person.last_campaign_name ? ` · ${person.last_campaign_name}` : ''}</span>
                          )}
                          {holdReason && (
                            <span className="text-deep-navy" data-testid="held-reason">held: {holdReason}</span>
                          )}
                        </PersonWhy>
                      </span>
                    </li>
                  );
                })}
              </ul>

              {/* Found and on their way to the list, or refused by the club's
                  contacts: never dropped, so the count above can be reconciled
                  with what is here. */}
              {group.found.length > 0 && (
                <ul className="divide-y divide-pale-sky border-t border-pale-sky" data-testid="found-rows">
                  {group.found.map((person) => {
                    const name = [person.first_name, person.last_name].filter(Boolean).join(' ') || person.email;
                    return (
                      <li key={person.id} className="flex items-start gap-3 px-3 py-2 text-sm" data-found={person.id}>
                        <span className="min-w-0 flex-1">
                          <PersonHeadline name={name || ''} person={foundAsPerson(person)} company={company.name} />
                          <PersonWhy person={foundAsPerson(person)} wanted={wanted}>
                            {person.groupUnit && (
                              <span className="bg-pale-sky/60 px-2 py-0.5 text-slate-600" data-testid="group-unit"
                                    title={`Works at ${person.groupUnit}, part of ${company.name}`}>
                                {person.groupUnit}
                              </span>
                            )}
                            <span className="truncate" title={person.email ?? undefined}>{person.email}</span>
                            {person.skippedReason
                              ? <span className="text-deep-navy">held: {person.skippedReason}</span>
                              : <span className="bg-pale-sky/60 px-2 py-0.5 text-deep-navy">
                                {settled ? 'adding…' : 'found · added when the search finishes'}
                              </span>}
                          </PersonWhy>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
