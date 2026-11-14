import { useEffect, useMemo, useState } from 'react';
import { api, type DirectoryFilter, type DirectoryPage, type DirectoryPerson, type DirectoryProof, type DirectoryReach, type DirectorySummary } from '../api';
import PageHeader from '../components/PageHeader';
import { Button, Chip, EmptyState, Notice, StatusBadge } from '../components/ui/Primitives';
import QueueOutreach from '../components/campaigns/QueueOutreach';

const PAGE_SIZE = 50;

type ChipKey = 'all' | 'ready' | 'not_contacted' | 'contacted' | 'replied' | 'bounced' | 'unverified';

const CHIPS: { key: ChipKey; label: string; filter: DirectoryFilter; count: (s: DirectorySummary) => number }[] = [
  { key: 'all', label: 'Everyone', filter: {}, count: (s) => s.total },
  { key: 'ready', label: 'Ready to contact', filter: { ready: true }, count: (s) => s.ready },
  { key: 'not_contacted', label: 'Not contacted', filter: { reach: 'not_contacted' }, count: (s) => s.reach.not_contacted },
  { key: 'contacted', label: 'Contacted', filter: { reach: 'contacted' }, count: (s) => s.reach.contacted },
  { key: 'replied', label: 'Replied', filter: { reach: 'replied' }, count: (s) => s.reach.replied },
  { key: 'bounced', label: 'Bounced', filter: { reach: 'bounced' }, count: (s) => s.reach.bounced },
  { key: 'unverified', label: 'Unverified', filter: { proof: 'unverified' }, count: (s) => s.proof.unverified },
];

const PROOF: Record<DirectoryProof, { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger' }> = {
  replied: { label: 'Replied', tone: 'success' },
  delivered: { label: 'Delivered before', tone: 'success' },
  mailbox_confirmed: { label: 'Mailbox confirmed', tone: 'success' },
  catch_all: { label: 'Catch-all domain', tone: 'warning' },
  bounced: { label: 'Bounced', tone: 'danger' },
  mailbox_rejected: { label: 'Mailbox rejected', tone: 'danger' },
  unverified: { label: 'Unverified', tone: 'neutral' },
};

/** "Nov 3", with the year when it is not this one. The stamp is UTC, so the date is read as UTC. */
function shortDate(stamp: string | null): string {
  const date = stamp ? new Date(`${stamp.slice(0, 10)}T00:00:00Z`) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  const sameYear = date.getUTCFullYear() === new Date().getUTCFullYear();
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }), timeZone: 'UTC' });
}

function reachText(reach: DirectoryReach): string {
  const who = reach.by_me ? 'you' : reach.by;
  const when = shortDate(reach.at);
  const parts: string[] = [];
  switch (reach.state) {
    case 'replied': parts.push('Replied'); break;
    case 'bounced': parts.push('Bounced'); break;
    case 'contacted': parts.push(who ? `Contacted by ${who}` : 'Contacted'); break;
    case 'queued': parts.push(who ? `Queued by ${who}` : 'Queued'); break;
    default: parts.push('Not contacted');
  }
  if (when && reach.state !== 'queued') parts.push(when);
  if (reach.state === 'contacted' && reach.sends > 1) parts.push(`${reach.sends} emails`);
  return parts.join(' · ');
}

function backlogLine(summary: DirectorySummary): string {
  const { backlog } = summary;
  const parts = [
    `${backlog.verified_total.toLocaleString()} addresses confirmed`,
    `${backlog.credits_used_today.toLocaleString()} of ${backlog.credits_cap_today.toLocaleString()} checks used today`,
    `${backlog.queue_remaining.toLocaleString()} waiting`,
  ];
  if (!backlog.enabled) parts.push('background checks are off');
  return parts.join(' · ');
}

/** Someone can be queued when they are ready and either a contacts row stands behind them or a readable search run can import them. */
const canQueue = (person: DirectoryPerson) =>
  person.ready && (person.contact_id !== null || (person.run_id !== null && person.prospect_id !== null));

function PersonRow({ person, selected, onToggle }: { person: DirectoryPerson; selected: boolean; onToggle: (person: DirectoryPerson) => void }) {
  const proof = PROOF[person.proof];
  return (
    <tr className="border-b border-[var(--border)] align-top">
      <td className="py-2 pr-2">
        {canQueue(person) && (
          <input
            type="checkbox"
            aria-label={`Select ${person.name || person.email}`}
            checked={selected}
            onChange={() => onToggle(person)}
          />
        )}
      </td>
      <td className="py-2 pr-4 font-medium">{person.name || '—'}</td>
      <td className="py-2 pr-4">{person.title || '—'}</td>
      <td className="py-2 pr-4">
        {person.company || '—'}
        {person.reach.claimed_by && <div className="mt-1"><StatusBadge tone="warning">Held by {person.reach.claimed_by}</StatusBadge></div>}
      </td>
      <td className="py-2 pr-4 break-all">{person.email}<div className="text-xs text-[var(--text-secondary)]">{person.source}</div></td>
      <td className="py-2 pr-4"><StatusBadge tone={proof.tone}>{proof.label}</StatusBadge></td>
      <td className="py-2">{reachText(person.reach)}</td>
    </tr>
  );
}

export default function Directory() {
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [chip, setChip] = useState<ChipKey>('all');
  const [offset, setOffset] = useState(0);
  // The answer to one request, tagged with that request's key: a request still in flight is simply one nobody has answered yet.
  const [settled, setSettled] = useState<{ key: string; page?: DirectoryPage; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const [summary, setSummary] = useState<DirectorySummary | null>(null);
  const [summaryError, setSummaryError] = useState(false);
  const [selected, setSelected] = useState<Map<string, DirectoryPerson>>(new Map());
  const [queueIds, setQueueIds] = useState<number[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');
  const filter: DirectoryFilter = useMemo(() => ({ ...CHIPS.find((c) => c.key === chip)!.filter, q }), [chip, q]);

  // Only a changed search resets the page and the ticks; otherwise the first
  // debounce after load would wipe a selection made in its first 250 ms.
  useEffect(() => {
    if (search === q) return;
    const timer = window.setTimeout(() => { setQ(search); setOffset(0); setSelected(new Map()); }, 250);
    return () => window.clearTimeout(timer);
  }, [search, q]);

  useEffect(() => {
    api.directory.summary().then(setSummary).catch(() => setSummaryError(true));
  }, []);

  const requestKey = JSON.stringify([filter, offset, retry]);
  useEffect(() => {
    const controller = new AbortController();
    api.directory.list(filter, PAGE_SIZE, offset, controller.signal)
      .then((result) => setSettled({ key: requestKey, page: result }))
      .catch((requestError: Error) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error: requestError.message });
      });
    return () => controller.abort();
  }, [filter, offset, requestKey]);
  const choose = (key: ChipKey) => { setChip(key); setOffset(0); setSelected(new Map()); };
  const toggle = (person: DirectoryPerson) => setSelected((current) => {
    const next = new Map(current);
    if (!next.delete(person.email)) next.set(person.email, person);
    return next;
  });
  const togglePage = () => setSelected((current) => {
    const next = new Map(current);
    const all = queueable.every((person) => next.has(person.email));
    for (const person of queueable) {
      if (all) next.delete(person.email);
      else next.set(person.email, person);
    }
    return next;
  });

  // People found by a search are brought on file first (one import per run, the
  // server applying the run's own visibility), then everyone goes to the queue panel.
  const queueSelected = async () => {
    setAddError('');
    setAdding(true);
    try {
      const people = [...selected.values()];
      const ids = people.flatMap((person) => (person.contact_id !== null ? [person.contact_id] : []));
      const byRun = new Map<number, number[]>();
      for (const person of people) {
        if (person.contact_id === null) byRun.set(person.run_id!, [...(byRun.get(person.run_id!) ?? []), person.prospect_id!]);
      }
      let left = 0;
      for (const [runId, prospectIds] of byRun) {
        const outcomes = await api.yucgoutreach.importContacts(runId, prospectIds);
        for (const outcome of outcomes.results) {
          if (outcome.contact_id !== null) ids.push(outcome.contact_id);
          else left += 1;
        }
      }
      setRetry((n) => n + 1);
      if (left > 0) setAddError(`${left} ${left === 1 ? 'person' : 'people'} could not be added to your contacts and ${left === 1 ? 'was' : 'were'} left out.`);
      if (ids.length > 0) setQueueIds(ids);
      setSelected(new Map());
    } catch (failure) {
      setAddError(failure instanceof Error ? failure.message : 'Could not add these people to your contacts.');
    } finally {
      setAdding(false);
    }
  };
  const loading = settled?.key !== requestKey;
  const error = loading ? '' : settled?.error ?? '';
  const page = settled?.page ?? null;
  const items = page?.items ?? [];
  const queueable = items.filter(canQueue);
  const adds = [...selected.values()].filter((person) => person.contact_id === null).length;
  const total = page?.total ?? 0;

  return (
    <div className="app-workspace max-w-6xl space-y-4">
      <PageHeader
        title="Directory"
        subtitle="Everyone the club knows: their best address, how well it is proven, and whether anyone has written to them."
        actions={
          <a className="ui-button ui-button--secondary ui-button--md" href={api.directory.exportUrl(filter)} download="directory.csv">
            Export CSV
          </a>
        }
      />

      <p className="text-sm text-[var(--text-secondary)]" role="status" aria-label="Verification backlog">
        {summary ? backlogLine(summary) : summaryError ? 'Verification status is unavailable right now.' : 'Loading verification status…'}
      </p>

      <section className="surface-card space-y-3 p-4" aria-label="Search and filters">
        <label className="block text-sm font-medium">
          Search name, title, company or email
          <input
            className="ui-input mt-1 block w-full sm:w-96"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter">
          {CHIPS.map((option) => (
            <Chip key={option.key} selected={chip === option.key} onClick={() => choose(option.key)}>
              {option.label}{summary && !summary.capped ? ` (${option.count(summary).toLocaleString()})` : ''}
            </Chip>
          ))}
        </div>
      </section>

      {error && (
        <Notice tone="danger">
          <span>Could not load the directory: {error} </span>
          <Button size="sm" variant="secondary" onClick={() => setRetry((n) => n + 1)}>Try again</Button>
        </Notice>
      )}
      {page?.capped && !error && (
        <Notice tone="info">This filter covers the newest people only. Narrow the search to see the rest.</Notice>
      )}

      {addError && <Notice tone="danger">{addError}</Notice>}
      {selected.size > 0 && (
        <Notice tone="info">
          <span>{selected.size} selected{adds > 0 ? `, ${adds} from searches that will be added to your contacts first` : ''}. </span>
          <Button size="sm" disabled={adding} onClick={queueSelected}>{adding ? 'Adding…' : 'Queue outreach'}</Button>{' '}
          <Button size="sm" variant="secondary" disabled={adding} onClick={() => setSelected(new Map())}>Clear</Button>
        </Notice>
      )}
      {items.some((person) => !person.on_file) && (
        <p className="text-xs text-[var(--text-secondary)]">
          People from “Find people” searches are not in your contacts yet; ticking one adds them when you queue outreach.
          Only people ready to contact can be selected.
        </p>
      )}
      {loading && !error ? (
        <p className="text-sm text-slate-500" role="status">Loading people…</p>
      ) : !error && items.length === 0 ? (
        <EmptyState
          title="No one matches"
          body={q || chip !== 'all' ? 'Try a different search or filter.' : 'No one is on file yet. Find people to start the directory.'}
        />
      ) : !error ? (
        <section className="surface-card p-4" aria-label="People">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-2 font-semibold">
                    <input
                      type="checkbox"
                      aria-label="Select everyone on this page who can be queued"
                      disabled={queueable.length === 0}
                      checked={queueable.length > 0 && queueable.every((p) => selected.has(p.email))}
                      onChange={togglePage}
                    />
                  </th>
                  <th className="py-2 pr-4 font-semibold">Name</th>
                  <th className="py-2 pr-4 font-semibold">Title</th>
                  <th className="py-2 pr-4 font-semibold">Company</th>
                  <th className="py-2 pr-4 font-semibold">Email</th>
                  <th className="py-2 pr-4 font-semibold">Address</th>
                  <th className="py-2 font-semibold">Reached</th>
                </tr>
              </thead>
              <tbody>
                {items.map((person) => (
                  <PersonRow
                    key={person.email}
                    person={person}
                    selected={selected.has(person.email)}
                    onToggle={toggle}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between gap-3 text-sm">
            <span>{offset + 1}–{offset + items.length} of {total.toLocaleString()}</span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={offset + items.length >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
            </div>
          </div>
        </section>
      ) : null}
      {queueIds && <QueueOutreach source={{ label: 'Directory selection', contactIds: queueIds }} onClose={() => setQueueIds(null)} />}
    </div>
  );
}
