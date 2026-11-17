import { useEffect, useState } from 'react';
import type { DirectoryEntry } from '../../api';
import { linkedinProblem } from '../../lib/alumniPortal';
import { EmptyState, Notice, StatusBadge } from '../ui/Primitives';
import { usePortal } from './portalContext';

/** The opt-in alumni directory: only people who chose to be listed. */
export default function DirectorySection() {
  const { api, fail } = usePortal();
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<DirectoryEntry[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.directory(query, controller.signal)
        .then((result) => { setItems(result.items); setError(''); })
        .catch((e: unknown) => {
          if (e instanceof Error && e.name === 'AbortError') return;
          setError(fail(e));
        });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [api, fail, query]);

  return (
    <section className="space-y-4" aria-labelledby="portal-directory">
      <div>
        <h2 id="portal-directory" className="font-semibold text-deep-navy">Alumni directory</h2>
        <p className="text-sm text-slate-600">Alumni who chose to be listed. Search by name, company, role, city, class year or note.</p>
      </div>
      <input
        className="ui-input"
        type="search"
        aria-label="Search the directory"
        placeholder="Search alumni"
        value={query}
        maxLength={100}
        onChange={(event) => setQuery(event.target.value)}
      />
      {error && <Notice tone="danger">Could not load the directory: {error}</Notice>}
      {!items && !error && <p className="text-sm text-slate-500" role="status">Loading…</p>}
      {items && items.length === 0 && (
        <EmptyState
          title={query.trim() ? 'No matching alumni' : 'Nobody is listed yet'}
          body={query.trim()
            ? 'Try another word.'
            : 'The directory is opt-in: alumni appear only after they choose “List me in the directory” on their profile.'}
        />
      )}
      {items && items.length > 0 && (
        <ul className="grid gap-3 md:grid-cols-2">
          {items.map((entry) => <DirectoryCard key={entry.id} entry={entry} />)}
        </ul>
      )}
    </section>
  );
}

function DirectoryCard({ entry }: { entry: DirectoryEntry }) {
  const work = entry.role && entry.company ? `${entry.role} at ${entry.company}` : entry.role ?? entry.company;
  const linkedin = entry.linkedin_url && !linkedinProblem(entry.linkedin_url) ? entry.linkedin_url : null;
  return (
    <li className="surface-card space-y-1 min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold text-deep-navy break-words min-w-0">{entry.display_name}</h3>
        {entry.coffee_chats && <StatusBadge tone="success">Open to coffee chats</StatusBadge>}
      </div>
      {entry.class_year != null && <p className="text-sm text-slate-600">Class of {entry.class_year}</p>}
      {work && <p className="text-sm text-slate-700 break-words">{work}</p>}
      {entry.city && <p className="text-sm text-slate-600 break-words">{entry.city}</p>}
      {entry.note && <p className="text-sm text-slate-700 break-words">{entry.note}</p>}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm min-w-0">
        {linkedin && <a className="text-blue-600 underline break-all" href={linkedin} target="_blank" rel="noopener noreferrer">LinkedIn</a>}
        {entry.email && <a className="text-blue-600 underline break-all" href={`mailto:${entry.email}`}>{entry.email}</a>}
      </div>
    </li>
  );
}
