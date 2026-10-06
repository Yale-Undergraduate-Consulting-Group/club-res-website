import { useMemo, useState } from 'react';
import { type Contact } from '../../api';
import {
  LEVEL_LABEL, LEVEL_RANK, type Level,
  companyKey, defaultSelection, isTooSenior, isWritable, levelOf, looksLikePerson,
} from '../../lib/recipients';
import type { ChosenCompany, CompanyRun, FoundPerson } from '../../lib/useDiscoveryRuns';

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

export default function RecipientPicker({
  mode, companies, people, selected, onSelectedChange, found, runs, onFind, onFindEverywhere, onAddFound, busy,
}: {
  mode: PickerMode;
  companies: ChosenCompany[];
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
  busy: boolean;
}) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState<'all' | Level>('all');
  const [hideWritten, setHideWritten] = useState(true);
  const [adding, setAdding] = useState<Record<string, true>>({});
  const [showHeld, setShowHeld] = useState<Record<string, boolean>>({});
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
      .sort((a, b) => LEVEL_RANK[levelOf(a)] - LEVEL_RANK[levelOf(b)]
        || (a.name || a.email || '').localeCompare(b.name || b.email || ''));
  }, [mode, people, chosen, query, level, hideWritten]);

  // Addresses already on file, so a found person who has since been added
  // (or was on file all along) is not listed twice.
  const onFileEmails = useMemo(
    () => new Set(people.map((p) => (p.email || '').trim().toLowerCase()).filter(Boolean)),
    [people],
  );

  const groups = useMemo(() => {
    const map = new Map<string, { company: ChosenCompany; rows: Contact[]; found: FoundPerson[] }>();
    // Every chosen company keeps a group even with nobody on file, because
    // that group is where its search lives.
    for (const company of companies) {
      const key = companyKey(company.name);
      if (!map.has(key)) {
        map.set(key, {
          company,
          rows: [],
          found: mode === 'review' ? [] : (found[key] || []).filter((p) => !onFileEmails.has((p.email || '').trim().toLowerCase())),
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
  }, [visible, companies, found, onFileEmails, mode]);

  const writableVisible = visible.filter(isWritable);
  const hiddenWritten = people.filter((p) => p.last_sent_at).length;
  const noAddress = people.filter((p) => !isWritable(p)).length;
  const selectable = mode === 'select';

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
        // The people worth writing to first; the very senior and rows whose
        // name is page text fold behind one link. A search at a large company
        // otherwise opens on its CEO, CFO and COO.
        const asContact = (p: FoundPerson) => ({
          name: [p.first_name, p.last_name].filter(Boolean).join(' '), title: p.title,
        } as Contact);
        const held = group.found.filter((p) => isTooSenior(asContact(p)) || !looksLikePerson(asContact(p)));
        const likely = group.found.filter((p) => !held.includes(p));
        const shownFound = showHeld[key] ? group.found : likely;
        const addable = likely.filter((p) => !p.skippedReason);
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
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-600 hover:bg-pale-sky/70"
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
              <span className="flex items-center gap-3 text-xs text-slate-500">
                {mode === 'review'
                  ? `${rows.length} recipient${rows.length === 1 ? '' : 's'}`
                  : `${rows.length} on file · ${writable.filter((p) => chosen.has(p.id)).length} selected${
                    group.found.length ? ` · ${group.found.length} found` : ''}`}
                {selectable && (
                  <button
                    type="button"
                    disabled={Boolean(searching)}
                    onClick={() => onFind(company)}
                    className={`ui-button ui-button--sm ${
                      rows.length === 0 && group.found.length === 0 ? 'ui-button--primary' : 'ui-button--secondary'}`}
                    title={`Search ${company.name} again for people not on file yet`}
                  >
                    {searching ? (run.state === 'queued' ? 'Queued' : 'Searching…')
                      : rows.length === 0 && group.found.length === 0 ? 'Find people here' : '+ Find more people'}
                  </button>
                )}
              </span>
            </div>

            {selectable && run && run.state !== 'idle' && (
              <div className="border-t border-pale-sky px-3 py-2" role="status">
                <p className={`text-xs ${run.state === 'failed' ? 'text-amber-900' : 'text-slate-600'}`}>
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
                  {run.state === 'failed' && group.found.length ? ' · what it found so far is below' : ''}
                </p>
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
              {rows.length === 0 && group.found.length === 0 && !searching && mode !== 'review' && (
                <p className="px-3 py-2 text-sm text-slate-500">
                  {selectable
                    ? 'Nobody here matches what you are looking at. Search this company, import a list, or widen the filters above.'
                    : 'Nobody on file here yet.'}
                </p>
              )}

              <ul className="divide-y divide-pale-sky">
                {rows.map((person) => {
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
                        <span className="font-medium text-deep-navy">{person.name || person.email}</span>
                        <span className="text-slate-500"> · {person.title || 'no title'}</span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          {/* The title outranks the stored level: a CFO whose
                              filing title reads as working level is still a CFO. */}
                          {!looksLikePerson(person) ? (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-900"
                                  title="This name looks like page text a search picked up, not a person, so it is not ticked by default">
                              Not a person? Check the name
                            </span>
                          ) : isTooSenior(person) ? (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-900"
                                  title="Chief officers, presidents and senior vice presidents rarely answer a student's cold email, so they are not ticked by default">
                              Very senior · rarely replies
                            </span>
                          ) : (
                            <span className={`rounded-full px-2 py-0.5 ${
                              levelOf(person) === 'working' ? 'bg-emerald-50 text-emerald-800'
                                : levelOf(person) === 'board' ? 'bg-amber-50 text-amber-800'
                                  : 'bg-pale-sky/60 text-deep-navy'}`}>
                              {LEVEL_LABEL[levelOf(person)]}
                            </span>
                          )}
                          {writableRow
                            ? <span className="truncate">{person.email}</span>
                            : <span className="text-amber-800">no address yet</span>}
                          {person.last_sent_at && (
                            <span>written {shortDate(person.last_sent_at)}
                              {person.last_campaign_name ? ` · ${person.last_campaign_name}` : ''}</span>
                          )}
                        </span>
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
                        onClick={() => void add(key, 'all')}
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
                            <span className="font-medium text-deep-navy">{name}</span>
                            <span className="text-slate-500"> · {person.title || 'no title'}</span>
                            <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-800">found</span>
                              <span className="truncate">{person.email}</span>
                              {person.skippedReason && (
                                <span className="text-amber-900">not added: {person.skippedReason}</span>
                              )}
                            </span>
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
                  {held.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowHeld((prev) => ({ ...prev, [key]: !prev[key] }))}
                      className="w-full border-t border-pale-sky px-3 py-2 text-left text-xs text-slate-600 underline"
                    >
                      {showHeld[key]
                        ? 'Hide the very senior and unclear names'
                        : `Show ${held.length} more: very senior, or the name is unclear`}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
