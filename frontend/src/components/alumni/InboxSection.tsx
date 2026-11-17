import { useCallback, useEffect, useState } from 'react';
import type { InboxMessage } from '../../api';
import { Button, Chip, ConfirmDialog, EmptyState, Notice, StatusBadge } from '../ui/Primitives';
import { formatDateTime, usePortal, useLoad } from './portalContext';

const senderLabel = (message: InboxMessage) =>
  message.sender ? (message.sender.name ? `${message.sender.name} (${message.sender.email})` : message.sender.email) : 'Deleted account';

/** The board inbox: messages alumni sent through the portal. Opening one marks it read. */
export default function InboxSection({ onUnread }: { onUnread?: (count: number) => void }) {
  const { api, fail } = usePortal();
  const [archived, setArchived] = useState(false);
  const load = useCallback(() => api.inbox.list(archived), [api, archived]);
  const { data, error, refresh } = useLoad(load);
  const [open, setOpen] = useState<InboxMessage | null>(null);
  const [opening, setOpening] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<InboxMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => { if (data) onUnread?.(data.unread); }, [data, onUnread]);

  const show = (value: boolean) => { setArchived(value); setOpen(null); setNotice(''); setFailure(''); };

  const openMessage = async (message: InboxMessage) => {
    setOpening(message.id);
    setFailure('');
    setNotice('');
    try {
      setOpen(await api.inbox.open(message.id));
      refresh();
    } catch (e) {
      setFailure(fail(e));
    } finally {
      setOpening(null);
    }
  };

  const change = async (message: InboxMessage, action: 'unread' | 'archive' | 'restore', done: string) => {
    setBusy(true);
    setFailure('');
    try {
      await api.inbox.change(message.id, action);
      setOpen(null);
      setNotice(done);
      refresh();
    } catch (e) {
      setFailure(fail(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.inbox.remove(deleting.id);
      setOpen(null);
      setNotice('Message deleted.');
      refresh();
    } catch (e) {
      setFailure(fail(e));
    } finally {
      setBusy(false);
      setDeleting(null);
    }
  };

  return (
    <section className="space-y-4" aria-labelledby="portal-inbox">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="portal-inbox" className="font-semibold text-deep-navy">Board inbox</h2>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Messages to show">
          <Chip role="radio" selected={!archived} onClick={() => show(false)}>Open</Chip>
          <Chip role="radio" selected={archived} onClick={() => show(true)}>Archived</Chip>
        </div>
      </div>
      {notice && <Notice tone="success">{notice}</Notice>}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {error && <Notice tone="danger">Could not load the inbox: {error}</Notice>}
      {!data && !error && <p className="text-sm text-slate-500" role="status">Loading…</p>}
      {data && data.items.length === 0 && (
        <EmptyState title={archived ? 'No archived messages' : 'Inbox is empty'} body={archived ? 'Archived messages are listed here until deleted.' : 'Messages alumni send to the board appear here.'} />
      )}
      {data && data.items.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <ul className="space-y-2 min-w-0">
            {data.items.map((message) => (
              <li key={message.id} className="min-w-0">
                <button
                  type="button"
                  className={`surface-card w-full min-w-0 text-left space-y-1 ${open?.id === message.id ? 'ring-2 ring-[var(--accent)]' : ''}`}
                  aria-current={open?.id === message.id}
                  disabled={opening !== null}
                  onClick={() => void openMessage(message)}
                >
                  <span className="flex flex-wrap items-center gap-2 min-w-0">
                    <span className={`break-words min-w-0 text-deep-navy ${message.read ? '' : 'font-bold'}`}>{message.subject}</span>
                    {!message.read && <StatusBadge tone="info">Unread</StatusBadge>}
                  </span>
                  <span className="block text-xs text-slate-500 break-all">{senderLabel(message)}</span>
                  <span className="block text-xs text-slate-500">{formatDateTime(message.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {open
              ? (
                <article className="surface-card space-y-3 min-w-0" aria-label="Open message">
                  <h3 className="font-semibold text-deep-navy break-words">{open.subject}</h3>
                  <p className="text-xs text-slate-500 break-all">{senderLabel(open)} · {formatDateTime(open.created_at)}</p>
                  <p className="text-sm text-slate-800 whitespace-pre-wrap break-words">{open.body}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => change(open, 'unread', 'Marked unread.')}>Mark unread</Button>
                    {open.archived
                      ? <Button size="sm" variant="secondary" disabled={busy} onClick={() => change(open, 'restore', 'Message restored to the inbox.')}>Restore</Button>
                      : <Button size="sm" variant="secondary" disabled={busy} onClick={() => change(open, 'archive', 'Message archived.')}>Archive</Button>}
                    {open.archived && <Button size="sm" variant="danger" disabled={busy} onClick={() => setDeleting(open)}>Delete</Button>}
                  </div>
                </article>
              )
              : <p className="text-sm text-slate-500">Select a message to read it.</p>}
          </div>
        </div>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this message?"
        body="The message is deleted for the whole board. This cannot be undone."
        confirmLabel="Delete message"
        danger
        busy={busy}
        onConfirm={remove}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}
