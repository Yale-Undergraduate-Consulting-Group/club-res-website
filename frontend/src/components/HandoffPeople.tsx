import { useEffect, useState } from 'react';
import { api, type Contact } from '../api';
import QueueOutreach from './campaigns/QueueOutreach';
import { Button } from './ui/Primitives';

/**
 * The people chosen in Find people (or ticked elsewhere), handed to Drafts.
 * Every member sends the club's template: each person opens in the editor
 * already written from it, to read and edit, or all of them are queued
 * together through the Queue panel with each company's line confirmed.
 */
export default function HandoffPeople({ companies, contactIds, onOpen, onDismiss }: {
  companies: string[];
  contactIds: number[];
  onOpen: (contact: Contact) => void;
  onDismiss: () => void;
}) {
  const [people, setPeople] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [listOpen, setListOpen] = useState(false);
  const [queueing, setQueueing] = useState(false);

  useEffect(() => {
    let live = true;
    const wanted = new Set(contactIds);
    api.contacts.list({ companies: companies.join(','), limit: 800 })
      .then((page) => { if (live) setPeople((page.items || []).filter((c) => wanted.has(c.id))); })
      .catch((e) => { if (live) setNote(e instanceof Error ? e.message : 'Could not load these people'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [companies, contactIds]);

  // On desktop Drafts is a fixed-height column and the workspace below takes
  // what is left, so this panel stays one short bar: the list of people is
  // folded away and, opened, floats over the workspace instead of taking
  // height from it.
  return (
    <section className="surface-card relative shrink-0 space-y-2" aria-label="The people you chose" data-testid="handoff-people">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-80">
          <h2 className="text-[15px] font-semibold text-deep-navy">
            {loading ? 'Loading the people you chose…' : `${people.length} ${people.length === 1 ? 'person' : 'people'} to write to with the club template`}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {people.length > 0 && (
            <Button size="sm" onClick={() => setQueueing(true)}>Queue all {people.length}</Button>
          )}
          <button type="button" onClick={onDismiss} className="px-1 text-xs text-slate-500 underline">Close</button>
        </div>
      </div>

      {people.length > 0 && (
        <button type="button" onClick={() => setListOpen((v) => !v)} aria-expanded={listOpen}
                className="text-xs font-medium text-deep-navy underline">
          {listOpen ? 'Hide the people' : `Show the ${people.length} people: open one to read and edit their email`}
        </button>
      )}

      {listOpen && (
        <ul className="absolute inset-x-0 top-full z-30 mt-1 grid max-h-72 grid-cols-1 gap-1 overflow-y-auto border border-pale-sky bg-white p-2 sm:grid-cols-2 xl:grid-cols-3">
          {people.map((p) => (
            <li key={p.id} data-person={p.id}>
              <button type="button" onClick={() => { setListOpen(false); onOpen(p); }}
                      className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-pale-sky/25"
                      title={`Open ${p.name || p.email}`}>
                <span className="min-w-0 flex-1 truncate text-sm text-deep-navy" title={`${p.name || p.email}${p.title ? ` · ${p.title}` : ''}`}>
                  {p.name || p.email}
                  <span className="text-xs text-slate-500">{p.title ? ` · ${p.title}` : ''}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {note && <p className="text-xs text-deep-navy" role="alert">{note}</p>}
      {queueing && (
        <QueueOutreach source={{ label: 'Drafts', contactIds: people.map((p) => p.id) }} onClose={() => setQueueing(false)} />
      )}
    </section>
  );
}
