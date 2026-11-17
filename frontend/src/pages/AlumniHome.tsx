/**
 * The alumni area: the past projects a client agreed to disclose publicly,
 * and their files. Read-only; everything else on the site is for members.
 * The server decides what is shown (backend/app/services/resources.py).
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, api, type AlumniResource, type AlumniResourceList } from '../api';
import { Button, EmptyState, Notice } from '../components/ui/Primitives';

export default function AlumniHome() {
  const navigate = useNavigate();
  const [me, setMe] = useState<{ email: string; name: string | null } | null>(null);
  const [query, setQuery] = useState('');
  const [list, setList] = useState<AlumniResourceList | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.alumni.me().then(setMe).catch(() => navigate('/alumni/login', { replace: true }));
  }, [navigate]);

  useEffect(() => {
    if (!me) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.alumni.resources({ q: query.trim() }, controller.signal)
        .then((result) => { setList(result); setError(''); })
        .catch((e: Error) => {
          if (e.name === 'AbortError') return;
          if (e instanceof ApiError && e.status === 401) navigate('/alumni/login', { replace: true });
          else setError(e.message);
        });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [me, query, navigate]);

  const signOut = async () => {
    await api.alumni.logout().catch(() => {});
    navigate('/alumni/login', { replace: true });
  };

  if (!me) return <p className="app-page-loading" role="status">Opening…</p>;

  return (
    <div className="app-workspace max-w-4xl mx-auto px-4 py-6 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <img src="/yucg-logo.png" alt="YUCG" className="h-10 w-auto" decoding="async" />
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-deep-navy">YUCG Alumni</h1>
            <p className="text-sm text-slate-600 break-all">{me.name || me.email}</p>
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={signOut}>Sign out</Button>
      </header>

      <section className="surface-card space-y-4" aria-labelledby="alumni-projects">
        <div>
          <h2 id="alumni-projects" className="font-semibold text-deep-navy">Public past projects</h2>
          <p className="text-sm text-slate-600">Projects whose clients agreed to public disclosure, with their deliverables.</p>
        </div>
        <input
          className="ui-input"
          type="search"
          aria-label="Search past projects"
          placeholder="Search by client, industry or topic"
          value={query}
          maxLength={200}
          onChange={(event) => setQuery(event.target.value)}
        />
        {error && <Notice tone="danger">Could not load projects: {error}</Notice>}
        {!list && !error && <p className="text-sm text-slate-500" role="status">Loading…</p>}
        {list && list.items.length === 0 && (
          <EmptyState
            title={query.trim() ? 'No matching projects' : 'No public projects yet'}
            body={query.trim() ? 'Try another word.' : 'Projects appear here once a client agrees to public disclosure.'}
          />
        )}
        {list && list.items.length > 0 && (
          <ul className="space-y-3">
            {list.items.map((item) => <AlumniProject key={item.id} item={item} />)}
          </ul>
        )}
      </section>
    </div>
  );
}

function AlumniProject({ item }: { item: AlumniResource }) {
  const [error, setError] = useState('');
  const open = async (fileId: number) => {
    setError('');
    try {
      const file = await api.alumni.download(item.id, fileId);
      window.open(file.url, '_blank', 'noopener');
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <li className="border-b border-pale-sky pb-3 last:border-0 last:pb-0 space-y-1 min-w-0">
      <h3 className="font-medium text-deep-navy break-words">{item.title}</h3>
      <p className="text-sm text-slate-600 break-words">
        {[item.client, item.semester, item.industries.join(', ')].filter(Boolean).join(' · ')}
      </p>
      {item.description && <p className="text-sm text-slate-700 break-words">{item.description}</p>}
      {item.files.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {item.files.map((file) => (
            <Button key={file.id} size="sm" variant="secondary" className="max-w-full" onClick={() => open(file.id)}>
              <span className="truncate">{file.filename}</span>
            </Button>
          ))}
        </div>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
    </li>
  );
}
