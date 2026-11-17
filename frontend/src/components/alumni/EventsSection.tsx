import { useCallback, useMemo, useState, type FormEvent } from 'react';
import type { PortalEvent } from '../../api';
import { browserTimeZone, onlineLinkProblem, timeZoneNames } from '../../lib/alumniPortal';
import { Button, ConfirmDialog, EmptyState, Notice } from '../ui/Primitives';
import PortalEditor from './PortalEditor';
import { EventCard } from './PortalCards';
import { usePortal, useLoad } from './portalContext';

/** Events: upcoming and past, each in its own time zone; the board adds, edits and deletes. */
export default function EventsSection() {
  const { api, canWrite, fail } = usePortal();
  const load = useCallback(() => api.events.list(), [api]);
  const { data, error, refresh } = useLoad(load);
  const [editing, setEditing] = useState<PortalEvent | 'new' | null>(null);
  const [deleting, setDeleting] = useState<PortalEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.events.remove(deleting.id);
      setNotice(`“${deleting.title}” deleted.`);
      setFailure('');
      refresh();
    } catch (e) {
      setFailure(fail(e));
    } finally {
      setBusy(false);
      setDeleting(null);
    }
  };

  if (editing) {
    return (
      <EventForm
        event={editing === 'new' ? null : editing}
        onDone={(message) => { setEditing(null); setNotice(message); refresh(); }}
        onCancel={() => setEditing(null)}
      />
    );
  }

  const actions = (event: PortalEvent) => canWrite && (
    <>
      <Button size="sm" variant="secondary" aria-label={`Edit ${event.title}`} onClick={() => { setNotice(''); setEditing(event); }}>Edit</Button>
      <Button size="sm" variant="danger" aria-label={`Delete ${event.title}`} onClick={() => setDeleting(event)}>Delete</Button>
    </>
  );

  return (
    <section className="space-y-6" aria-labelledby="portal-events">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="portal-events" className="font-semibold text-deep-navy">Events</h2>
        {canWrite && <Button onClick={() => { setNotice(''); setEditing('new'); }}>New event</Button>}
      </div>
      {notice && <Notice tone="success">{notice}</Notice>}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {error && <Notice tone="danger">Could not load events: {error}</Notice>}
      {!data && !error && <p className="text-sm text-slate-500" role="status">Loading…</p>}
      {data && (
        <>
          <div className="space-y-3" role="region" aria-label="Upcoming events">
            <h3 className="font-semibold text-deep-navy">Upcoming</h3>
            {data.upcoming.length === 0
              ? <EmptyState title="No upcoming events" body="Events the alumni board schedules appear here." />
              : data.upcoming.map((event) => <EventCard key={event.id} event={event} actions={actions(event)} />)}
          </div>
          <div className="space-y-3" role="region" aria-label="Past events">
            <h3 className="font-semibold text-deep-navy">Past</h3>
            {data.past.length === 0
              ? <p className="text-sm text-slate-500">No past events.</p>
              : data.past.map((event) => <EventCard key={event.id} event={event} actions={actions(event)} />)}
          </div>
        </>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this event?"
        body={deleting ? `“${deleting.title}” is deleted for everyone. This cannot be undone.` : ''}
        confirmLabel="Delete event"
        danger
        busy={busy}
        onConfirm={remove}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}

function EventForm({ event, onDone, onCancel }: { event: PortalEvent | null; onDone: (message: string) => void; onCancel: () => void }) {
  const { api, fail } = usePortal();
  const [title, setTitle] = useState(event?.title ?? '');
  const [startsLocal, setStartsLocal] = useState(event?.starts_local ?? '');
  const [timezone, setTimezone] = useState(event?.timezone ?? browserTimeZone());
  const [place, setPlace] = useState(event?.place ?? '');
  const [onlineUrl, setOnlineUrl] = useState(event?.online_url ?? '');
  const [description, setDescription] = useState(event?.description_html ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const zones = useMemo(() => timeZoneNames([timezone]), [timezone]);

  const save = async (submitted: FormEvent) => {
    submitted.preventDefault();
    const problem = !title.trim() ? 'Give the event a title.'
      : !startsLocal ? 'Enter the date and time of the event.'
      : !place.trim() && !onlineUrl.trim() ? 'Give a place, an online link, or both.'
      : onlineLinkProblem(onlineUrl);
    if (problem) { setError(problem); return; }
    setSaving(true);
    setError('');
    const input = {
      title: title.trim(), starts_local: startsLocal, timezone,
      place: place.trim() || null, online_url: onlineUrl.trim() || null, description_html: description,
    };
    try {
      if (event) await api.events.update(event.id, input);
      else await api.events.create(input);
      onDone(event ? 'Event saved.' : 'Event added.');
    } catch (e) {
      setError(fail(e));
      setSaving(false);
    }
  };

  return (
    <form className="surface-card space-y-4" onSubmit={save} noValidate aria-label={event ? 'Edit event' : 'New event'}>
      <h2 className="font-semibold text-deep-navy">{event ? 'Edit event' : 'New event'}</h2>
      {error && <Notice tone="danger">{error}</Notice>}
      <label className="block space-y-1">
        <span className="block text-sm font-medium text-deep-navy">Title</span>
        <input className="ui-input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="flex flex-wrap gap-4">
        <label className="block space-y-1 flex-[1_1_14rem] min-w-0">
          <span className="block text-sm font-medium text-deep-navy">Date and time</span>
          <input className="ui-input" type="datetime-local" value={startsLocal} onChange={(e) => setStartsLocal(e.target.value)} />
        </label>
        <label className="block space-y-1 flex-[1_1_14rem] min-w-0">
          <span className="block text-sm font-medium text-deep-navy">Time zone</span>
          <select className="ui-input" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {zones.map((zone) => <option key={zone} value={zone}>{zone}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs text-slate-500">The time is where the event happens; alumni elsewhere see it in that zone.</p>
      <div className="flex flex-wrap gap-4">
        <label className="block space-y-1 flex-[1_1_14rem] min-w-0">
          <span className="block text-sm font-medium text-deep-navy">Place</span>
          <input className="ui-input" value={place} maxLength={200} onChange={(e) => setPlace(e.target.value)} />
        </label>
        <label className="block space-y-1 flex-[1_1_14rem] min-w-0">
          <span className="block text-sm font-medium text-deep-navy">Online link</span>
          <input className="ui-input" type="url" placeholder="https://" value={onlineUrl} maxLength={500} onChange={(e) => setOnlineUrl(e.target.value)} />
        </label>
      </div>
      <PortalEditor label="Event description" initialHtml={event?.description_html ?? ''} onChange={setDescription} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving}>{saving ? 'Saving…' : event ? 'Save event' : 'Add event'}</Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </form>
  );
}
