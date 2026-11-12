import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BuildResult, type SendAllowance, type Template } from '../../api';
import { Button, Notice } from '../ui/Primitives';

/** Who the outreach is for: a target list the server resolves (and checks the
 *  member may read), or contacts picked by hand. */
export type OutreachSource = { label: string; targetListId?: number; contactIds?: number[] };

/** Held people listed before "and N more"; the count is always the whole. */
const HELD_SHOWN = 6;

const message = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

/**
 * One panel from a list of people to a released campaign: choose a template,
 * read who will and will not be written to (and why), then create the campaign
 * and release it. The preview is the same /build call that makes the campaign,
 * so what is read here is what is created. Nothing is sent by choosing a
 * template; sending starts only at the final button, and the member's daily
 * limit still paces it.
 */
export default function QueueOutreach({ source, onClose }: { source: OutreachSource; onClose: () => void }) {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templateId, setTemplateId] = useState<number | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [done, setDone] = useState<{ campaignId: number; queued: number; allowance?: SendAllowance } | null>(null);
  const titleId = useId();
  const selectRef = useRef<HTMLSelectElement>(null);
  const latest = useRef({ busy, onClose });
  useEffect(() => { latest.current = { busy, onClose }; }, [busy, onClose]);

  useEffect(() => {
    api.outreach.templates.list()
      .then(setTemplates)
      .catch((e) => { setTemplates([]); setError(message(e, 'Could not load templates')); });
  }, []);

  useEffect(() => {
    selectRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !latest.current.busy) latest.current.onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  // Keyed on the values, not the object, so a caller that builds `source` on
  // every render does not restart the preview each time.
  const { targetListId } = source;
  const idsKey = (source.contactIds ?? []).join(',');
  const target = useMemo(
    () => (targetListId ? { target_list_id: targetListId } : { contact_ids: idsKey ? idsKey.split(',').map(Number) : [] }),
    [targetListId, idsKey],
  );

  // The answer to one request, tagged with that request's key: a request still
  // in flight is simply one nobody has answered yet.
  const requestKey = `${templateId}|${targetListId ?? idsKey}`;
  const [settled, setSettled] = useState<{ key: string; result?: BuildResult; error?: string } | null>(null);
  useEffect(() => {
    if (templateId === '') return;
    let live = true;
    api.campaigns.build({ ...target, template_id: templateId, preview_only: true })
      .then((result) => { if (live) setSettled({ key: requestKey, result }); })
      .catch((e) => { if (live) setSettled({ key: requestKey, error: message(e, 'Could not preview this template') }); });
    return () => { live = false; };
  }, [templateId, target, requestKey]);
  const answered = settled?.key === requestKey ? settled : null;
  const previewing = templateId !== '' && !answered;
  const preview = answered?.result ?? null;

  const template = templates?.find((t) => t.id === templateId);

  const create = async () => {
    if (!template) return;
    setBusy(true);
    setError('');
    try {
      const built = await api.campaigns.build({ ...target, template_id: template.id, name: `${source.label}: ${template.name}` });
      setCreatedId(built.campaign_id);
      const released = await api.campaigns.release(built.campaign_id);
      setDone({ campaignId: built.campaign_id, queued: built.created, allowance: released.allowance });
    } catch (e) {
      setError(message(e, 'Could not queue this outreach'));
    } finally {
      setBusy(false);
    }
  };

  const club = templates?.filter((t) => t.scope === 'club') ?? [];
  const own = templates?.filter((t) => t.scope === 'mine') ?? [];
  const held = preview?.held ?? [];

  return (
    <div className="ui-dialog-root" role="presentation" data-testid="queue-outreach">
      <button className="ui-dialog-backdrop" aria-label="Close dialog" onClick={onClose} disabled={busy} />
      <section className="ui-dialog" style={{ width: 'min(44rem, 100%)', maxHeight: '90vh', overflowY: 'auto' }}
               role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId}>Queue outreach: {source.label}</h2>

        {done ? (
          <div className="ui-dialog__body space-y-3">
            <Notice tone="success">
              Queued {done.queued} {done.queued === 1 ? 'email' : 'emails'} from campaign #{done.campaignId}.
              {done.allowance && (
                <> They go out at up to {done.allowance.daily_limit} a day
                  {done.allowance.days_to_drain > 1 ? `, so this takes about ${done.allowance.days_to_drain} days` : ''}.</>
              )}
            </Notice>
            {done.allowance?.warning && <Notice tone="warning">{done.allowance.warning}</Notice>}
            <div className="ui-dialog__actions">
              <Link className="ui-button ui-button--secondary ui-button--md" to={`/campaigns/${done.campaignId}`}>Open the campaign</Link>
              <Button onClick={onClose}>Done</Button>
            </div>
          </div>
        ) : (
          <div className="ui-dialog__body space-y-4">
            <label className="block text-sm font-medium text-[var(--ink)]">
              Template
              <select
                ref={selectRef}
                className="ui-input mt-1 block w-full"
                value={templateId}
                disabled={busy || templates === null}
                onChange={(event) => { setError(''); setTemplateId(event.target.value ? Number(event.target.value) : ''); }}
              >
                <option value="">{templates === null ? 'Loading templates…' : 'Choose a template'}</option>
                {club.length > 0 && <optgroup label="Club templates">
                  {club.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </optgroup>}
                {own.length > 0 && <optgroup label="My templates">
                  {own.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </optgroup>}
              </select>
            </label>
            {templates?.length === 0 && !error && (
              <Notice tone="info">
                There are no templates yet. <Link className="underline" to="/outreach?view=resources">Write one</Link> and come back.
              </Notice>
            )}

            {previewing && <p className="text-sm text-slate-500" role="status">Checking who this reaches…</p>}
            {preview && (
              <div className="space-y-3" aria-label="Preview">
                <p className="text-sm text-[var(--ink)]" role="status">
                  <strong>{preview.ready ?? 0} ready</strong>
                  {held.length > 0 && <>, <strong>{held.length} held</strong></>}
                  {(preview.skipped ?? 0) > 0 && <>, {preview.skipped} left out (no email on file, or another member's private contact)</>}.
                </p>
                {preview.sample && (
                  <div className="rounded-lg border border-pale-sky p-3 text-sm">
                    <div className="text-xs uppercase tracking-wide text-slate-500">First email as {preview.sample.email || 'the first recipient'} will read it</div>
                    <div className="mt-1 font-medium text-[var(--ink)]">{preview.sample.subject}</div>
                    <div className="mt-1 whitespace-pre-wrap text-[var(--ink)]">{preview.sample.body}</div>
                  </div>
                )}
                {held.length > 0 && (
                  <div className="text-sm">
                    <div className="font-medium text-[var(--ink)]">Held, and why</div>
                    <ul className="mt-1 list-disc pl-5 text-slate-700">
                      {held.slice(0, HELD_SHOWN).map((h, index) => (
                        <li key={h.contact_id ?? index}>{h.name || h.email || 'Someone'}: {h.reason}</li>
                      ))}
                    </ul>
                    {held.length > HELD_SHOWN && <p className="mt-1 text-xs text-slate-500">and {held.length - HELD_SHOWN} more</p>}
                  </div>
                )}
              </div>
            )}

            {(error || answered?.error) && (
              <Notice tone="danger">
                {error || answered?.error}
                {createdId && <> Campaign #{createdId} was created but not released. <Link className="underline" to={`/campaigns/${createdId}`}>Open it</Link> to release it yourself.</>}
              </Notice>
            )}
            <div className="ui-dialog__actions">
              <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
              <Button onClick={() => void create()} disabled={busy || previewing || !preview || (preview.ready ?? 0) === 0}>
                {busy ? 'Queuing…' : preview?.ready ? `Create campaign and release ${preview.ready}` : 'Create campaign and release'}
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
