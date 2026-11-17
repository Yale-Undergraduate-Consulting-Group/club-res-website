import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type AlumniOverview } from '../../api';
import { useToast } from '../../contexts/useToast';
import { useUrlTab } from '../../lib/useUrlTab';
import AppTabMenu from '../AppTabMenu';
import DirectorySection from '../alumni/DirectorySection';
import EventsSection from '../alumni/EventsSection';
import InboxSection from '../alumni/InboxSection';
import PostsSection from '../alumni/PostsSection';
import { PortalContext, type PortalContextValue } from '../alumni/portalContext';
import { Button, ConfirmDialog, EmptyState, Notice, StatusBadge } from '../ui/Primitives';

const SECTIONS = ['access', 'news', 'events', 'inbox', 'directory'] as const;
type Section = (typeof SECTIONS)[number];
const SECTION_LABELS: Record<Section, string> = { access: 'Access', news: 'News', events: 'Events', inbox: 'Board inbox', directory: 'Directory' };

/** Club admins act as the board: their member session, the /api/admin/alumni routes. A 401 is handled by the shell. */
const ADMIN_PORTAL: PortalContextValue = {
  api: api.adminPortal,
  canWrite: true,
  fail: (error) => (error instanceof Error ? error.message : 'Something went wrong.'),
};

const OUTCOMES: Record<AlumniOverview['sign_ins'][number]['outcome'], { label: string; tone: 'success' | 'warning' | 'danger' }> = {
  signed_in: { label: 'Signed in', tone: 'success' },
  not_eligible: { label: 'Not on the list', tone: 'warning' },
  revoked: { label: 'Revoked', tone: 'danger' },
  identity_mismatch: { label: 'Other Google account', tone: 'danger' },
};

/**
 * Admin → Alumni: who may sign in on the alumni portal (/alumni/login), who did, who is on the board, and revocation,
 * plus the portal's news, events, board inbox and directory, which club admins manage as the board.
 * The server re-checks these lists on every alumni request, so a removal takes effect at once.
 */
export default function AlumniAccess() {
  const [section, setSection] = useUrlTab(SECTIONS, 'access', 'section');
  return (
    <PortalContext.Provider value={ADMIN_PORTAL}>
      <div className="space-y-6">
        <AppTabMenu
          tabs={SECTIONS.map((id) => ({ id, label: SECTION_LABELS[id] }))}
          active={section}
          onChange={(id) => setSection(id as Section)}
          label="Alumni sections"
        />
        {section === 'access' && <AccessSection />}
        {section === 'news' && <PostsSection />}
        {section === 'events' && <EventsSection />}
        {section === 'inbox' && <InboxSection />}
        {section === 'directory' && <DirectorySection />}
      </div>
    </PortalContext.Provider>
  );
}

function AccessSection() {
  const toast = useToast();
  const [data, setData] = useState<AlumniOverview | null>(null);
  const [error, setError] = useState('');
  const [domain, setDomain] = useState('');
  const [email, setEmail] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState('');
  const [deleting, setDeleting] = useState<AlumniOverview['accounts'][number] | null>(null);

  const load = useCallback(() => {
    api.admin.alumni.overview().then((overview) => { setData(overview); setError(''); }).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const run = async (label: string, action: () => Promise<unknown>, done: string) => {
    setBusy(label);
    try {
      await action();
      toast.addToast(done, 'success');
      load();
      return true;
    } catch (e) {
      toast.addToast((e as Error).message, 'error');
      return false;
    } finally {
      setBusy('');
    }
  };

  if (error) return <Notice tone="danger">Could not load alumni access: {error}</Notice>;
  if (!data) return <p className="text-sm text-slate-500" role="status">Loading…</p>;

  const addDomain = async (event: FormEvent) => {
    event.preventDefault();
    if (await run('domain', () => api.admin.alumni.addDomain(domain), `${domain.trim()} added.`)) setDomain('');
  };
  const addEmail = async (event: FormEvent) => {
    event.preventDefault();
    if (await run('email', () => api.admin.alumni.addEmail(email, note), `${email.trim()} added.`)) {
      setEmail('');
      setNote('');
    }
  };

  return (
    <div className="space-y-6">
      <Notice>
        Alumni sign in at <strong>/alumni/login</strong> with Google and use the alumni portal: news, events, the alumni
        directory and a way to contact the board. They see no club data: no members’ pages, contacts, outreach, projects or
        resources. Board members, marked below, post news and events and read the board inbox. Only club admins manage who may
        sign in.
      </Notice>

      <section className="surface-card space-y-3" aria-labelledby="alumni-domains">
        <h2 id="alumni-domains" className="font-semibold text-deep-navy">Alumni email domains</h2>
        <p className="text-sm text-slate-600">
          Anyone whose verified Google address is in one of these domains can sign in. Yale’s alumni YaleMail domain is
          aya.yale.edu; post-docs and Yale affiliates also have it. yale.edu and public mail providers are refused.
        </p>
        {data.domains.length === 0
          ? <p className="text-sm text-slate-500">No domains. Only the addresses below can sign in.</p>
          : (
            <ul className="space-y-2">
              {data.domains.map((row) => (
                <li key={row.domain} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-sm break-all">{row.domain}</span>
                  <Button size="sm" variant="secondary" disabled={busy !== ''} aria-label={`Remove ${row.domain}`}
                    onClick={() => run(`d:${row.domain}`, () => api.admin.alumni.removeDomain(row.domain), `${row.domain} removed.`)}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        <form className="flex flex-wrap gap-2" onSubmit={addDomain}>
          <input className="ui-input flex-1 min-w-0" aria-label="Domain" placeholder="aya.yale.edu" value={domain}
            maxLength={253} onChange={(e) => setDomain(e.target.value)} />
          <Button type="submit" disabled={!domain.trim() || busy !== ''}>Add domain</Button>
        </form>
      </section>

      <section className="surface-card space-y-3" aria-labelledby="alumni-emails">
        <h2 id="alumni-emails" className="font-semibold text-deep-navy">Individually allowed addresses</h2>
        <p className="text-sm text-slate-600">One person each, from any provider. The note is for admins only.</p>
        {data.allowlist.length === 0
          ? <p className="text-sm text-slate-500">No addresses yet.</p>
          : (
            <ul className="space-y-2">
              {data.allowlist.map((row) => (
                <li key={row.email} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0">
                    <span className="text-sm break-all">{row.email}</span>
                    {row.note && <span className="block text-xs text-slate-500 break-words">{row.note}</span>}
                  </span>
                  <Button size="sm" variant="secondary" disabled={busy !== ''} aria-label={`Remove ${row.email}`}
                    onClick={() => run(`e:${row.email}`, () => api.admin.alumni.removeEmail(row.email), `${row.email} removed.`)}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        <form className="flex flex-wrap gap-2" onSubmit={addEmail}>
          <input className="ui-input flex-[1_1_14rem] min-w-0" type="email" aria-label="Email address" placeholder="name@example.com"
            value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} />
          <input className="ui-input flex-[1_1_14rem] min-w-0" aria-label="Note (optional)" placeholder="Note, e.g. Class of 2022"
            value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} />
          <Button type="submit" disabled={!email.trim() || busy !== ''}>Add address</Button>
        </form>
      </section>

      <section className="surface-card space-y-3" aria-labelledby="alumni-accounts">
        <h2 id="alumni-accounts" className="font-semibold text-deep-navy">Alumni who have signed in</h2>
        {data.accounts.length === 0
          ? <EmptyState title="No alumni yet" body="Accounts appear after their first sign-in." />
          : (
            <ul className="divide-y divide-pale-sky">
              {data.accounts.map((account) => (
                <li key={account.id} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
                  <span className="min-w-0 flex-[1_1_16rem]">
                    <span className="block break-all">{account.email}</span>
                    <span className="block text-xs text-slate-500 break-words">
                      {account.name || 'No name'} · last sign-in {account.last_sign_in_at ? new Date(account.last_sign_in_at * 1000).toLocaleString() : 'never'}
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    {account.revoked_at
                      ? <StatusBadge tone="danger">Revoked</StatusBadge>
                      : account.allowed ? <StatusBadge tone="success">Active</StatusBadge> : <StatusBadge tone="warning">Not on the list</StatusBadge>}
                    {account.board && <StatusBadge tone="info">Board member</StatusBadge>}
                    <Button size="sm" variant="secondary" disabled={busy !== ''}
                      onClick={() => run(`b:${account.id}`, () => api.admin.alumni.setBoard(account.id, !account.board),
                        account.board ? `${account.email} removed from the board.` : `${account.email} is now a board member.`)}>
                      {account.board ? 'Remove from board' : 'Make board member'}
                    </Button>
                    {account.revoked_at
                      ? <Button size="sm" variant="secondary" disabled={busy !== ''}
                          onClick={() => run(`r:${account.id}`, () => api.admin.alumni.restore(account.id), `${account.email} restored.`)}>Restore</Button>
                      : <Button size="sm" variant="secondary" disabled={busy !== ''}
                          onClick={() => run(`v:${account.id}`, () => api.admin.alumni.revoke(account.id), `${account.email} revoked.`)}>Revoke</Button>}
                    <Button size="sm" variant="danger" disabled={busy !== ''} onClick={() => setDeleting(account)}>Delete</Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
      </section>

      <section className="surface-card space-y-3" aria-labelledby="alumni-sign-ins">
        <h2 id="alumni-sign-ins" className="font-semibold text-deep-navy">Recent alumni sign-ins</h2>
        <p className="text-sm text-slate-600">The latest 200, kept for a year. A refused address is recorded by its domain only.</p>
        {data.sign_ins.length === 0
          ? <p className="text-sm text-slate-500">No sign-ins yet.</p>
          : (
            <ul className="space-y-1 text-sm">
              {data.sign_ins.map((row, index) => (
                <li key={`${row.at}-${index}`} className="flex flex-wrap items-center gap-2">
                  <span className="whitespace-nowrap text-slate-500">{new Date(row.at * 1000).toLocaleString()}</span>
                  <StatusBadge tone={OUTCOMES[row.outcome].tone}>{OUTCOMES[row.outcome].label}</StatusBadge>
                  <span className="break-all">{row.email ?? `someone at ${row.email_domain}`}</span>
                </li>
              ))}
            </ul>
          )}
      </section>

      <ConfirmDialog
        open={deleting !== null}
        title="Delete alumni account?"
        body={deleting ? `${deleting.email}, its sign-ins and its directory profile are deleted. Posts and events they wrote stay, under a generic “YUCG Alumni Board” byline; messages they sent to the board stay, without a sender. If the address is still allowed it can sign in again; revoke instead to keep someone out. Daily backups keep a copy for up to 30 days.` : ''}
        confirmLabel="Delete"
        danger
        busy={busy === 'delete'}
        onConfirm={async () => {
          if (!deleting) return;
          await run('delete', () => api.admin.alumni.remove(deleting.id), `${deleting.email} deleted.`);
          setDeleting(null);
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}
