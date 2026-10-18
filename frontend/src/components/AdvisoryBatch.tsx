import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Contact } from '../api';
import { looksLikePerson } from '../lib/recipients';

/**
 * The people chosen in Find people, each written to on their own.
 *
 * Find people used to end in one template for everybody, with {first} and
 * {company} standing in for the only parts that differed. That reads as a
 * mail merge because it is one. Here every person gets an advisory draft of
 * their own - the projects a YUCG team could build for their company, chosen
 * for their role - saved as their draft for the member to read and edit, and
 * the campaign is built from those saved drafts.
 */

type Status = 'waiting' | 'drafting' | 'done' | 'failed';

/** Drafts written at once. The server runs two model calls for the whole
 *  club and refuses a third as busy, so a batch takes one at a time. */
const WORKERS = 1;
/** Waits before retrying a person the server was too busy to draft. */
const BACKOFF_MS = [3000, 6000, 12000];
const TRANSIENT = /busy|unavailable|please retry shortly/i;

export default function AdvisoryBatch({ companies, contactIds, tone, model, valueProp, goal, onOpen, onDrafted, onDismiss }: {
  companies: string[];
  contactIds: number[];
  tone: string;
  model?: string;
  /** What the member typed in the generator's proof field, if anything. */
  valueProp: string;
  /** What the member typed as the email's purpose, if anything. */
  goal: string;
  onOpen: (contact: Contact) => void;
  onDrafted: () => void;
  onDismiss: () => void;
}) {
  const [people, setPeople] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<Record<number, Status>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState('');
  const [campaignName, setCampaignName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<number | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const stopRef = useRef(false);

  useEffect(() => {
    let live = true;
    const wanted = new Set(contactIds);
    api.contacts.list({ companies: companies.join(','), limit: 800 })
      .then((page) => { if (live) setPeople((page.items || []).filter((c) => wanted.has(c.id))); })
      .catch((e) => { if (live) setNote(e instanceof Error ? e.message : 'Could not load these people'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [companies, contactIds]);

  const counts = useMemo(() => {
    const out = { done: 0, failed: 0 };
    for (const p of people) {
      if (status[p.id] === 'done') out.done += 1;
      if (status[p.id] === 'failed') out.failed += 1;
    }
    return out;
  }, [people, status]);
  // A row whose name is page text ("Transformation Leader") would get an
  // email opening "Dear Transformation,". It is listed, never drafted.
  const skipped = people.filter((p) => !looksLikePerson(p));
  const writable = people.filter(looksLikePerson);
  const remaining = writable.filter((p) => status[p.id] !== 'done');

  const draftAll = async () => {
    stopRef.current = false;
    setRunning(true);
    setNote('');
    // One citation lookup per company, not per person: it only ever returns
    // projects an admin marked discussable, so it is safe to reuse.
    const citations = new Map<string, Promise<string>>();
    const citeFor = (company: string) => {
      if (!citations.has(company)) {
        citations.set(company, api.projects.suggestCitations(company).then((res) => {
          const clients = (res.projects || []).map((p) => p.client_name).filter(Boolean);
          return clients.length ? `Past clients we can discuss: ${clients.join(', ')}.` : '';
        }).catch(() => ''));
      }
      return citations.get(company)!;
    };
    const queue = [...remaining];
    const work = async () => {
      for (let person = queue.shift(); person && !stopRef.current; person = queue.shift()) {
        const id = person.id;
        setStatus((s) => ({ ...s, [id]: 'drafting' }));
        try {
          const cite = person.company ? await citeFor(person.company) : '';
          const request = () => api.emails.generate({
            contact_id: id,
            angle: 'advisory',
            // Two or three proposals need the room; "short" cannot hold a list.
            length: 'standard',
            tone,
            model,
            value_proposition: [valueProp.trim(), cite].filter(Boolean).join('\n\n') || undefined,
            custom_instructions: goal.trim() ? `Email purpose: ${goal.trim()}` : undefined,
          });
          // A busy or throttled model is not a failed draft: wait and ask
          // again. The server gives back the hourly draft for these.
          for (let attempt = 0; ; attempt += 1) {
            try {
              await request();
              break;
            } catch (e) {
              const message = e instanceof Error ? e.message : '';
              if (!TRANSIENT.test(message) || attempt >= BACKOFF_MS.length || stopRef.current) throw e;
              await new Promise((resolve) => window.setTimeout(resolve, BACKOFF_MS[attempt]));
            }
          }
          setStatus((s) => ({ ...s, [id]: 'done' }));
        } catch (e) {
          const message = e instanceof Error ? e.message : 'Draft failed';
          setStatus((s) => ({ ...s, [id]: /limit reached/i.test(message) ? 'waiting' : 'failed' }));
          setReason((r) => ({ ...r, [id]: message }));
          // The hourly limit fails every later request too: stop rather than
          // mark the whole remainder failed.
          if (/limit reached/i.test(message)) {
            stopRef.current = true;
            setNote('The hourly draft limit was reached. Everything drafted so far is saved; press the button again after the hour to write the rest.');
          }
        }
      }
    };
    await Promise.all(Array.from({ length: WORKERS }, work));
    setRunning(false);
    onDrafted();
  };

  const saveCampaign = async () => {
    setSaving(true);
    setNote('');
    try {
      const ids = people.filter((p) => status[p.id] === 'done').map((p) => p.id);
      const camp = await api.campaigns.create(campaignName.trim() || `Advisory ${new Date().toLocaleDateString()}`);
      // No subjects or bodies: each person's latest saved draft is used, so
      // whatever the member edited is what goes into the campaign.
      await api.campaigns.addContacts(camp.id, { contact_ids: ids });
      setSaved(camp.id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not save the campaign');
    } finally {
      setSaving(false);
    }
  };

  const dot: Record<Status, string> = {
    waiting: 'bg-slate-300',
    drafting: 'bg-sky-500 animate-pulse',
    done: 'bg-emerald-600',
    failed: 'bg-amber-600',
  };
  const label: Record<Status, string> = { waiting: 'not drafted', drafting: 'writing…', done: 'drafted', failed: 'failed' };
  const pct = writable.length ? Math.round((counts.done / writable.length) * 100) : 0;

  // On desktop Drafts is a fixed-height column and the workspace below takes
  // what is left, so this panel stays one short bar: the list of people is
  // folded away and, opened, floats over the workspace instead of taking
  // height from it.
  return (
    <section className="surface-card relative shrink-0 rounded-xl border border-pale-sky px-4 py-3 space-y-2" aria-label="Write to each person"
             data-testid="advisory-batch">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-80">
          <h2 className="text-[15px] font-semibold text-deep-navy">
            {loading ? 'Loading the people you chose…' : `Writing to ${people.length} ${people.length === 1 ? 'person' : 'people'}, one email each`}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {counts.done > 0 && !running && (saved ? (
            <span className="text-sm text-emerald-900">
              Campaign #{saved} saved, nothing sent.{' '}
              <Link className="font-semibold underline" to={`/campaigns/${saved}`}>Review and release it</Link>
            </span>
          ) : (
            <>
              <input
                value={campaignName}
                onChange={(e) => setCampaignName(e.target.value)}
                placeholder={`Advisory ${new Date().toLocaleDateString()}`}
                aria-label="Campaign name"
                className="w-44 rounded-lg border border-pale-sky px-3 py-1.5 text-sm text-deep-navy"
              />
              <button type="button" disabled={saving} onClick={() => void saveCampaign()} className="ui-button ui-button--secondary ui-button--sm">
                {saving ? 'Saving…' : `Save the ${counts.done} drafted as a campaign`}
              </button>
            </>
          ))}
          {!(remaining.length === 0 && people.length > 0) && (
            <button
              type="button"
              disabled={loading || running}
              onClick={() => void draftAll()}
              className="ui-button ui-button--primary ui-button--sm"
            >
              {running
                ? `Writing… ${counts.done} of ${writable.length}`
                : counts.done > 0
                  ? `Draft the remaining ${remaining.length}`
                  : `Draft an advisory email for each of these ${writable.length}`}
            </button>
          )}
          {running && (
            <button type="button" onClick={() => { stopRef.current = true; }} className="ui-button ui-button--ghost ui-button--sm">
              Stop
            </button>
          )}
          <button type="button" onClick={onDismiss} className="px-1 text-xs text-slate-500 underline">Close</button>
        </div>
      </div>

      {people.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600">
          <button
            type="button"
            onClick={() => setListOpen((v) => !v)}
            aria-expanded={listOpen}
            className="font-medium text-deep-navy underline"
          >
            {listOpen ? 'Hide the people' : `Show the ${people.length} people`}
          </button>
          {skipped.length > 0 && (
            <span className="text-amber-900" data-testid="skipped-note">
              {skipped.length} skipped: the name is not a person&apos;s
            </span>
          )}
          {(running || counts.done > 0 || counts.failed > 0) && (
            <>
              <span className="h-1.5 w-40 overflow-hidden rounded-full bg-pale-sky/70" aria-hidden="true">
                <span className="block h-full bg-emerald-600 transition-[width] duration-300" style={{ width: `${pct}%` }} />
              </span>
              <span role="status">
                {counts.done} drafted{counts.failed ? ` · ${counts.failed} failed` : ''}
              </span>
            </>
          )}
        </div>
      )}

      {listOpen && (
        <ul className="absolute inset-x-0 top-full z-30 mt-1 grid max-h-72 grid-cols-1 gap-1 overflow-y-auto rounded-xl border border-pale-sky bg-white p-2 shadow-lg sm:grid-cols-2 xl:grid-cols-3">
          {people.map((p) => {
            const st = status[p.id] || 'waiting';
            const person = looksLikePerson(p);
            return (
              <li key={p.id} data-person={p.id}>
                <button
                  type="button"
                  onClick={() => onOpen(p)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-pale-sky/25"
                  title={person ? reason[p.id] || `${label[st]} - open ${p.name || p.email}` : 'Skipped: this name looks like page text, not a person'}
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${person ? dot[st] : 'bg-transparent ring-1 ring-amber-600'}`}
                        aria-label={person ? label[st] : 'skipped'} role="img" />
                  <span className="min-w-0 flex-1 truncate text-sm text-deep-navy">
                    {p.name || p.email}
                    <span className="text-xs text-slate-500">{p.title ? ` · ${p.title}` : ''}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {note && <p className="text-xs text-amber-900" role="alert">{note}</p>}
    </section>
  );
}
