import { api, type Project } from '../api';
import { useCallback, useEffect, useState } from 'react';
import { workspaceRequest } from '../lib/workspaceApi';
import { ConfirmDialog } from './ui/Primitives';
type Invitation = { id: number; email: string; state: string; delivery_state: string; expires_at: number; delivery_error?: string };
type BulkResult = { email: string; status: 'created' | 'skipped'; reason?: string; id?: number; token?: string };
const emailsIn = (text: string) => [...new Set((text.match(/[^\s,;<>"']+@[^\s,;<>"']+/g) ?? []).map((e) => e.toLowerCase()))];
const deliveryLabel = (state: string) => ({
  ready: 'Ready to send',
  sending: 'Sending',
  sent: 'Accepted by Gmail',
  ambiguous: 'Delivery unconfirmed',
  manual: 'Share link directly',
}[state] ?? state);
export default function Invitations() {
  const [items, setItems] = useState<Invitation[]>([]);
  const [emails, setEmails] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [tokens, setTokens] = useState<Record<number, string>>({});
  const [links, setLinks] = useState<Record<number, string>>({});
  const [replaceItem, setReplaceItem] = useState<Invitation | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<{ created: number; skipped: BulkResult[] } | null>(null);
  const [sendReport, setSendReport] = useState('');
  const refresh = useCallback(async () => { setItems(await workspaceRequest<Invitation[]>('/api/admin/invitations')); }, []);
  useEffect(() => {
    workspaceRequest<Invitation[]>('/api/admin/invitations').then(setItems).catch((e: Error) => setError(e.message));
    api.admin.projects.list().then(setProjects).catch((e: Error) => setError(e.message));
  }, []);
  async function create() {
    setBusy(true); setError(''); setReport(null);
    try {
      const result = await workspaceRequest<{ results: BulkResult[]; created: number }>('/api/admin/invitations/bulk', 'POST', { emails: emailsIn(emails), project_ids: projectIds });
      const created = result.results.filter((r) => r.status === 'created' && r.id && r.token);
      setTokens(previous => ({ ...previous, ...Object.fromEntries(created.map((r) => [r.id, r.token])) }));
      setLinks(previous => ({ ...previous, ...Object.fromEntries(created.map((r) => [r.id, `${window.location.origin}/api/auth/google?invitation=${r.token}`])) }));
      setReport({ created: result.created, skipped: result.results.filter((r) => r.status === 'skipped') });
      setEmails('');
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); await refresh().catch((e: Error) => setError(e.message)); }
  }
  async function loadFile(file: File | undefined) {
    if (!file) return;
    const found = emailsIn(await file.text());
    setEmails((current) => [...new Set([...emailsIn(current), ...found])].join('\n'));
  }
  async function sendAll() {
    const ready = items.filter((item) => item.state === 'pending' && item.delivery_state === 'ready');
    setBusy(true); setError(''); setSendReport('');
    let sent = 0;
    const failed: string[] = [];
    for (const item of ready) {
      try {
        await workspaceRequest(`/api/admin/invitations/${item.id}/send`, 'POST', { token: tokens[item.id] });
        sent += 1;
      } catch (e) { failed.push(`${item.email}: ${(e as Error).message}`); }
    }
    setSendReport(`Sent ${sent} of ${ready.length}.${failed.length ? ` Not sent: ${failed.join('; ')}` : ''}`);
    setBusy(false);
    await refresh().catch((e: Error) => setError(e.message));
  }
  async function act(item: Invitation, send: boolean) {
    setBusy(true); setError('');
    try {
      if (send) await workspaceRequest(`/api/admin/invitations/${item.id}/send`, 'POST', { token: tokens[item.id] });
      else await workspaceRequest(`/api/admin/invitations/${item.id}`, 'DELETE');
      await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      setError('Could not copy automatically. Select the invitation link and copy it.');
    }
  }
  async function replaceLink() {
    if (!replaceItem) return;
    setBusy(true); setError('');
    try {
      const result = await workspaceRequest<{ link: string }>(`/api/admin/invitations/${replaceItem.id}/link`, 'POST');
      setLinks(previous => ({ ...previous, [replaceItem.id]: result.link }));
      setReplaceItem(null);
      await refresh();
      await copyLink(result.link);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="surface-card p-6 mb-6">
    <h2 className="app-section-title">Member invitations</h2>
    <p className="text-sm my-2">Paste Yale email addresses or upload a CSV (any column). Every address gets a result; then send the invitations from your connected Gmail account, one at a time or all at once.</p>
    <label className="block" htmlFor="invitation-emails">Email addresses</label>
    <textarea id="invitation-emails" className="w-full border p-2 my-2" value={emails} onChange={e => setEmails(e.target.value)} />
    <label className="block text-sm mb-3">Or upload a CSV <input type="file" accept=".csv,.txt,text/csv,text/plain" className="ml-2 text-sm" onChange={(e) => { void loadFile(e.target.files?.[0]); e.target.value = ''; }} /></label>
    <fieldset className="mb-3"><legend className="text-sm font-semibold">Project assignments on acceptance</legend>
      {!projects.length && <p className="text-sm">No projects available. Members can be assigned later.</p>}
      {projects.map(project => <label className="block text-sm py-1" key={project.id}><input type="checkbox" checked={projectIds.includes(project.id)} onChange={event => setProjectIds(previous => event.target.checked ? [...previous, project.id] : previous.filter(id => id !== project.id))} /> {project.name}</label>)}
    </fieldset>
    <div className="flex flex-wrap gap-2">
      <button className="ui-button ui-button--secondary" disabled={busy || emailsIn(emails).length === 0} onClick={() => void create()}>Create {emailsIn(emails).length || ''} invitation{emailsIn(emails).length === 1 ? '' : 's'}</button>
      <button className="ui-button ui-button--secondary" disabled={busy || !items.some((i) => i.state === 'pending' && i.delivery_state === 'ready')} onClick={() => void sendAll()}>Send all ready ({items.filter((i) => i.state === 'pending' && i.delivery_state === 'ready').length})</button>
    </div>
    {error && <p role="alert" className="text-red-700 my-2">{error}</p>}
    {report && <div role="status" className="ui-notice ui-notice--info my-3 text-sm">
      Created {report.created}{report.skipped.length ? `; skipped ${report.skipped.length}:` : '.'}
      {report.skipped.length > 0 && <ul className="mt-1 list-disc pl-5">{report.skipped.map((r) => <li key={r.email}>{r.email}: {r.reason}</li>)}</ul>}
    </div>}
    {sendReport && <p role="status" className="ui-notice ui-notice--info my-3 text-sm">{sendReport}</p>}
    <div className="overflow-x-auto"><table className="w-full mt-4 text-left"><thead><tr><th>Email</th><th>Membership</th><th>Delivery</th><th>Actions</th></tr></thead>
      <tbody>{items.map(item => <tr key={item.id}><td>{item.email}</td><td>{item.state}</td><td>{deliveryLabel(item.delivery_state)}{item.delivery_error && <p className="text-sm">{item.delivery_error}</p>}</td><td>
        {item.state === 'pending' && <div className="min-w-80 max-w-md space-y-2">
          {links[item.id] && <input aria-label={`Invitation link for ${item.email}`} className="ui-input w-full text-xs" readOnly value={links[item.id]} onFocus={event => event.currentTarget.select()} />}
          <div className={`grid gap-2 ${item.delivery_state === 'ready' ? 'grid-cols-3' : 'grid-cols-2'}`}>
            {links[item.id]
              ? <button className="ui-button ui-button--secondary h-full w-full" disabled={busy} onClick={() => void copyLink(links[item.id])}>Copy link</button>
              : <button className="ui-button ui-button--secondary h-full w-full" disabled={busy} onClick={() => setReplaceItem(item)}>Replace with shareable link</button>}
            {item.delivery_state === 'ready' && <button className="ui-button ui-button--secondary h-full w-full" disabled={busy} onClick={() => void act(item, true)}>Send invitation</button>}
            <button className="ui-button ui-button--secondary h-full w-full" disabled={busy} onClick={() => void act(item, false)}>Revoke</button>
          </div>
        </div>}
      </td></tr>)}</tbody></table></div>
    <p className="text-xs mt-2">Gmail accepting a send does not prove it reached the inbox. If it is missing, check spam first. A shareable replacement invalidates the emailed link; send the new link directly.</p>
    <ConfirmDialog open={!!replaceItem} title="Replace emailed invitation link?" body="This invalidates the link in the email. You will get a new link to copy and send directly." confirmLabel="Replace link" busy={busy} onConfirm={() => void replaceLink()} onClose={() => setReplaceItem(null)} />
  </section>;
}
