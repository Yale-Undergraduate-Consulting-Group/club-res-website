import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api, type ResourceItem, type ResourceList } from '../api';
import PageHeader from '../components/PageHeader';
import { Button, ConfirmDialog, EmptyState, Notice } from '../components/ui/Primitives';
import {
  clientText, formatFileSize, formatRevenue, groupTeam, hasAdminDetails, hiddenFilesNote, kindLabel, kindSingular, orderKinds,
} from '../lib/resourcesView';

const PAGE_SIZE = 30;
/** A description longer than this is clamped on the card, with a toggle to read all of it. */
const LONG_DESCRIPTION = 260;

type Facets = ResourceList['facets'];
type Settled = { key: string; items?: ResourceItem[]; total?: number; error?: string };

const union = (current: string[], more: string[]) => [...current, ...more.filter((value) => !current.includes(value))];

/** Resources: the club's past projects, slide templates, interview and survey guides and analytics resources. */
export default function Resources() {
  const { user } = useOutletContext<{ user: { role?: string } }>();
  const isAdmin = user?.role === 'admin';
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [semester, setSemester] = useState('');
  const [industry, setIndustry] = useState('');
  const [retry, setRetry] = useState(0);
  const [settled, setSettled] = useState<Settled | null>(null);
  // The filters narrow the server's facets, so the choices already seen stay on offer.
  const [facets, setFacets] = useState<Facets>({ kinds: [], semesters: [], industries: [] });
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState('');
  const [removing, setRemoving] = useState<ResourceItem | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState('');
  const moreController = useRef<AbortController | null>(null);

  // Typing searches after a pause; Enter searches at once.
  useEffect(() => {
    if (search === q) return;
    const timer = window.setTimeout(() => setQ(search), 300);
    return () => window.clearTimeout(timer);
  }, [search, q]);

  const filter = useMemo(() => ({ q, kind, semester, industry }), [q, kind, semester, industry]);
  const requestKey = JSON.stringify([filter, retry]);
  useEffect(() => {
    const controller = new AbortController();
    api.resources.list({ ...filter, limit: PAGE_SIZE }, controller.signal)
      .then((result) => {
        setFacets((seen) => ({
          kinds: union(seen.kinds, result.facets.kinds),
          semesters: union(seen.semesters, result.facets.semesters),
          industries: union(seen.industries, result.facets.industries),
        }));
        setSettled({ key: requestKey, items: result.items, total: result.total });
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error: failure.message });
      });
    return () => { controller.abort(); moreController.current?.abort(); };
  }, [filter, requestKey]);

  const loading = settled?.key !== requestKey;
  const error = loading ? '' : settled?.error ?? '';
  const items = loading ? [] : settled?.items ?? [];
  const total = settled?.total ?? 0;
  const filtered = Boolean(q || kind || semester || industry);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setQ(search);
  };

  const showMore = async () => {
    const controller = new AbortController();
    moreController.current = controller;
    setMoreError('');
    setLoadingMore(true);
    try {
      const result = await api.resources.list({ ...filter, limit: PAGE_SIZE, offset: items.length }, controller.signal);
      setSettled((current) => (current?.key === requestKey
        ? { ...current, items: [...(current.items ?? []), ...result.items], total: result.total }
        : current));
    } catch (failure) {
      if (!controller.signal.aborted) setMoreError(failure instanceof Error ? failure.message : 'Could not load more resources.');
    } finally {
      setLoadingMore(false);
    }
  };

  const remove = async () => {
    if (!removing) return;
    setRemoveBusy(true);
    setRemoveError('');
    try {
      await api.resources.remove(removing.id);
      const gone = removing.id;
      setSettled((current) => (current?.items
        ? { ...current, items: current.items.filter((item) => item.id !== gone), total: Math.max(0, (current.total ?? 1) - 1) }
        : current));
      setRemoving(null);
    } catch (failure) {
      setRemoveError(failure instanceof Error ? failure.message : 'Could not delete this resource.');
    } finally {
      setRemoveBusy(false);
    }
  };

  return (
    <div className="app-workspace max-w-5xl space-y-4">
      <PageHeader
        title="Resources"
        subtitle="The club's past projects, slide templates, interview and survey guides and analytics resources."
      />

      <section className="surface-card space-y-3" aria-label="Search and filters">
        <form role="search" className="resource-search" onSubmit={submitSearch}>
          <label className="block text-sm font-medium">
            Search resources
            <input
              className="ui-input mt-1 block w-full"
              type="search"
              value={search}
              placeholder="Title, client, industry or topic"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <Button type="submit" variant="secondary">Search</Button>
        </form>
        <div className="resource-filters">
          <label className="block text-sm font-medium">
            Type
            <select className="ui-input mt-1 block w-full" aria-label="Type" value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="">All types</option>
              {orderKinds(facets.kinds).map((value) => <option key={value} value={value}>{kindLabel(value)}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium">
            Semester
            <select className="ui-input mt-1 block w-full" aria-label="Semester" value={semester} onChange={(event) => setSemester(event.target.value)}>
              <option value="">All semesters</option>
              {facets.semesters.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium">
            Industry
            <select className="ui-input mt-1 block w-full" aria-label="Industry" value={industry} onChange={(event) => setIndustry(event.target.value)}>
              <option value="">All industries</option>
              {facets.industries.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </div>
      </section>

      {error && (
        <Notice tone="danger">
          <span>Could not load resources: {error} </span>
          <Button size="sm" variant="secondary" onClick={() => setRetry((n) => n + 1)}>Retry</Button>
        </Notice>
      )}

      {loading ? (
        <p className="text-sm text-[var(--text-secondary)]" role="status">Loading resources…</p>
      ) : !error && items.length === 0 ? (
        <EmptyState
          title="No resources match"
          body={filtered ? 'Try a different search or clear a filter.' : 'Nothing has been added to the library yet.'}
        />
      ) : !error ? (
        <section aria-label="Results" className="space-y-3">
          <p className="text-sm text-[var(--text-secondary)]" role="status">
            Showing {items.length.toLocaleString()} of {total.toLocaleString()} {total === 1 ? 'resource' : 'resources'}
          </p>
          <ul className="resource-list">
            {items.map((item) => (
              <li key={item.id}>
                <ResourceCard item={item} canDelete={isAdmin} onDelete={() => { setRemoveError(''); setRemoving(item); }} />
              </li>
            ))}
          </ul>
          {moreError && <Notice tone="danger">{moreError}</Notice>}
          {items.length < total && (
            <div className="flex justify-center">
              <Button variant="secondary" disabled={loadingMore} onClick={showMore}>{loadingMore ? 'Loading…' : 'Show more'}</Button>
            </div>
          )}
        </section>
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        title="Delete this resource?"
        danger
        busy={removeBusy}
        confirmLabel="Delete resource"
        body={
          <>
            <p>
              <strong className="resource-wrap">{removing?.title}</strong> and all of its files are removed from the library for everyone.
              Downloads already made cannot be recalled.
            </p>
            {removeError && <Notice tone="danger" className="mt-3">{removeError}</Notice>}
          </>
        }
        onConfirm={remove}
        onClose={() => { setRemoving(null); setRemoveError(''); }}
      />
    </div>
  );
}

function ResourceCard({ item, canDelete, onDelete }: { item: ResourceItem; canDelete: boolean; onDelete: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [opening, setOpening] = useState<number | null>(null);
  const [fileError, setFileError] = useState('');
  const client = clientText(item);
  const hiddenNote = hiddenFilesNote(item);
  const team = groupTeam(item.team);
  const long = (item.description?.length ?? 0) > LONG_DESCRIPTION;

  const open = async (fileId: number) => {
    setFileError('');
    setOpening(fileId);
    try {
      const file = await api.resources.download(item.id, fileId);
      window.open(file.url, '_blank', 'noopener');
    } catch (failure) {
      setFileError(failure instanceof Error ? failure.message : 'Could not open this file.');
    } finally {
      setOpening(null);
    }
  };

  const meta = [
    client ? <span key="client" className={item.client_hidden ? 'resource-client resource-client--hidden' : 'resource-client'}>{client}</span> : null,
    item.semester ? <span key="semester">{item.semester}</span> : null,
    item.industry ? <span key="industry">{item.industry}</span> : null,
  ].filter(Boolean);

  return (
    <article className="surface-card resource-card" aria-label={item.title}>
      <div className="resource-card__head">
        <h2 className="resource-wrap">{item.title}</h2>
        <div className="resource-tags">
          <span className="resource-tag">{kindSingular(item.kind)}</span>
          {item.status && <span className="resource-tag resource-tag--status">{item.status}</span>}
        </div>
      </div>

      {meta.length > 0 && (
        <p className="resource-meta">
          {meta.map((part, index) => <span key={index} className="resource-meta__part">{part}</span>)}
        </p>
      )}
      {item.areas && <p className="resource-areas resource-wrap"><span className="resource-label">Areas</span> {item.areas}</p>}

      {item.description && (
        <div>
          <p
            className={expanded ? 'resource-desc resource-wrap' : 'resource-desc resource-desc--clamped resource-wrap'}
            title={long && !expanded ? item.description : undefined}
          >
            {item.description}
          </p>
          {long && (
            <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
              {expanded ? 'Show less' : 'Read more'}
            </Button>
          )}
        </div>
      )}

      {(item.files.length > 0 || hiddenNote) && (
        <div>
          <h3 className="resource-label">Files</h3>
          {item.files.length > 0 && (
            <ul className="resource-files">
              {item.files.map((file) => (
                <li key={file.id} className="resource-file">
                  <span className="resource-file__name resource-wrap">
                    {file.filename}
                    <span className="resource-file__size"> {formatFileSize(file.byte_size)}</span>
                    {file.admin_only && <span className="resource-tag resource-tag--admin">Admin only</span>}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label={`Open ${file.filename}`}
                    disabled={opening === file.id}
                    onClick={() => open(file.id)}
                  >
                    {opening === file.id ? 'Opening…' : 'Open'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {hiddenNote && <p className="resource-note">{hiddenNote}</p>}
          {fileError && <Notice tone="danger" className="mt-2">{fileError}</Notice>}
        </div>
      )}

      {team.length > 0 && (
        <dl className="resource-team" aria-label="Project team">
          {team.map((group) => (
            <div key={group.role} className="resource-team__group">
              <dt className="resource-label">{group.role}{group.members.length > 1 ? 's' : ''}</dt>
              <dd>
                {group.members.map((person, index) => (
                  <span key={`${person.name}-${index}`} className={person.member ? 'resource-person resource-person--member' : 'resource-person'}>
                    {person.name}
                  </span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {hasAdminDetails(item) && (
        <div className="resource-admin" role="group" aria-label="Admin only">
          <h3>Admin only</h3>
          <dl>
            <div>
              <dt className="resource-label">Revenue</dt>
              <dd>{formatRevenue(item.revenue)}</dd>
            </div>
            <div>
              <dt className="resource-label">Client contacts</dt>
              <dd>
                {item.contacts && item.contacts.length > 0 ? (
                  <ul className="resource-contacts">
                    {item.contacts.map((contact, index) => (
                      <li key={`${contact.email}-${index}`} className="resource-wrap">
                        {contact.name} <a href={`mailto:${contact.email}`}>{contact.email}</a>
                      </li>
                    ))}
                  </ul>
                ) : 'None on record'}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {canDelete && (
        <div className="resource-card__actions">
          <Button size="sm" variant="danger" aria-label={`Delete ${item.title}`} onClick={onDelete}>Delete</Button>
        </div>
      )}
    </article>
  );
}
