import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BuildResult, type Contact, type SendAllowance, type Template } from '../../api';
import { beyondMostSenior, clubTemplate, companiesOf, liveWarnings, usesPersonalisation, warningText } from '../../lib/sendChecks';
import { useCompanyLines } from '../../lib/useCompanyLines';
import CompanyLinesField, { LinesPending } from '../CompanyLinesField';
import { Button, ConfirmDialog, Notice } from '../ui/Primitives';

/**
 * From people to a released campaign without leaving the page: the message
 * (the club's Intro, which every member sends), one line for each company the
 * member confirms, and the one Send button, which asks once.
 *
 * Nothing here decides who is sent to. The caller counts that - the ticked
 * people who have an address and are not held - and this panel only asks the
 * server what it would hold (the same /build call that makes the campaign, in
 * preview) and reports it back, so the number on the button is the number in
 * the header. Every send-time rule (window, per-company cap, replies and
 * meetings, the probe, teammates, the daily cap) is the server's and runs when
 * the campaign is released. The confirm also passes on the server's warnings
 * about writing to many people at one company; they never block.
 */

/** Held people named in the confirm before "and N more"; the count is the whole. */
const HELD_SHOWN = 6;
const TYPING_PAUSE_MS = 250;

export type Held = { name: string; reason: string };

type Sent = { campaignId: number; queued: number; allowance?: SendAllowance };

const message = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

export default function SendPanel({ ticked, recipients, held, heldPeople, leftOut, searching, companyLabel, draftsHref, onHeld, onUntick }: {
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
  /** Untick these people (a warning's "Keep the most senior 2"). */
  onUntick: (ids: number[]) => void;
}) {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templateId, setTemplateId] = useState<number | ''>('');
  const [error, setError] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [sent, setSent] = useState<Sent | null>(null);

  // Choosing a template puts its text on screen to read or edit for this
  // send; choosing nothing clears it.
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
        choose(clubTemplate(list));
      })
      .catch((e) => { if (live) { setTemplates([]); setError(message(e, 'Could not load templates')); } });
    return () => { live = false; };
  }, []);

  // --- One line for each company (useCompanyLines) -------------------------
  const companies = useMemo(() => companiesOf(recipients), [recipients]);
  const companyLines = useCompanyLines(companies, usesPersonalisation(body) || usesPersonalisation(subject));
  const { personalisation } = companyLines;

  const tickedKey = ticked.map((p) => p.id).join(',');

  // --- What the server would render, and hold ------------------------------
  // An edited template is no longer the saved one: the text on screen is what
  // the server renders and what is sent.
  const template = templates?.find((t) => t.id === templateId);
  const hasText = Boolean(subject.trim() && body.trim());
  const usesTemplate = Boolean(template) && subject === template?.subject && body === template?.body;
  const linesKey = personalisation ? JSON.stringify(personalisation) : '';
  const previewKey = `${usesTemplate ? templateId : `${subject}\n${body}`}|${tickedKey}|${linesKey}`;
  const [settled, setSettled] = useState<{ key: string; result?: BuildResult; error?: string } | null>(null);
  const wantsPreview = (usesTemplate || hasText) && tickedKey !== '';
  useEffect(() => {
    if (!wantsPreview) return;
    let live = true;
    const timer = window.setTimeout(() => {
      const ids = tickedKey.split(',').map(Number);
      const lineMap = linesKey ? JSON.parse(linesKey) as Record<string, string> : undefined;
      api.campaigns.build(usesTemplate
        ? { contact_ids: ids, template_id: Number(templateId), preview_only: true, personalisation: lineMap }
        : { contact_ids: ids, subject: subject.trim(), body: body.trim(), preview_only: true, personalisation: lineMap })
        .then((result) => { if (live) setSettled({ key: previewKey, result }); })
        .catch((e) => { if (live) setSettled({ key: previewKey, error: message(e, 'Could not preview this message') }); });
    }, TYPING_PAUSE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [wantsPreview, tickedKey, templateId, previewKey, usesTemplate, subject, body, linesKey]);
  const answered = settled?.key === previewKey ? settled : null;
  const previewing = wantsPreview && !answered;
  // The last answer stands while a new one is asked for, so the count does
  // not jump up and back down at every tick.
  const preview = wantsPreview ? answered?.result ?? settled?.result ?? null : null;

  // --- What the page is told is held --------------------------------------
  const [queued, setQueued] = useState<Map<number, string>>(new Map());
  const holds = useMemo(() => {
    const map = new Map<number, string>();
    for (const h of preview?.held ?? []) if (h.contact_id !== undefined) map.set(h.contact_id, h.reason);
    for (const [id, why] of queued) map.set(id, why);
    return map;
  }, [preview, queued]);
  useEffect(() => { onHeld(holds); }, [holds, onHeld]);

  const count = recipients.length;
  const recipientIds = useMemo(() => new Set(recipients.map((p) => p.id)), [recipients]);
  const warnings = useMemo(() => liveWarnings(preview?.warnings ?? [], recipientIds), [preview, recipientIds]);
  // What is sent: the chosen template as it is, or - once the member has
  // edited it - the text on screen.
  const outgoing = usesTemplate && template ? { template_id: template.id } : hasText ? { subject: subject.trim(), body: body.trim() } : null;
  const ready = count > 0 && !busy && Boolean(outgoing) && !previewing && !answered?.error
    && companyLines.pending === 0 && !companyLines.suggesting;

  const send = async () => {
    if (!outgoing) return;
    setBusy(true);
    setError('');
    setCreatedId(null);
    try {
      const ids = recipients.map((p) => p.id);
      const name = `${companyLabel}: ${usesTemplate && template ? template.name : 'Outreach'}`;
      const built = await api.campaigns.build({ contact_ids: ids, ...outgoing, name, personalisation });
      setCreatedId(built.campaign_id);
      const released = await api.campaigns.release(built.campaign_id);
      setConfirming(false);
      setSent({ campaignId: built.campaign_id, queued: built.created, allowance: released.allowance });
      setQueued((current) => new Map([...current, ...ids.map((id) => [id, `queued in campaign #${built.campaign_id}`] as const)]));
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

        {/* Every member sends the club's template; a choice appears only for
            someone who still has templates of their own. */}
        {templates && templates.length > 1 && (
          <label className="block text-xs font-medium text-slate-600">
            Template
            <select
              className="ui-input mt-1 block w-full"
              value={templateId}
              disabled={busy}
              onChange={(e) => { setError(''); choose(templates.find((t) => t.id === Number(e.target.value))); }}
            >
              <option value="">Choose a template</option>
              {club.length > 0 && <optgroup label="Club templates">
                {club.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </optgroup>}
              {own.length > 0 && <optgroup label="My templates">
                {own.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </optgroup>}
            </select>
          </label>
        )}
        <label className="block text-xs font-medium text-slate-600">
          Subject
          <input className="ui-input mt-1 block w-full" value={subject} disabled={busy || templates === null}
                 onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label className="block text-xs font-medium text-slate-600">
          Message
          <textarea className="ui-input mt-1 block w-full" rows={6} value={body} disabled={busy || templates === null}
                    onChange={(e) => setBody(e.target.value)} />
        </label>
        {templates?.length === 0 && (
          <Notice tone="info">
            There is no club template yet. <Link className="underline" to="/outreach?view=resources">Write one</Link>.
          </Notice>
        )}

        <CompanyLinesField state={companyLines} disabled={busy} />

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
        {count > 0 && <LinesPending state={companyLines} />}
        {draftsHref && (
          <p className="text-xs text-slate-500">
            Want to read or edit each email first?{' '}
            <Link className="underline" to={draftsHref}>Open these {count} in Drafts</Link>.
          </p>
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
            {warnings.length > 0 && (
              <div className="space-y-2" data-testid="confirm-warnings">
                {warnings.map((w) => (
                  <Notice key={`${w.kind}:${w.company}:${w.team ?? ''}`} tone="warning">
                    <span className="block">{warningText(w)}</span>
                    {w.kind === 'team' && (
                      <Button size="sm" variant="secondary" className="mt-2" disabled={busy}
                              onClick={() => onUntick(beyondMostSenior(w))}>
                        Keep the most senior {w.keep ?? 2}
                      </Button>
                    )}
                  </Notice>
                ))}
              </div>
            )}
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
