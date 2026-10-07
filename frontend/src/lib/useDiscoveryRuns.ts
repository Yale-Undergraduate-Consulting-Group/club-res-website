import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError, api,
  type Contact, type DiscoveryProspect, type DiscoveryRun, type EntityProfile, type ImportOutcome,
  type ResolvedAlternative,
} from '../api';
import { companyKey, isTooSenior, looksLikePerson } from './recipients';

/**
 * The durable Find people searches behind the campaign pipeline, one per
 * company, kept where every step can see them.
 *
 * A search used to live inside the picker of one step and be forgotten when
 * the member moved on or reloaded: a run still working on the server had no
 * lane to report to, and its people, once found, were imported wholesale
 * whether or not anybody wanted them. Here the runs are read back from the
 * server on arrival, so a reload resumes the progress bar. What a search
 * started in this sitting finds is added to the club's contacts straight away
 * - the people the default tick would choose - so the member ticks rather
 * than adds; anyone else, and anything an earlier visit's search found, stays
 * "found" until the member adds them on purpose.
 *
 * The server allows one search per member at a time. Asking for a second
 * one is not a mistake, so it is queued here and started when the first
 * finishes, rather than refused.
 */

export type ChosenCompany = {
  name: string;
  domain?: string;
  /** The website was checked (or came from a trusted record), not guessed. */
  verified?: boolean;
  linkedinUrl?: string;
  /** How the name was found: a pasted LinkedIn page, the club's own list,
   *  the public register, or the text exactly as typed. */
  source?: 'linkedin' | 'register' | 'club' | 'typed';
  /** Other companies the same words could have meant, for "Not this?":
   *  other group entities and countries carry their own profile. */
  alternatives?: ResolvedAlternative[];
  /** Home country of the entity, ISO code ("GB"). */
  country?: string;
  /** The entity the search is about, so it can skip other group entities. */
  entity?: EntityProfile;
  /** Where to look: the home country by default, "*" for anywhere. */
  targetCountry?: string;
};

export type LaneState = 'idle' | 'queued' | 'searching' | 'done' | 'failed';

export type CompanyRun = {
  state: LaneState;
  runId?: number;
  pct: number;
  message: string;
};

/** A person a run found, with the reason the last Add left them behind. */
export type FoundPerson = DiscoveryProspect & { skippedReason?: string };

export type SearchOptions = { titleHints: string; maxProspects: number; domain?: string };

/** Options are read when the search starts, not when it joins the queue:
 *  a member who changes who to look for while three companies wait should
 *  have all three look for the new people. */
type Queued = { company: ChosenCompany; options: () => SearchOptions };

const POLL_MS = 2500;
/** How long a search paused by a limit waits before asking again. */
const RETRY_AFTER_LIMIT_MS = 5 * 60 * 1000;
const ACTIVE: Record<string, true> = { queued: true, running: true };

/** Prospects a member could actually add: junk and address-less rows are
 *  what the import refuses, so they are not offered. */
function usable(rows: DiscoveryProspect[]): FoundPerson[] {
  const seen = new Set<string>();
  const out: FoundPerson[] = [];
  for (const row of rows) {
    const email = (row.email || '').trim().toLowerCase();
    if (!email || row.ai_verdict === 'junk' || seen.has(email)) continue;
    seen.add(email);
    out.push(row);
  }
  return out;
}

export function useDiscoveryRuns({ chosen, linkedRunId, onImported }: {
  chosen: ChosenCompany[];
  /** `?run=<id>` from a link: the run whose lane should be in focus. */
  linkedRunId?: number;
  /** The people that landed on file, so the caller can reload and tick them. */
  onImported: (outcomes: ImportOutcome[]) => Promise<void> | void;
}) {
  const [runsById, setRunsById] = useState<Record<number, DiscoveryRun>>({});
  const [found, setFound] = useState<Record<string, FoundPerson[]>>({});
  const [queue, setQueue] = useState<Queued[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const queueRef = useRef<Queued[]>([]);
  const loadedProspects = useRef<Set<number>>(new Set());
  // Searches started here, in this sitting. Their people are added to the
  // club's contacts as soon as they are found, so they can be ticked without
  // an "Add all" step; a search from an earlier visit only shows what it found.
  const startedHere = useRef<Set<number>>(new Set());
  const onImportedRef = useRef(onImported);
  useEffect(() => { onImportedRef.current = onImported; }, [onImported]);

  const chosenKeys = useMemo(() => new Set(chosen.map((c) => companyKey(c.name))), [chosen]);
  const chosenKeyList = [...chosenKeys].sort().join('\n');

  // Latest run per company: an older completed run says nothing once a newer
  // one has been asked for.
  const latestByKey = useMemo(() => {
    const map: Record<string, DiscoveryRun> = {};
    for (const run of Object.values(runsById)) {
      const key = companyKey(run.company_name);
      if (!map[key] || map[key].id < run.id) map[key] = run;
    }
    return map;
  }, [runsById]);

  // What an import did to a company's found list: the people who landed leave
  // it, and anyone refused says why.
  const applyImport = useCallback((key: string, results: ImportOutcome[]) => {
    const landed = new Set(results.filter((r) => r.outcome !== 'skipped').map((r) => r.prospect_id));
    const reasons = new Map(results.filter((r) => r.outcome === 'skipped').map((r) => [r.prospect_id, r.reason || 'skipped']));
    setFound((prev) => ({
      ...prev,
      [key]: (prev[key] || [])
        .filter((p) => !landed.has(p.id))
        .map((p) => (reasons.has(p.id) ? { ...p, skippedReason: reasons.get(p.id) } : p)),
    }));
  }, []);

  const loadFound = useCallback(async (run: DiscoveryRun) => {
    if (loadedProspects.current.has(run.id)) return;
    loadedProspects.current.add(run.id);
    try {
      const rows = usable(await api.yucgoutreach.listProspects(run.id, 800));
      const key = companyKey(run.company_name);
      setFound((prev) => ({ ...prev, [key]: rows }));
      if (!startedHere.current.has(run.id)) return;
      startedHere.current.delete(run.id);
      // The same people the default tick would choose: a real name, not too
      // senior to answer. The rest stay under "found" for a deliberate add.
      const wanted = rows.filter((p) => {
        const person = { name: [p.first_name, p.last_name].filter(Boolean).join(' '), title: p.title } as Contact;
        return looksLikePerson(person) && !isTooSenior(person);
      });
      if (!wanted.length) return;
      const res = await api.yucgoutreach.importContacts(run.id, wanted.map((p) => p.id));
      applyImport(key, res.results);
      await onImportedRef.current(res.results);
    } catch (e) {
      loadedProspects.current.delete(run.id);
      setError(e instanceof Error ? e.message : 'Could not read what that search found');
    }
  }, [applyImport]);

  const refreshRuns = useCallback(async () => {
    try {
      const list = await api.yucgoutreach.listRuns(40);
      setRunsById(Object.fromEntries(list.map((run) => [run.id, run])));
      return list;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read earlier searches');
      return [];
    }
  }, []);

  // Arrival, and every change of companies: what the server already knows.
  // Deferred so the fetch is not a synchronous setState inside the effect,
  // which would cascade a render on mount.
  // Which set of companies the run list has been read for: anything that
  // starts searches on its own must wait for it, or a company searched an
  // hour ago looks unsearched and is searched again.
  const [runsReadyFor, setRunsReadyFor] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshRuns().then(() => setRunsReadyFor(chosenKeyList));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshRuns, chosenKeyList]);
  const runsReady = runsReadyFor === chosenKeyList;

  const linkedRun = linkedRunId ? runsById[linkedRunId] ?? null : null;

  // Finished runs for chosen companies show what they found. A failed run
  // keeps its partial results: they are real people, and the lane says the
  // search did not finish.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      for (const run of Object.values(latestByKey)) {
        if (!chosenKeys.has(companyKey(run.company_name))) continue;
        if (run.status === 'completed' || run.status === 'failed') void loadFound(run);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [latestByKey, chosenKeys, loadFound]);

  // The queue's next step, for a paused search to call back into later.
  const startNextRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const startNow = useCallback(async (item: Queued): Promise<'started' | 'busy' | 'failed'> => {
    const options = item.options();
    try {
      const res = await api.yucgoutreach.createRun({
        company_name: item.company.name,
        company_domain: options.domain || item.company.domain || undefined,
        title_hints: options.titleHints.trim() || undefined,
        max_prospects: options.maxProspects,
        // A country only when the member chose one: by default the search
        // skips the excluded entities and their countries, not everyone
        // outside the home country (Barclays' New York staff are Barclays).
        entity: item.company.entity
          ? { ...item.company.entity, target_country: item.company.targetCountry ?? null }
          : undefined,
      });
      loadedProspects.current.delete(res.id);
      startedHere.current.add(res.id);
      setRunsById((prev) => ({
        ...prev,
        [res.id]: {
          id: res.id, company_name: item.company.name,
          company_domain: item.company.domain, status: 'queued', progress_pct: 0,
          progress_message: 'Starting…',
        },
      }));
      return 'started';
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Not a failure: the server is already busy for this member, and
        // this request waits its turn.
        const list = await refreshRuns();
        const busy = list.find((run) => ACTIVE[run.status]);
        setNotice(`Already searching ${busy ? busy.company_name : 'another company'}; ${item.company.name} is queued behind it.`);
        return 'busy';
      }
      if (e instanceof ApiError && e.status === 429) {
        // The club's search queue or an hourly limit: a pause, not a
        // failure. The company keeps its place and is asked for again.
        setNotice(`Searches are paused for a few minutes (the hourly limit). ${item.company.name} and anything after it start by themselves; meanwhile you can tick people already found or import a spreadsheet.`);
        window.setTimeout(() => { void startNextRef.current(); }, RETRY_AFTER_LIMIT_MS);
        return 'busy';
      }
      setError(e instanceof Error ? e.message : 'Could not start that search');
      return 'failed';
    }
  }, [refreshRuns]);

  const startNext = useCallback(async () => {
    const next = queueRef.current[0];
    if (!next) return;
    if (await startNow(next) !== 'busy') {
      queueRef.current = queueRef.current.slice(1);
      setQueue(queueRef.current);
    }
  }, [startNow]);
  useEffect(() => { startNextRef.current = startNext; }, [startNext]);

  // One poll for every active run this member has, whether it was started
  // here or before a reload. When a run finishes, its people are read and
  // the next queued company starts.
  const activeIds = Object.values(runsById).filter((run) => ACTIVE[run.status]).map((run) => run.id).join(',');
  useEffect(() => {
    if (!activeIds) return;
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      let finished = false;
      for (const id of activeIds.split(',').map(Number)) {
        try {
          const run = await api.yucgoutreach.getRun(id);
          if (cancelled) return;
          setRunsById((prev) => ({ ...prev, [run.id]: run }));
          if (run.status === 'completed' || run.status === 'failed') {
            finished = true;
            if (chosenKeys.has(companyKey(run.company_name))) void loadFound(run);
          }
        } catch (e) {
          if (cancelled) return;
          setError(e instanceof Error ? e.message : 'Lost track of that search');
          return;
        }
      }
      if (finished) {
        setNotice('');
        void startNext();
        return;
      }
      timer = window.setTimeout(() => { void tick(); }, POLL_MS);
    };
    timer = window.setTimeout(() => { void tick(); }, POLL_MS);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [activeIds, chosenKeys, loadFound, startNext]);

  const startRun = useCallback(async (company: ChosenCompany, options: () => SearchOptions) => {
    setError('');
    const key = companyKey(company.name);
    if (queueRef.current.some((q) => companyKey(q.company.name) === key)) return;
    const item: Queued = { company, options };
    const busyRun = Object.values(runsById).find((run) => ACTIVE[run.status]);
    if (!busyRun && queueRef.current.length === 0) {
      if (await startNow(item) !== 'busy') return;
    } else {
      // Same words whether the page knew the server was busy or the server
      // had to say so: this search waits its turn.
      setNotice(busyRun
        ? `Already searching ${busyRun.company_name}; ${company.name} is queued behind it.`
        : `${company.name} is queued behind ${queueRef.current[0].company.name}.`);
    }
    if (queueRef.current.some((q) => companyKey(q.company.name) === key)) return;
    queueRef.current = [...queueRef.current, item];
    setQueue(queueRef.current);
  }, [runsById, startNow]);

  const addFound = useCallback(async (key: string, ids: number[] | 'all') => {
    const run = latestByKey[key];
    const rows = found[key] || [];
    const wanted = ids === 'all' ? rows.filter((p) => !p.skippedReason).map((p) => p.id) : ids;
    if (!run || wanted.length === 0) return;
    setError('');
    try {
      const res = await api.yucgoutreach.importContacts(run.id, wanted);
      applyImport(key, res.results);
      await onImportedRef.current(res.results);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add those people');
    }
  }, [latestByKey, found, applyImport]);

  const exportRun = useCallback(async (runId: number) => {
    setError('');
    try {
      await api.yucgoutreach.exportExcel(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed');
    }
  }, []);

  const deleteRun = useCallback(async (runId: number) => {
    setError('');
    try {
      await api.yucgoutreach.deleteRun(runId);
      const run = runsById[runId];
      setRunsById((prev) => {
        const next = { ...prev };
        delete next[runId];
        return next;
      });
      if (run) {
        setFound((prev) => {
          const next = { ...prev };
          delete next[companyKey(run.company_name)];
          return next;
        });
      }
      loadedProspects.current.delete(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  }, [runsById]);

  // What each chosen company's lane shows.
  const runs = useMemo(() => {
    const map: Record<string, CompanyRun> = {};
    for (const key of chosenKeys) {
      const run = latestByKey[key];
      if (queue.some((q) => companyKey(q.company.name) === key)) {
        map[key] = { state: 'queued', pct: 0, message: 'Queued' };
      } else if (!run) {
        map[key] = { state: 'idle', pct: 0, message: '' };
      } else if (ACTIVE[run.status]) {
        map[key] = {
          state: 'searching', runId: run.id,
          pct: Number(run.progress_pct) || 0,
          message: run.progress_message || 'Searching…',
        };
      } else if (run.status === 'failed') {
        map[key] = { state: 'failed', runId: run.id, pct: 100, message: run.error_message || 'Search failed' };
      } else {
        map[key] = { state: 'done', runId: run.id, pct: 100, message: run.progress_message || 'Search finished' };
      }
    }
    return map;
  }, [chosenKeys, latestByKey, queue]);

  return { runs, runsReady, found, linkedRun, error, notice, startRun, addFound, exportRun, deleteRun };
}
