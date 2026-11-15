import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BuildResult, type Contact, type SendAllowance, type Template } from '../../api';
import { AI_DRAFTING_UNAVAILABLE } from '../../lib/aiDrafting';
import { BUDGET_NOTE, DRAFT_WORKERS, draftEach, type DraftStatus } from '../../lib/aiDrafts';
import { looksLikePerson } from '../../lib/recipients';
import { Button, ConfirmDialog, Notice } from '../ui/Primitives';

/**
 * From people to a released campaign without leaving the page: the message
 * (the club's Intro template by default, or an email the AI writes for each
 * person) and the one Send button, which asks once.
 *
 * Nothing here decides who is sent to. The caller counts that - the ticked
 * people who have an address and are not held - and this panel only asks the
 * server what it would hold (the same /build call that makes the campaign, in
 * preview) and reports it back, so the number on the button is the number in
 * the header. Every send-time rule (window, per-company cap, replies and
 * meetings, the probe, teammates, the daily cap) is the server's and runs when
 * the campaign is released.
 */

/** Held people named in the confirm before "and N more"; the count is the whole. */
const HELD_SHOWN = 6;
const TYPING_PAUSE_MS = 250;

export type Held = { name: string; reason: string };

type Sent = { campaignId: number; queued: number; allowance?: SendAllowance };

const message = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

/** The club's Intro template if there is one, else the first club template. */
function defaultTemplate(list: Template[]): Template | undefined {
  const club = list.filter((t) => t.scope === 'club');
  return club.find((t) => t.name.trim().toLowerCase() === 'intro') ?? club[0] ?? list[0];
}

export default function SendPanel({ ticked, recipients, held, heldPeople, leftOut, searching, companyLabel, draftsHref, onHeld }: {
  /** Everyone ticked who has an address: who the server is asked about. */
  ticked: Contact[];
  /** The people a send writes to: ticked, reachable and not held. */
  recipients: Contact[];
  /** Who is held, and why, across the page: the confirm names them. */
  held: Held[];
  heldPeople: number;
  /** What the number leaves out ("2 no address · 1 already written to"). */
  leftOut: string;
  /** Companies whose search has not finished: they are not part of this send. */
  searching: number;
  /** Used in the campaign's name. */
  companyLabel: string;
  /** Where the same people open in Drafts, to read and edit each email; '' with nobody to send to. */
  draftsHref: string;
  /** Tell the page which ticked people cannot be written to, and why. */
  onHeld: (holds: Map<number, string>) => void;
}) {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templateId, setTemplateId] = useState<number | ''>('');
  const [mode, setMode] = useState<'template' | 'ai'>('template');
  const [error, setError] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [sent, setSent] = useState<Sent | null>(null);

  // Choosing a template puts its text on screen to read, edit or save under a
  // new name; choosing nothing clears it.
  const choose = (t: Template | undefined) => {
    setTemplateId(t?.id ?? '');
    setSubject(t?.subject ?? '');
    setBody(t?.body ?? '');
  };
  useEffect(() => {
    let live = true;
    api.outreach.templates.list()
      .then((response) => {
        if (!live) return;
        const list = Array.isArray(response) ? response : [];
        setTemplates(list);
        choose(defaultTemplate(list));
      })
      .catch((e) => { if (live) { setTemplates([]); setError(message(e, 'Could not load templates')); } });
    return () => { live = false; };
  }, []);

  const [naming, setNaming] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  // Saved through the same endpoint as the Outreach page, so it is the
  // member's own template; the server says why if it refuses the name or the
  // text. The saved template is chosen straight away.
  const saveTemplate = async () => {
    setSaving(true);
    setSaveError('');
    try {
      const created = await api.outreach.templates.create({ name: templateName.trim(), subject: subject.trim(), body: body.trim() });
      const list = await api.outreach.templates.list();
      setTemplates(list);
      choose(list.find((t) => t.id === created.id));
      setNaming(false);
      setTemplateName('');
    } catch (e) {
      setSaveError(message(e, 'Could not save the template'));
    } finally {
      setSaving(false);
    }
  };

  const tickedKey = ticked.map((p) => p.id).join(',');

  // --- Template: what the server would render, and hold -------------------
  // An edited template is no longer the saved one: the text on screen is what
  // the server renders and what is sent, until it is saved as a template.
  const template = templates?.find((t) => t.id === templateId);
  const hasText = Boolean(subject.trim() && body.trim());
  const usesTemplate = Boolean(template) && subject === template?.subject && body === template?.body;
  const previewKey = `${usesTemplate ? templateId : `${subject}\n${body}`}|${tickedKey}`;
  const [settled, setSettled] = useState<{ key: string; result?: BuildResult; error?: string } | null>(null);
  const wantsPreview = mode === 'template' && (usesTemplate || hasText) && tickedKey !== '';
  useEffect(() => {
    if (!wantsPreview) return;
    let live = true;
    const timer = window.setTimeout(() => {
      const ids = tickedKey.split(',').map(Number);
      api.campaigns.build(usesTemplate
        ? { contact_ids: ids, template_id: Number(templateId), preview_only: true }
        : { contact_ids: ids, subject: subject.trim(), body: body.trim(), preview_only: true })
        .then((result) => { if (live) setSettled({ key: previewKey, result }); })
        .catch((e) => { if (live) setSettled({ key: previewKey, error: message(e, 'Could not preview this template') }); });
    }, TYPING_PAUSE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [wantsPreview, tickedKey, templateId, previewKey, usesTemplate, subject, body]);
  const answered = settled?.key === previewKey ? settled : null;
  const previewing = wantsPreview && !answered;
  // The last answer stands while a new one is asked for, so the count does
  // not jump up and back down at every tick.
  const preview = wantsPreview ? answered?.result ?? settled?.result ?? null : null;

  // --- AI: an email each, several at a time -------------------------------
  const [status, setStatus] = useState<Record<number, DraftStatus>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  const [firstDraft, setFirstDraft] = useState<{ id: number; subject: string; body: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [aiDown, setAiDown] = useState(false);
  const [aiNote, setAiNote] = useState('');
  const stopRef = useRef(false);
  // A row whose name is page text would get an email opening "Dear Transformation,".
  const writable = ticked.filter(looksLikePerson);
  const todo = writable.filter((p) => status[p.id] !== 'done');
  const draftedCount = writable.length - todo.length;
  const failedCount = writable.filter((p) => status[p.id] === 'failed').length;

  const draftAll = async () => {
    stopRef.current = false;
    setRunning(true);
    setError('');
    setAiNote('');
    setAiDown(false);
    await draftEach({
      people: todo,
      tone: 'professional',
      stop: stopRef,
      onEvent: (event) => {
        setStatus((s) => ({ ...s, [event.id]: event.status }));
        if (event.status === 'failed') setReason((r) => ({ ...r, [event.id]: event.reason }));
        if (event.status === 'done') setFirstDraft((d) => d ?? { id: event.id, ...event.email });
      },
      onUnavailable: () => setAiDown(true),
      onBudgetSpent: () => setAiNote(BUDGET_NOTE),
    });
    setRunning(false);
  };

  // --- What the page is told is held --------------------------------------
  const [queued, setQueued] = useState<Map<number, string>>(new Map());
  const holds = useMemo(() => {
    const map = new Map<number, string>();
    if (mode === 'template') {
      for (const h of preview?.held ?? []) if (h.contact_id !== undefined) map.set(h.contact_id, h.reason);
    } else {
      for (const p of ticked) {
        // Page text for a name is listed, never drafted.
        if (!looksLikePerson(p)) map.set(p.id, 'the name looks like page text, not a person');
        else if (status[p.id] === 'failed') map.set(p.id, `the draft failed: ${reason[p.id] || 'try again'}`);
      }
    }
    for (const [id, why] of queued) map.set(id, why);
    return map;
  }, [mode, preview, status, reason, ticked, queued]);
  useEffect(() => { onHeld(holds); }, [holds, onHeld]);

  // The AI path cannot send until every ticked person has a draft or a reason.
  const undrafted = mode === 'ai' ? writable.filter((p) => status[p.id] !== 'done' && status[p.id] !== 'failed').length : 0;
  const sampleDraft = mode === 'ai' && firstDraft ? ticked.find((p) => p.id === firstDraft.id) : undefined;
  const count = recipients.length;
  // What is sent: the chosen template as it is, or - once the member has
  // edited it or written their own - the text on screen.
  const outgoing = usesTemplate && template ? { template_id: template.id } : hasText ? { subject: subject.trim(), body: body.trim() } : null;
  const ready = count > 0 && !busy && !running && (mode === 'template'
    ? Boolean(outgoing) && !previewing && !answered?.error
    : undrafted === 0);

  const send = async () => {
    setBusy(true);
    setError('');
    setCreatedId(null);
    try {
      const ids = recipients.map((p) => p.id);
      const name = `${companyLabel}: ${mode === 'ai' ? 'advisory emails' : usesTemplate && template ? template.name : 'Outreach'}`;
      let campaignId: number;
      let created: number;
      if (mode === 'template' && outgoing) {
        const built = await api.campaigns.build({ contact_ids: ids, ...outgoing, name });
        campaignId = built.campaign_id;
        created = built.created;
      } else {
        // No subjects or bodies: each person's latest saved draft is used, the
        // way Drafts builds a campaign, so what was written is what is sent.
        const camp = await api.campaigns.create(name);
        campaignId = camp.id;
        await api.campaigns.addContacts(camp.id, { contact_ids: ids });
        created = ids.length;
      }
      setCreatedId(campaignId);
      const released = await api.campaigns.release(campaignId);
      setConfirming(false);
      setSent({ campaignId, queued: created, allowance: released.allowance });
      setQueued((current) => new Map([...current, ...ids.map((id) => [id, `queued in campaign #${campaignId}`] as const)]));
    } catch (e) {
      setConfirming(false);
      setError(message(e, 'Could not send this'));
    } finally {
      setBusy(false);
    }
  };

  const club = templates?.filter((t) => t.scope === 'club') ?? [];
  const own = templates?.filter((t) => t.scope === 'mine') ?? [];

  return (
    <>
      <div className="space-y-3 border-b border-pale-sky px-5 pb-5 pt-4" data-testid="message-panel">
        <h3 className="text-sm font-semibold text-deep-navy">Message</h3>

        {mode === 'template' ? (
          <>
            <div className="flex items-end gap-2">
              <label className="block min-w-0 flex-1 text-xs font-medium text-slate-600">
                Template
                <select
                  className="ui-input mt-1 block w-full"
                  value={templateId}
                  disabled={busy || templates === null}
                  onChange={(e) => { setError(''); choose(templates?.find((t) => t.id === Number(e.target.value))); }}
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
              <Button variant="secondary" size="sm" className="shrink-0"
                      disabled={busy || saving || !hasText} onClick={() => { setSaveError(''); setNaming((v) => !v); }}
                      data-testid="save-template-open">
                Save as template
              </Button>
            </div>
            {naming && (
              <form className="flex flex-wrap items-end gap-2 border border-pale-sky p-2" data-testid="save-template-form"
                    onSubmit={(e) => { e.preventDefault(); void saveTemplate(); }}>
                <label className="min-w-0 flex-1 text-xs font-medium text-slate-600">
                  Name for this template
                  <input className="ui-input mt-1 block w-full" value={templateName} autoFocus
                         onChange={(e) => setTemplateName(e.target.value)} />
                </label>
                <Button type="submit" size="sm" disabled={saving || !templateName.trim() || !hasText}>
                  {saving ? 'Saving…' : 'Save'}
                </Button>
                <Button variant="ghost" size="sm" disabled={saving} onClick={() => { setNaming(false); setSaveError(''); }}>
                  Cancel
                </Button>
              </form>
            )}
            {saveError && <Notice tone="danger">{saveError}</Notice>}
            <label className="block text-xs font-medium text-slate-600">
              Subject
              <input className="ui-input mt-1 block w-full" value={subject} disabled={busy}
                     onChange={(e) => setSubject(e.target.value)} />
            </label>
            <label className="block text-xs font-medium text-slate-600">
              Message
              <textarea className="ui-input mt-1 block w-full" rows={6} value={body} disabled={busy}
                        onChange={(e) => setBody(e.target.value)} />
            </label>
            {templates?.length === 0 && (
              <Notice tone="info">
                There are no templates yet. <Link className="underline" to="/outreach?view=resources">Write one</Link>, or have the AI write each email.
              </Notice>
            )}
            {previewing && <p className="text-xs text-slate-500" role="status">Checking who this reaches…</p>}
            {preview?.sample && (
              <div className="border border-pale-sky p-3 text-sm" data-testid="message-preview">
                <div className="text-xs uppercase tracking-wide text-slate-500">
                  First email as {preview.sample.email || 'the first recipient'} will read it
                </div>
                <div className="mt-1 font-medium text-[var(--ink)]">{preview.sample.subject}</div>
                <div className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-[var(--ink)]">{preview.sample.body}</div>
              </div>
            )}
            {answered?.error && <Notice tone="danger">{answered.error}</Notice>}
            <Button variant="secondary" size="sm" onClick={() => { setError(''); setMode('ai'); }}>
              Write with AI instead
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-slate-700">
              The AI writes an advisory email to each person: two or three projects a YUCG team could build
              for their company, chosen for their role. {DRAFT_WORKERS} are written at a time.
            </p>
            {writable.length === 0 && <p className="text-xs text-slate-500">Tick people and the emails are written for them.</p>}
            {writable.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                {todo.length > 0 && (
                  <Button size="sm" disabled={running} onClick={() => void draftAll()} data-testid="draft-all">
                    {running
                      ? `Writing… ${draftedCount} of ${writable.length}`
                      : draftedCount > 0 ? `Write the other ${todo.length}` : `Write ${writable.length} emails with AI`}
                  </Button>
                )}
                {running && (
                  <Button size="sm" variant="ghost" onClick={() => { stopRef.current = true; }}>Stop</Button>
                )}
                <span className="text-xs text-slate-600" role="status" data-testid="draft-progress">
                  {draftedCount} written{failedCount ? ` · ${failedCount} failed` : ''}
                </span>
              </div>
            )}
            {sampleDraft && firstDraft && (
              <div className="border border-pale-sky p-3 text-sm" data-testid="message-preview">
                <div className="text-xs uppercase tracking-wide text-slate-500">
                  First email, to {sampleDraft.email}
                </div>
                <div className="mt-1 font-medium text-[var(--ink)]">{firstDraft.subject}</div>
                <div className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-[var(--ink)]">{firstDraft.body}</div>
              </div>
            )}
            {aiDown && (
              <Notice tone="warning">
                {AI_DRAFTING_UNAVAILABLE}{' '}
                <Button size="sm" variant="secondary" onClick={() => { setAiDown(false); setMode('template'); }}>
                  Use a template
                </Button>
              </Notice>
            )}
            {aiNote && <Notice tone="warning">{aiNote}</Notice>}
            <Button variant="secondary" size="sm" disabled={running} onClick={() => setMode('template')}>
              Use a template instead
            </Button>
          </>
        )}
      </div>

      <div className="space-y-3 border-b border-pale-sky px-5 pb-5 pt-4" data-testid="send-panel">
        <h3 className="text-sm font-semibold text-deep-navy">Send</h3>
        {searching > 0 && (
          <p className="text-xs text-slate-600" role="status">
            {searching} {searching === 1 ? 'company is' : 'companies are'} still being searched; people found after you
            send are not part of it.
          </p>
        )}
        <Button className="w-full" disabled={!ready} onClick={() => setConfirming(true)} data-testid="send-button">
          {count > 0 ? `Send to ${count}` : 'Send'}
        </Button>
        {count === 0 && <p className="text-xs text-slate-500">Nobody can be emailed yet: tick people on the right.</p>}
        {draftsHref && (
          <p className="text-xs text-slate-500">
            Want to read or edit each email first?{' '}
            <Link className="underline" to={draftsHref}>Open these {count} in Drafts</Link>.
          </p>
        )}
        {mode === 'ai' && undrafted > 0 && count > 0 && !running && (
          <p className="text-xs text-slate-500">Write the emails first; Send opens when each person has one.</p>
        )}
        {error && (
          <Notice tone="danger">
            {error}
            {createdId && <> Campaign #{createdId} was created but not released. <Link className="underline" to={`/campaigns/${createdId}`}>Open it</Link> to release it yourself.</>}
          </Notice>
        )}
        {sent && (
          <Notice tone="success">
            <span data-testid="sent-summary">
              Queued {sent.queued} {sent.queued === 1 ? 'email' : 'emails'} from your Gmail.
              {sent.allowance && (
                <> They go out at up to {sent.allowance.daily_limit} a day
                  {sent.allowance.days_to_drain > 1 ? `, so this takes about ${sent.allowance.days_to_drain} days` : ''}.</>
              )}
              {' '}<Link className="font-semibold underline" to={`/campaigns/${sent.campaignId}`}>Open campaign #{sent.campaignId}</Link>
              {' '}to follow it or pause it.
            </span>
            {sent.allowance?.warning && <span className="mt-1 block">{sent.allowance.warning}</span>}
          </Notice>
        )}
      </div>

      <ConfirmDialog
        open={confirming}
        title={`Send ${count} ${count === 1 ? 'email' : 'emails'} from your Gmail?`}
        confirmLabel={`Send ${count}`}
        busy={busy}
        onConfirm={() => void send()}
        onClose={() => setConfirming(false)}
        body={(
          <div className="space-y-3 text-sm" data-testid="confirm-send">
            <p>
              They go from your own Gmail, paced by your daily limit. Nobody is written to twice, and a company that
              replies is left alone; you can pause the campaign at any time.
            </p>
            {heldPeople > 0 && (
              <div data-testid="confirm-held">
                <p className="font-medium text-[var(--ink)]">{heldPeople} held:</p>
                <ul className="mt-1 list-disc pl-5 text-slate-700">
                  {held.slice(0, HELD_SHOWN).map((h, index) => <li key={index}>{h.name}: {h.reason}</li>)}
                </ul>
                {heldPeople > HELD_SHOWN && <p className="mt-1 text-xs text-slate-500">and {heldPeople - HELD_SHOWN} more</p>}
              </div>
            )}
            {leftOut && <p className="text-xs text-slate-600" data-testid="confirm-left-out">Not included: {leftOut}.</p>}
            {searching > 0 && (
              <p className="text-xs text-slate-600">
                {searching} {searching === 1 ? 'company is' : 'companies are'} still being searched; whoever they find
                later is not in this send.
              </p>
            )}
          </div>
        )}
      />
    </>
  );
}
