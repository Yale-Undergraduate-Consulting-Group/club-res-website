import { useEffect, useState } from 'react';
import { api, type AppAccess as AppAccessRow, type Member } from '../../api';
import { useToast } from '../../contexts/useToast';
import { Button, Chip, EmptyState, Notice, StatusBadge } from '../ui/Primitives';

/**
 * Who may use each app. Every app is open to all active members until an
 * admin restricts it; a restricted app is open to the members ticked here and
 * to admins. The server checks this on every route and background job.
 */
export default function AppAccess() {
  const toast = useToast();
  const [apps, setApps] = useState<AppAccessRow[] | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [drafts, setDrafts] = useState<Record<string, AppAccessRow>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([api.admin.apps.list(), api.admin.users.list()])
      .then(([rows, users]) => {
        setApps(rows);
        setMembers(users.filter((u) => u.is_active && u.role !== 'system'));
        setDrafts(Object.fromEntries(rows.map((row) => [row.app, row])));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <Notice tone="danger">Could not load app access: {error}</Notice>;
  if (!apps) return <p className="text-sm text-slate-500">Loading…</p>;
  if (apps.length === 0) return <EmptyState title="No apps yet" body="Apps added to the platform appear here." />;

  const save = async (app: string) => {
    const draft = drafts[app];
    setSaving(app);
    try {
      await api.admin.apps.update(app, { restricted: draft.restricted, member_ids: draft.restricted ? draft.member_ids : [] });
      setApps((rows) => rows?.map((row) => (row.app === app ? draft : row)) ?? rows);
      toast.addToast(`${draft.title}: ${draft.restricted ? 'restricted' : 'open to all members'}.`, 'success');
    } catch (e) {
      toast.addToast((e as Error).message, 'error');
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Every app is open to all active members unless you restrict it. Admins can always use every app.
      </p>
      {apps.map((saved) => {
        const draft = drafts[saved.app];
        const changed = JSON.stringify(draft) !== JSON.stringify(saved);
        return (
          <section key={saved.app} className="surface-card rounded-xl p-6 space-y-3" data-testid={`app-access-${saved.app}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold text-deep-navy">{saved.title}</h2>
              <StatusBadge tone={saved.restricted ? 'warning' : 'success'}>
                {saved.restricted ? `Restricted · ${saved.member_ids.length} member(s)` : 'All members'}
              </StatusBadge>
            </div>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={`Who may use ${saved.title}`}>
              <Chip role="radio" selected={!draft.restricted}
                    onClick={() => setDrafts((d) => ({ ...d, [saved.app]: { ...draft, restricted: false } }))}>
                All members
              </Chip>
              <Chip role="radio" selected={draft.restricted}
                    onClick={() => setDrafts((d) => ({ ...d, [saved.app]: { ...draft, restricted: true } }))}>
                Only chosen members
              </Chip>
            </div>
            {draft.restricted && (
              <fieldset className="grid gap-1 sm:grid-cols-2">
                <legend className="mb-1 text-xs font-medium text-slate-600">Members who may use {saved.title}</legend>
                {members.map((m) => (
                  <label key={m.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={draft.member_ids.includes(m.id)}
                      onChange={(e) => setDrafts((d) => ({
                        ...d,
                        [saved.app]: {
                          ...draft,
                          member_ids: e.target.checked
                            ? [...draft.member_ids, m.id].sort((a, b) => a - b)
                            : draft.member_ids.filter((id) => id !== m.id),
                        },
                      }))}
                    />
                    <span>{m.name || m.email}</span>
                    {m.role === 'admin' && <span className="text-xs text-slate-500">(admin, always allowed)</span>}
                  </label>
                ))}
              </fieldset>
            )}
            <Button size="sm" disabled={!changed || saving === saved.app} onClick={() => void save(saved.app)}>
              {saving === saved.app ? 'Saving…' : 'Save'}
            </Button>
          </section>
        );
      })}
    </div>
  );
}
