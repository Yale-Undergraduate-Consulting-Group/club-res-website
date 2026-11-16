import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  api, type ResourceDisclosure, type ResourceFacet, type ResourceItem, type ResourceLinkCreate, type ResourceList,
} from '../api';
import PageHeader from '../components/PageHeader';
import { Button, Chip, ConfirmDialog, EmptyState, Notice, StatusBadge } from '../components/ui/Primitives';
import {
  clientText, disclosureLabel, editableLink, facetOptions, fileCountText, formatFileSize, formatRevenue, groupResources, groupTeam,
  hasAdminDetails, hiddenFilesNote, kindLabel, kindSingular, linkLabel, orderKinds,
} from '../lib/resourcesView';

const PAGE_SIZE = 200;
type Facets = ResourceList['facets'];
type Settled = {
  key: string; items?: ResourceItem[]; total?: number; facets?: Facets; sharepoint_configured?: boolean; error?: string;
};
type Filter = { q: string; kind: string; semester: string; industry: string; disclosure: string };

const EMPTY_FACETS: Facets = { kinds: [], semesters: [], industries: [], disclosures: [] };
const DISCLOSURES: ResourceDisclosure[] = ['public', 'name_only', 'private'];

/** Resources: a compact, faceted library of the club's past work, templates and guides. */
export default function Resources() {
  const { user } = useOutletContext<{ user: { role?: string } }>();
  const isAdmin = user?.role === 'admin';
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>({ q: '', kind: '', semester: '', industry: '', disclosure: '' });
  const [retry, setRetry] = useState(0);
  const [settled, setSettled] = useState<Settled | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState('');
  const [removing, setRemoving] = useState<ResourceItem | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState('');
  const [adding, setAdding] = useState(false);
  const moreController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (search === filter.q) return;
    const timer = window.setTimeout(() => setFilter((current) => ({ ...current, q: search })), 300);
    return () => window.clearTimeout(timer);
  }, [search, filter.q]);

  const requestKey = JSON.stringify([filter, retry]);
  useEffect(() => {
    const controller = new AbortController();
    api.resources.list({ ...filter, limit: PAGE_SIZE }, controller.signal)
      .then((result) => setSettled({ key: requestKey, ...result }))
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error: failure.message });
      });
    return () => { controller.abort(); moreController.current?.abort(); };
  }, [filter, requestKey]);

  const loading = settled?.key !== requestKey;
  const error = loading ? '' : settled?.error ?? '';
  const items = loading ? [] : settled?.items ?? [];
  const total = settled?.total ?? 0;
  const facets = loading ? EMPTY_FACETS : settled?.facets ?? EMPTY_FACETS;
  const filtered = Object.values(filter).some(Boolean);

  const setFacet = (name: keyof Omit<Filter, 'q'>, value: string) => {
    setFilter((current) => ({ ...current, [name]: current[name] === value ? '' : value }));
  };

  const clearFilters = () => {
    setSearch('');
    setFilter({ q: '', kind: '', semester: '', industry: '', disclosure: '' });
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setFilter((current) => ({ ...current, q: search }));
  };

  const showMore = async () => {
    const controller = new AbortController();
    moreController.current = controller;
    setMoreError('');
    setLoadingMore(true);
    try {
      const result = await api.resources.list({ ...filter, limit: PAGE_SIZE, offset: items.length }, controller.signal);
      setSettled((current) => (current?.key === requestKey
        ? { ...current, items: [...(current.items ?? []), ...result.items], total: result.total, facets: result.facets }
        : current));
    } catch (failure) {
      if (!controller.signal.aborted) setMoreError(failure instanceof Error ? failure.message : 'Could not load more resources.');
    } finally {
      setLoadingMore(false);
    }
  };

  const updateItem = (changed: ResourceItem) => {
    setSettled((current) => current?.items
      ? { ...current, items: current.items.map((item) => (item.id === changed.id ? changed : item)) }
      : current);
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
    <div className="app-workspace resource-page">
      <PageHeader
        title="Resources"
        subtitle="Past projects, templates, interview and survey guides, and analytics resources."
        actions={isAdmin ? <Button onClick={() => setAdding(true)}>Add linked resource</Button> : undefined}
      />

      <form role="search" className="surface-card resource-search" onSubmit={submitSearch}>
        <label>
          <span className="sr-only">Search resources</span>
          <input
            className="ui-input block w-full"
            type="search"
            value={search}
            placeholder="Search title, client, industry, topic or file text"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <Button type="submit" variant="secondary">Search</Button>
      </form>

      {isAdmin && !loading && settled?.sharepoint_configured === false && (
        <Notice tone="info">
          SharePoint links are hidden: SHAREPOINT_BASE_URL is not set (Admin → Apps lists it). Google Drive links still open.
        </Notice>
      )}
      {error && (
        <Notice tone="danger">
          <span>Could not load resources: {error} </span>
          <Button size="sm" variant="secondary" onClick={() => setRetry((n) => n + 1)}>Retry</Button>
        </Notice>
      )}

      <div className="resource-layout">
        <aside className="surface-card resource-sidebar" aria-label="Resource filters">
          <div className="resource-sidebar__head">
            <h2>Filter</h2>
            {filtered && <Button size="sm" variant="ghost" onClick={clearFilters}>Clear all</Button>}
          </div>
          <Facet title="Type" values={orderFacetKinds(facetOptions(facets.kinds, filter.kind))} chosen={filter.kind}
            label={kindLabel} onChoose={(value) => setFacet('kind', value)} />
          <Facet title="Semester" values={facetOptions(facets.semesters, filter.semester)} chosen={filter.semester}
            onChoose={(value) => setFacet('semester', value)} />
          <Facet title="Industry" values={facetOptions(facets.industries, filter.industry)} chosen={filter.industry}
            onChoose={(value) => setFacet('industry', value)} collapsible />
          <Facet title="Disclosure" values={facetOptions(facets.disclosures, filter.disclosure)} chosen={filter.disclosure}
            label={disclosureLabel} onChoose={(value) => setFacet('disclosure', value)} />
        </aside>

        <section className="resource-results" aria-label="Results">
          {loading ? (
            <p className="text-sm text-[var(--text-secondary)]" role="status">Loading resources…</p>
          ) : !error && items.length === 0 ? (
            <EmptyState
              title="No resources match"
              body={filtered ? 'Try a different search or clear a filter.' : 'Nothing has been added to the library yet.'}
              action={filtered ? <Button variant="secondary" onClick={clearFilters}>Clear filters</Button> : undefined}
            />
          ) : !error ? (
            <>
              <p className="resource-result-count" role="status">
                {total.toLocaleString()} {total === 1 ? 'resource' : 'resources'}
              </p>
              <ResourceGroups
                items={items}
                isAdmin={isAdmin}
                onChange={updateItem}
                onDelete={(item) => { setRemoveError(''); setRemoving(item); }}
              />
              {moreError && <Notice tone="danger">{moreError}</Notice>}
              {items.length < total && (
                <div className="flex justify-center">
                  <Button variant="secondary" disabled={loadingMore} onClick={showMore}>{loadingMore ? 'Loading…' : 'Show more'}</Button>
                </div>
              )}
            </>
          ) : null}
        </section>
      </div>
      <ConfirmDialog
        open={removing !== null}
        title="Delete this resource?"
        danger
        busy={removeBusy}
        confirmLabel="Delete resource"
        body={(
          <>
            <p><strong className="resource-wrap">{removing?.title}</strong> and all of its files are removed from the library for everyone.</p>
            <p>Downloads already made cannot be recalled.</p>
            {removeError && <Notice tone="danger" className="mt-3">{removeError}</Notice>}
          </>
        )}
        onConfirm={remove}
        onClose={() => { setRemoving(null); setRemoveError(''); }}
      />
      <AddLinkDialog open={adding} onClose={() => setAdding(false)} onAdded={() => {
        setAdding(false);
        setRetry((n) => n + 1);
      }} />
    </div>
  );
}

function orderFacetKinds(values: ResourceFacet[]): ResourceFacet[] {
  const byValue = new Map(values.map((entry) => [entry.value, entry]));
  return orderKinds(values.map((entry) => entry.value)).map((value) => byValue.get(value)!);
}

function Facet({ title, values, chosen, label = (value) => value, onChoose, collapsible = false }: {
  title: string; values: ResourceFacet[]; chosen: string; label?: (value: string) => string;
  onChoose: (value: string) => void; collapsible?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const shown = collapsible && !showAll ? values.slice(0, 8) : values;
  if (values.length === 0 && !chosen) return null;
  return (
    <section className="resource-facet" aria-labelledby={`resource-facet-${title.toLowerCase()}`}>
      <h3 id={`resource-facet-${title.toLowerCase()}`}>{title}</h3>
      <div className="resource-facet__choices">
        {shown.map((entry) => (
          <Chip key={entry.value} selected={chosen === entry.value} onClick={() => onChoose(entry.value)}>
            <span>{label(entry.value)}</span><span className="resource-facet__count">{entry.count}</span>
          </Chip>
        ))}
      </div>
      {collapsible && values.length > 8 && (
        <Button size="sm" variant="ghost" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Show fewer' : `Show all ${values.length}`}
        </Button>
      )}
    </section>
  );
}

function ResourceGroups({ items, isAdmin, onChange, onDelete }: {
  items: ResourceItem[]; isAdmin: boolean; onChange: (item: ResourceItem) => void; onDelete: (item: ResourceItem) => void;
}) {
  const groups = useMemo(() => groupResources(items), [items]);
  const [groupState, setGroupState] = useState<Record<string, boolean>>({});

  return (
    <div className="resource-groups">
      {groups.map((group, index) => {
        const open = groupState[group.key] ?? index === 0;
        const contentId = `resource-group-${group.key.replace(/[^a-z0-9]+/gi, '-')}`;
        return (
          <section key={group.key} className="resource-group" aria-labelledby={`${contentId}-label`}>
            <h2 id={`${contentId}-label`}>
              <button
                type="button"
                className="resource-group__toggle"
                aria-expanded={open}
                aria-controls={contentId}
                onClick={() => setGroupState((current) => ({ ...current, [group.key]: !open }))}
              >
                <span className="resource-chevron" aria-hidden="true">{open ? '−' : '+'}</span>
                <span>{group.label}</span>
                <span className="resource-group__count">{group.items.length}</span>
              </button>
            </h2>
            {open && (
              <ul id={contentId} className="resource-list">
                {group.items.map((item) => (
                  <li key={item.id}>
                    <ResourceRow item={item} isAdmin={isAdmin} onChange={onChange} onDelete={() => onDelete(item)} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function ResourceRow({ item, isAdmin, onChange, onDelete }: {
  item: ResourceItem; isAdmin: boolean; onChange: (item: ResourceItem) => void; onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const client = clientText(item);
  const count = fileCountText(item);
  const detailId = `resource-detail-${item.id}`;
  return (
    <article className="resource-row" aria-label={item.title}>
      <div className="resource-row__summary">
        <button
          type="button"
          className="resource-row__toggle"
          aria-expanded={expanded}
          aria-controls={detailId}
          onClick={() => setExpanded(!expanded)}
        >
          <span className="resource-chevron" aria-hidden="true">{expanded ? '−' : '+'}</span>
          <span className="resource-row__identity">
            <strong className="resource-wrap">{client ?? item.title}</strong>
            {client && item.code_name && <span className="resource-code resource-wrap">{item.code_name}</span>}
            {client && item.title !== client && !item.code_name && <span className="resource-code resource-wrap">{item.title}</span>}
          </span>
          {item.industry && <span className="resource-row__industry resource-wrap">{item.industry}</span>}
          <span className="resource-row__badges">
            {item.kind === 'past_project' && <StatusBadge>{disclosureLabel(item.disclosure)}</StatusBadge>}
            {item.status && <StatusBadge tone="info">{item.status}</StatusBadge>}
            {count && <span className="resource-file-count">{count}</span>}
          </span>
        </button>
        {item.link && (
          <a className="ui-button ui-button--secondary ui-button--sm resource-link" href={item.link.url} target="_blank" rel="noreferrer">
            {linkLabel(item.link.provider)}
          </a>
        )}
      </div>
      {expanded && (
        <ResourceDetail id={detailId} item={item} isAdmin={isAdmin} onChange={onChange} onDelete={onDelete} />
      )}
    </article>
  );
}

function ResourceDetail({ id, item, isAdmin, onChange, onDelete }: {
  id: string; item: ResourceItem; isAdmin: boolean; onChange: (item: ResourceItem) => void; onDelete: () => void;
}) {
  const [opening, setOpening] = useState<number | null>(null);
  const [fileError, setFileError] = useState('');
  const team = groupTeam(item.team);
  const hiddenNote = hiddenFilesNote(item);

  const openFile = async (fileId: number) => {
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

  return (
    <div id={id} className="resource-detail">
      <div className="resource-detail__main">
        <p className="resource-detail__kind">{kindSingular(item.kind)}{item.semester ? ` · ${item.semester}` : ''}</p>
        {item.description && <p className="resource-desc resource-wrap">{item.description}</p>}
        {item.areas && <p className="resource-areas resource-wrap"><span className="resource-label">Areas</span> {item.areas}</p>}
        {(item.files.length > 0 || hiddenNote) && (
          <section>
            <h3 className="resource-label">Files</h3>
            {item.files.length > 0 && (
              <ul className="resource-files">
                {item.files.map((file) => (
                  <li key={file.id} className="resource-file">
                    <span className="resource-file__name resource-wrap">
                      {file.filename}<span className="resource-file__size"> {formatFileSize(file.byte_size)}</span>
                      {file.admin_only && <StatusBadge tone="warning">Admin only</StatusBadge>}
                    </span>
                    <Button size="sm" variant="secondary" aria-label={`Open ${file.filename}`} disabled={opening === file.id}
                      onClick={() => openFile(file.id)}>
                      {opening === file.id ? 'Opening…' : 'Open'}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {hiddenNote && <p className="resource-note">{hiddenNote}</p>}
            {fileError && <Notice tone="danger" className="mt-2">{fileError}</Notice>}
          </section>
        )}
        {team.length > 0 && (
          <dl className="resource-team" aria-label="Project team">
            {team.map((group) => (
              <div key={group.role} className="resource-team__group">
                <dt className="resource-label">{group.role}{group.members.length > 1 ? 's' : ''}</dt>
                <dd>{group.members.map((person, index) => (
                  <span key={`${person.name}-${index}`} className={person.member ? 'resource-person resource-person--member' : 'resource-person'}>
                    {person.name}
                  </span>
                ))}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {hasAdminDetails(item) && (
        <div className="resource-admin" role="group" aria-label="Admin only">
          <h3>Admin only</h3>
          <dl>
            <div><dt className="resource-label">Revenue</dt><dd>{formatRevenue(item.revenue)}</dd></div>
            <div>
              <dt className="resource-label">Client contacts</dt>
              <dd>{item.contacts && item.contacts.length > 0 ? (
                <ul className="resource-contacts">{item.contacts.map((contact, index) => (
                  <li key={`${contact.email}-${index}`} className="resource-wrap">
                    {contact.name} <a href={`mailto:${contact.email}`}>{contact.email}</a>
                  </li>
                ))}</ul>
              ) : 'None on record'}</dd>
            </div>
          </dl>
          {isAdmin && <LinkEditor item={item} onChange={onChange} />}
          {isAdmin && <div className="resource-card__actions"><Button size="sm" variant="danger" aria-label={`Delete ${item.title}`} onClick={onDelete}>Delete</Button></div>}
        </div>
      )}
    </div>
  );
}

function LinkEditor({ item, onChange }: { item: ResourceItem; onChange: (item: ResourceItem) => void }) {
  const [value, setValue] = useState(editableLink(item));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const waitingPath = item.stored_link?.startsWith('/') ? item.stored_link : null;

  const save = async (url: string | null) => {
    setBusy(true);
    setError('');
    try {
      const changed = await api.resources.setLink(item.id, url);
      onChange(changed);
      setValue(editableLink(changed));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not save the link.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="resource-link-editor">
      <label className="resource-label" htmlFor={`resource-link-${item.id}`}>SharePoint or Google Drive link</label>
      {waitingPath && !item.link && <p className="resource-note">Imported SharePoint path saved. Configure SHAREPOINT_BASE_URL to show it.</p>}
      <div className="resource-link-editor__row">
        <input id={`resource-link-${item.id}`} className="ui-input" type="url" value={value}
          placeholder="https://…" onChange={(event) => setValue(event.target.value)} />
        <Button size="sm" variant="secondary" disabled={busy || !value.trim()} onClick={() => save(value.trim())}>Save link</Button>
        {item.stored_link && <Button size="sm" variant="ghost" disabled={busy} onClick={() => save(null)}>Remove link</Button>}
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  );
}

function AddLinkDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: () => void }) {
  const [data, setData] = useState<ResourceLinkCreate>({ kind: 'past_project', title: '', semester: '', industry: '', disclosure: 'private', url: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const reset = () => {
    setError('');
    setData({ kind: 'past_project', title: '', semester: '', industry: '', disclosure: 'private', url: '' });
  };

  const field = (name: keyof ResourceLinkCreate, value: string) => setData((current) => ({ ...current, [name]: value }));
  const add = async () => {
    setBusy(true);
    setError('');
    try {
      await api.resources.create(data);
      reset();
      onAdded();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not add this resource.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open={open}
      title="Add linked resource"
      confirmLabel="Add resource"
      busy={busy}
      body={(
        <div className="resource-add-form">
          <Field label="Title"><input className="ui-input" value={data.title} required maxLength={200} onChange={(event) => field('title', event.target.value)} /></Field>
          <Field label="Type"><select className="ui-input" value={data.kind} onChange={(event) => field('kind', event.target.value)}>
            {orderKinds(['past_project', 'template', 'interview', 'survey', 'analytics', 'other']).map((kind) => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}
          </select></Field>
          <Field label="Semester (optional)"><input className="ui-input" value={data.semester} placeholder="Fall 2026" onChange={(event) => field('semester', event.target.value)} /></Field>
          <Field label="Industry (optional)"><input className="ui-input" value={data.industry} onChange={(event) => field('industry', event.target.value)} /></Field>
          {data.kind === 'past_project' && <Field label="Disclosure"><select className="ui-input" value={data.disclosure} onChange={(event) => field('disclosure', event.target.value)}>
            {DISCLOSURES.map((value) => <option key={value} value={value}>{disclosureLabel(value)}</option>)}
          </select></Field>}
          <Field label="SharePoint or Google Drive URL"><input className="ui-input" type="url" value={data.url} required placeholder="https://…" onChange={(event) => field('url', event.target.value)} /></Field>
          {error && <Notice tone="danger">{error}</Notice>}
        </div>
      )}
      onConfirm={add}
      onClose={() => { reset(); onClose(); }}
    />
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="resource-add-field"><span>{label}</span>{children}</label>;
}
