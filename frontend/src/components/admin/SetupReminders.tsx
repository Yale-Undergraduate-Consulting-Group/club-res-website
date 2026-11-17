import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { api, type ReminderPreview, type ReminderResult } from '../../api';
import { Button, Notice, StatusBadge } from '../ui/Primitives';
import { defaultSelection, remindedLabel, resultLabel } from '../../lib/setupReminders';

/**
 * Admin → Members: Slack-DM each member what is still missing from their setup.
 * The list is the server's own view of who is missing what; sending names only
 * member ids and the server works out the message again, so a stale dialog can
 * never remind someone about something they have since done. Members go one at
 * a time: Slack is paced at about one a second and each row shows its own result.
 */
export default function SetupReminders() {
  const [preview, setPreview] = useState<ReminderPreview | null>(null);
  const [loadError, setLoadError] = useState('');
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [results, setResults] = useState<Record<number, ReminderResult>>({});
  const [sending, setSending] = useState(false);
  const [finished, setFinished] = useState(false);
  const [sendError, setSendError] = useState('');
  const mounted = useRef(true);
  const titleId = useId();
  const load = useCallback(() => api.admin.users.setupReminders.preview()
    .then((next) => {
      if (!mounted.current) return;
      setPreview(next);
      setLoadError('');
      setSelected(defaultSelection(next.members));
    })
    .catch((e: Error) => {
      if (mounted.current) setLoadError(e.message || 'Could not load who is missing what');
    }), []);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; };
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape' && !sending) setOpen(false); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, sending]);

  const openDialog = () => {
    setResults({});
    setFinished(false);
    setSendError('');
    setOpen(true);
    void load();
  };

  const send = async () => {
    const ids = (preview?.members ?? []).filter((m) => selected.has(m.user_id)).map((m) => m.user_id);
    setSending(true);
    setSendError('');
    try {
      for (const id of ids) {
        const { results: got } = await api.admin.users.setupReminders.send([id]);
        if (!mounted.current) return;
        setResults((prev) => ({ ...prev, ...Object.fromEntries(got.map((r) => [r.user_id, r])) }));
      }
    } catch (e) {
      if (mounted.current) setSendError((e as Error).message || 'Sending stopped');
    }
    if (!mounted.current) return;
    setSending(false);
    setFinished(true);
    void api.admin.users.setupReminders.preview().then((next) => { if (mounted.current) setPreview(next); }).catch(() => {});
  };

  const members = preview?.members ?? [];
  const slackOff = preview !== null && !preview.slack_connected;
  const picked = members.filter((m) => selected.has(m.user_id)).length;
  const tally = (status: ReminderResult['status']) => Object.values(results).filter((r) => r.status === status).length;

  return (
    <>
      <Button
        variant="secondary"
        disabled={(preview === null && !loadError) || slackOff}
        title={slackOff ? 'The club Slack bot has no usable token. Connect Slack first.' : undefined}
        onClick={openDialog}
        data-testid="setup-reminders-open"
      >
        {slackOff ? 'Slack not connected' : 'Send setup reminders'}
      </Button>
      {open && (
        <div className="ui-dialog-root" role="presentation" data-testid="setup-reminders">
          <button className="ui-dialog-backdrop" aria-label="Close dialog" onClick={() => setOpen(false)} disabled={sending} />
          <section
            className="ui-dialog"
            style={{ width: 'min(40rem, 100%)', minWidth: 0, maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
            role="dialog" aria-modal="true" aria-labelledby={titleId}
          >
            <h2 id={titleId}>Send setup reminders</h2>
            <p className="mt-2 text-sm text-[var(--ink-muted)]">
              Each member ticked below gets one Slack message from the club bot listing only what they are missing,
              with links to fix it. Gmail comes first: nobody can send without it.
            </p>

            <div className="mt-3 min-h-0 flex-1 overflow-y-auto" data-testid="setup-reminders-list">
              {loadError && <Notice tone="danger">{loadError}</Notice>}
              {!loadError && preview === null && <p className="text-sm text-[var(--ink-muted)]">Loading…</p>}
              {preview && members.length === 0 && <Notice tone="success">Everyone has finished setting up.</Notice>}
              {members.length > 0 && (
                <ul>
                  {members.map((m) => {
                    const result = results[m.user_id];
                    const outcome = result ? resultLabel(result) : null;
                    return (
                      <li key={m.user_id} className="flex items-start gap-3 border-b border-[var(--border)] py-2" data-testid={`reminder-${m.user_id}`}>
                        <input
                          type="checkbox"
                          className="mt-1"
                          aria-label={`Remind ${m.name || m.email}`}
                          checked={selected.has(m.user_id)}
                          disabled={sending || finished}
                          onChange={(e) => setSelected((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(m.user_id); else next.delete(m.user_id);
                            return next;
                          })}
                        />
                        <div className="min-w-0 flex-1 text-sm">
                          <div className="[overflow-wrap:anywhere] font-medium text-[var(--ink)]">
                            {m.name || m.email}
                            {m.name && <span className="ml-2 font-normal text-[var(--ink-muted)]">{m.email}</span>}
                          </div>
                          <div className="[overflow-wrap:anywhere] text-xs text-[var(--ink-muted)]">
                            Missing: {m.missing.map((i) => i.label).join(', ')}
                          </div>
                          {m.reminded_at && <div className="text-xs text-[var(--ink-muted)]">{remindedLabel(m.reminded_at)}</div>}
                        </div>
                        {outcome && <StatusBadge tone={outcome.tone}>{outcome.text}</StatusBadge>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {sendError && <Notice tone="danger" className="mt-3">{sendError}</Notice>}
            {finished && !sendError && (
              <Notice tone="info" className="mt-3">
                {tally('sent')} sent, {tally('not_found')} not found in Slack, {tally('failed')} failed.
              </Notice>
            )}
            <div className="ui-dialog__actions">
              {finished ? (
                <Button onClick={() => setOpen(false)}>Close</Button>
              ) : (
                <>
                  <Button variant="secondary" onClick={() => setOpen(false)} disabled={sending}>Cancel</Button>
                  <Button onClick={() => void send()} disabled={sending || picked === 0 || slackOff}>
                    {sending ? `Sending… ${Object.keys(results).length} of ${picked}` : `Send to ${picked} ${picked === 1 ? 'member' : 'members'}`}
                  </Button>
                </>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
