import { useCallback, useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { workspaceRequest } from '../lib/workspaceApi';
type Project = { id: number; name: string; semester?: string; description?: string };
type Document = { id: number; title: string; owner_user_id: number; owner_name: string; owner_email: string; project_name?: string; project_id?: number; visibility: 'private' | 'project' | 'club'; current_version: number; revision: number };
type Version = { state: 'pending' | 'ready' | 'failed'; id: number; filename: string; byte_size: number; created_at: number };
type Share = { id: number; expires_at: number; revoked_at?: number };
export default function Documents({ projectsOnly = false }: { projectsOnly?: boolean }) {
  const { user } = useOutletContext<{ user: { id?: number } }>();
  const [projects, setProjects] = useState<Project[]>([]);
  const [items, setItems] = useState<Document[]>([]);
  const [q, setQ] = useState('');
  const [title, setTitle] = useState('');
  const [visibility, setVisibility] = useState<Document['visibility']>('private');
  const [projectId, setProjectId] = useState('');
  const [quota, setQuota] = useState<{ quota_bytes: number; reserved_bytes: number; available_bytes: number } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Document | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [shares, setShares] = useState<Share[]>([]);
  const [shareUrl, setShareUrl] = useState('');
  const refresh = useCallback(async () => {
    const [docs, groups, usage] = await Promise.all([
      workspaceRequest<Document[]>(`/api/workspace/documents?q=${encodeURIComponent(q)}`),
      workspaceRequest<Project[]>('/api/workspace/projects'),
      workspaceRequest<{ quota_bytes: number; reserved_bytes: number; available_bytes: number }>('/api/workspace/storage-quota'),
    ]);
    setItems(docs); setProjects(groups);
    setSelected(previous => previous ? docs.find(item => item.id === previous.id) || null : null);
    if (![usage.quota_bytes, usage.reserved_bytes, usage.available_bytes].every(value => Number.isFinite(value) && value >= 0)) {
      setQuota(null); throw new Error('Storage usage is unavailable. Refresh before starting another upload.');
    }
    setQuota(usage);
  }, [q]);
  useEffect(() => { const timer = setTimeout(() => { void refresh().catch((e: Error) => setError(e.message)); }, 200); return () => clearTimeout(timer); }, [refresh]);
  async function execute(work: () => Promise<void>) {
    setBusy(true); setError('');
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function details(document: Document) {
    setSelected(previous => previous?.id === document.id ? previous : document); setShareUrl('');
    setVersions(await workspaceRequest<Version[]>(`/api/workspace/documents/${document.id}/versions`));
    setShares(document.owner_user_id === user.id ? await workspaceRequest<Share[]>(`/api/workspace/documents/${document.id}/shares`) : []);
  }
  async function upload(document: Document, file: File) {
    if (!quota) throw new Error('Storage usage must be available before uploading.');
    if (file.size > quota.available_bytes) throw new Error('This file exceeds your available storage.');
    if (file.size > 100 * 1024 * 1024) throw new Error('Files must be 100 MB or smaller.');
    const result = await workspaceRequest<{ version_id: number; upload: { url: string; method: string; headers: Record<string, string> } }>(`/api/workspace/documents/${document.id}/uploads`, 'POST', { filename: file.name, byte_size: file.size, content_type: file.type || 'application/octet-stream' });
    const response = await fetch(result.upload.url, { method: result.upload.method, headers: result.upload.headers, body: file });
    if (!response.ok) throw new Error('File transfer failed. The pending upload has not been published.');
    await workspaceRequest(`/api/workspace/documents/${document.id}/uploads/${result.version_id}/complete`, 'POST');
    await refresh(); await details(document);
  }
  async function download(document: Document, version?: number) {
    const result = await workspaceRequest<{ url: string }>(`/api/workspace/documents/${document.id}/download${version ? `?version_id=${version}` : ''}`);
    window.location.assign(result.url);
  }
  return <div className="app-workspace">
    <PageHeader title={projectsOnly ? 'Projects' : 'Documents'} subtitle={projectsOnly ? 'Your project memberships and shared records.' : 'Private files, project documents and the club library.'} />
    {quota && !projectsOnly && <p className="text-sm my-3">Storage reserved: {(quota.reserved_bytes / 1024 ** 2).toFixed(1)} MB of {(quota.quota_bytes / 1024 ** 2).toFixed(0)} MB, including pending uploads.</p>}
    {error && <p role="alert" className="ui-notice ui-notice--danger">{error}</p>}
    {projectsOnly ? <div className="grid gap-4 md:grid-cols-2">{projects.map(project => <section className="surface-card p-5" key={project.id}><h2 className="app-section-title">{project.name}</h2><p>{project.semester}</p><p>{project.description}</p><h3 className="font-semibold mt-4">Documents you can access</h3>{items.filter(doc => doc.project_id === project.id).map(doc => <button className="block underline" key={doc.id} onClick={() => void execute(() => details(doc))}>{doc.title}</button>)}</section>)}{!projects.length && <p>No project assignments yet. Ask a club administrator to assign your projects.</p>}</div> : <>
      <section className="surface-card p-5 mb-5">
        <h2 className="app-section-title">Register a document</h2>
        <label className="block my-2">Title<input className="block w-full border p-2" value={title} onChange={e => setTitle(e.target.value)} /></label>
        <div className="grid gap-3 sm:flex sm:flex-wrap sm:items-end"><label className="block text-sm">Visibility <select className="block w-full mt-1 border rounded px-3 py-2 bg-white" value={visibility} onChange={e => setVisibility(e.target.value as Document['visibility'])}><option value="private">Only me</option><option value="project">Project members</option><option value="club">Club library</option></select></label>
        <label className="block text-sm">Project <select className="block w-full mt-1 border rounded px-3 py-2 bg-white" value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">No project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <button disabled={busy || !title.trim() || (visibility === 'project' && !projectId)} className="ui-button ui-button--primary" onClick={() => void execute(async () => { await workspaceRequest('/api/workspace/documents', 'POST', { title, visibility, project_id: projectId ? Number(projectId) : null }); setTitle(''); await refresh(); })}>Create record</button></div>
      </section>
      <label className="block mb-3">Search documents<input className="block w-full sm:w-72 mt-1 border p-2" value={q} onChange={e => setQ(e.target.value)} /></label>
      <div className="hidden md:block overflow-x-auto surface-card p-4"><table className="w-full text-left"><thead><tr><th>Document</th><th>Owner</th><th>Project</th><th>Visibility</th><th>Status</th></tr></thead><tbody>{items.map(doc => <tr className="border-t border-[var(--border)]" key={doc.id}><td><button className="underline py-3" onClick={() => void execute(() => details(doc))}>{doc.title}</button></td><td>{doc.owner_name || doc.owner_email}</td><td>{doc.project_name || '—'}</td><td>{doc.visibility === 'private' ? 'Only owner' : doc.visibility === 'project' ? 'Project members' : 'Club library'}</td><td>{doc.current_version ? 'Available' : 'Awaiting file'}</td></tr>)}</tbody></table></div>
      <div className="grid gap-3 md:hidden">{items.map(doc => <article className="surface-card p-4" key={doc.id}>
        <button className="font-semibold text-left underline" onClick={() => void execute(() => details(doc))}>{doc.title}</button>
        <dl className="text-sm mt-3 space-y-2"><div><dt className="font-semibold inline">Owner: </dt><dd className="inline break-all">{doc.owner_name || doc.owner_email}</dd></div><div><dt className="font-semibold inline">Project: </dt><dd className="inline">{doc.project_name || '—'}</dd></div><div><dt className="font-semibold inline">Access: </dt><dd className="inline">{doc.visibility === 'private' ? 'Only owner' : doc.visibility === 'project' ? 'Project members' : 'Club library'}</dd></div><div><dt className="font-semibold inline">Status: </dt><dd className="inline">{doc.current_version ? 'Available' : 'Awaiting file'}</dd></div></dl>
      </article>)}</div>
      {!items.length && <p>No matching documents.</p>}
    </>}
    {selected && <section className="surface-card p-5 mt-5" aria-label="Document details"><h2 className="app-section-title">{selected.title}</h2><button className="ui-button ui-button--secondary my-2" disabled={busy || !selected.current_version} onClick={() => void execute(() => download(selected))}>Download latest</button>
      {selected.owner_user_id === user.id && <><label className="block my-3">Visibility <select disabled={busy} value={selected.visibility} onChange={e => { const next = e.target.value as Document['visibility']; void execute(async () => { await workspaceRequest(`/api/workspace/documents/${selected.id}`, 'PUT', { title: selected.title, visibility: next, project_id: selected.project_id ?? null, revision: selected.revision }); setSelected({ ...selected, visibility: next, revision: selected.revision + 1 }); await refresh(); }); }}><option value="private">Only me</option><option value="project" disabled={!selected.project_id}>Project members</option><option value="club">Club library</option></select></label><label className="block my-3">Upload a version (maximum 100 MB)<input type="file" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void execute(() => upload(selected, file)); e.target.value = ''; }} /></label>
        <p className="text-sm">External links grant anyone with the link download access for 24 hours. Revocation stops new downloads; already-issued URLs last up to 60 seconds.</p>
        <button className="ui-button ui-button--secondary my-2" disabled={busy} onClick={() => void execute(async () => { const result = await workspaceRequest<{ url: string }>(`/api/workspace/documents/${selected.id}/shares`, 'POST'); setShareUrl(result.url); setShares(await workspaceRequest<Share[]>(`/api/workspace/documents/${selected.id}/shares`)); })}>Create external share link</button>
        {shareUrl && <label className="block">Copy share link<input className="block w-full border p-2" readOnly value={shareUrl} onFocus={e => e.target.select()} /></label>}
        {shares.filter(share => !share.revoked_at).map(share => <div key={share.id}>Link {share.id}: expires {new Date(share.expires_at * 1000).toLocaleString()} <button className="ui-button ui-button--secondary" disabled={busy} onClick={() => void execute(async () => { await workspaceRequest(`/api/workspace/documents/${selected.id}/shares/${share.id}`, 'DELETE'); await details(selected); })}>Revoke</button></div>)}
      </>}
      <h3 className="font-semibold mt-4">Version history</h3>{versions.map(version => <div className="py-3 border-b" key={version.id}>
        {version.state === 'ready' ? <button className="underline" disabled={busy} onClick={() => void execute(() => download(selected, version.id))}>{version.filename}</button> : <span>{version.filename}</span>}
        <p className="text-sm">{version.byte_size.toLocaleString()} bytes · {new Date(version.created_at * 1000).toLocaleString()} · {version.state}</p>
        {version.state === 'pending' && selected.owner_user_id === user.id && <div className="flex flex-wrap gap-2 mt-2">
          <button className="ui-button ui-button--secondary" disabled={busy} onClick={() => void execute(async () => { await workspaceRequest(`/api/workspace/documents/${selected.id}/uploads/${version.id}/complete`, 'POST'); await refresh(); await details(selected); })}>Check upload</button>
          <button className="ui-button ui-button--secondary" disabled={busy} onClick={() => void execute(async () => { await workspaceRequest(`/api/workspace/documents/${selected.id}/uploads/${version.id}/abandon`, 'POST'); await refresh(); await details(selected); })}>Cancel unused upload</button>
          <p className="text-sm">Unused reservations can be released after their upload link expires. Uploaded data cannot be silently discarded.</p>
        </div>}
      </div>)}
    </section>}
  </div>;
}
