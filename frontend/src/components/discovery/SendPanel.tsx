import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, api, type BuildResult, type Contact, type SendAllowance, type Template } from '../../api';
import {
  beyondMostSenior, companiesOf, liveWarnings, personalisationFor, unconfirmed, usesPersonalisation, warningText,
  type CompanyLine,
} from '../../lib/sendChecks';
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
/** Suggestions asked for at once: the server runs two model calls for the whole club. */
const SUGGEST_WORKERS = 2;

export type Held = { name: string; reason: string };

type Sent = { campaignId: number; queued: number; allowance?: SendAllowance };

const message = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);
/** The model is off, or the month's budget is spent: every later suggestion would fail the same way. */
const stopsEverySuggestion = (e: unknown) => e instanceof ApiError && (e.status === 503 || (e.status === 429 && /budget/i.test(e.message)));

/** The club's Intro template if there is one, else the first club template. */
function defaultTemplate(list: Template[]): Template | undefined {
  const club = list.filter((t) => t.scope === 'club');
  return club.find((t) => t.name.trim().toLowerCase() === 'intro') ?? club[0] ?? list[0];
}

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
        choose(defaultTemplate(list));
      })
      .catch((e) => { if (live) { setTemplates([]); setError(message(e, 'Could not load templates')); } });
    return () => { live = false; };
  }, []);

  // --- One line for each company -----------------------------------------
  // The Intro leaves room for one sentence about each company. The member
  // asks for a suggestion (one model call per company, from that company's
  // material only) or writes it, and confirms it; an empty confirmed line
  // sends without one. Writing it yourself makes no model call.
  const [lines, setLines] = useState<Record<string, CompanyLine>>({});
  const companies = useMemo(() => companiesOf(recipients), [recipients]);
  const needsLines = usesPersonalisation(body) || usesPersonalisation(subject);
  const pendingLines = needsLines ? unconfirmed(companies, lines) : 0;
  const suggestingAny = companies.some((c) => lines[c.key]?.suggesting);
  const personalisation = useMemo(
    () => (needsLines ? personalisationFor(companies, lines) : undefined),
    [needsLines, companies, lines],
  );
  const setLine = (key: string, change: Partial<CompanyLine>) =>
    setLines((all) => ({ ...all, [key]: { ...(all[key] ?? { text: '', confirmed: false }), ...change } }));

  const suggest = async (company: { key: string; name: string }): Promise<boolean> => {
    const previous = (lines[company.key]?.text || '').trim();
    setLine(company.key, { suggesting: true, error: undefined, confirmed: false });
    try {
      const answer = await api.campaigns.personalisation(company.name, previous);
      setLine(company.key, { text: answer.line, sources: answer.sources, suggesting: false });
      return true;
    } catch (e) {
      setLine(company.key, { suggesting: false, error: message(e, 'Could not suggest a line') });
      return !stopsEverySuggestion(e);
    }
  };
  const suggestAll = async () => {
    const queue = companies.filter((c) => !lines[c.key]?.confirmed && !(lines[c.key]?.text || '').trim());
    let carryOn = true;
    const work = async () => {
      for (let next = queue.shift(); next && carryOn; next = queue.shift()) carryOn = await suggest(next);
    };
    await Promise.all(Array.from({ length: SUGGEST_WORKERS }, work));
  };
  const unwritten = companies.filter((c) => !lines[c.key]?.confirmed && !(lines[c.key]?.text || '').trim()).length;

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
  const ready = count > 0 && !busy && Boolean(outgoing) && !previewing && !answered?.error && pendingLines === 0 && !suggestingAny;

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

        {needsLines && companies.length > 0 && (
          <fieldset className="space-y-2 border border-pale-sky p-3" data-testid="company-lines">
            <legend className="px-1 text-xs font-semibold text-deep-navy">A line for each company</legend>
            <p className="text-xs text-slate-600">
              Follows “I'd love to work with them on a project in the new year.” Suggest one or write your own,
              then confirm it. Confirm an empty line to send without one.
            </p>
            {unwritten > 0 && (
              <Button size="sm" variant="secondary" disabled={busy || suggestingAny} onClick={() => void suggestAll()}
                      data-testid="suggest-lines">
                {unwritten === 1 ? 'Suggest a line' : `Suggest lines for ${unwritten} companies`}
              </Button>
            )}
            <ul className="space-y-3">
              {companies.map((c) => {
                const line = lines[c.key] ?? { text: '', confirmed: false };
                return (
                  <li key={c.key} data-testid="company-line" data-company={c.name} className="space-y-1">
                    <label className="block text-xs font-medium text-slate-600">
                      {c.name}
                      <textarea className="ui-input mt-1 block w-full" rows={2} value={line.text} aria-label={`Line for ${c.name}`}
                                disabled={busy || line.suggesting}
                                placeholder={line.suggesting ? 'Suggesting…' : 'One sentence about this company'}
                                onChange={(e) => setLine(c.key, { text: e.target.value, confirmed: false, sources: undefined })} />
                    </label>
                    <div className="flex flex-wrap items-center gap-3">
                      <Button size="sm" variant="ghost" disabled={busy || line.suggesting} onClick={() => void suggest(c)}>
                        {line.suggesting ? 'Suggesting…' : line.text.trim() ? 'Regenerate' : 'Suggest'}
                      </Button>
                      <label className="flex items-center gap-1.5 text-xs text-slate-700">
                        <input type="checkbox" checked={line.confirmed} disabled={busy || line.suggesting}
                               aria-label={`Confirm the line for ${c.name}`}
                               onChange={(e) => setLine(c.key, { confirmed: e.target.checked })} />
                        {line.text.trim() ? 'Confirmed' : 'Confirmed: no line'}
                      </label>
                    </div>
                    {line.sources && line.sources.length > 0 && (
                      <p className="text-xs text-slate-500">From {line.sources.join(', ')}.</p>
                    )}
                    {line.error && <p className="text-xs text-red-700" role="alert">{line.error}</p>}
                  </li>
                );
              })}
            </ul>
          </fieldset>
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
        {count > 0 && pendingLines > 0 && (
          <p className="text-xs text-slate-500" data-testid="lines-pending">
            Confirm each company's line to send: {companies.length - pendingLines} of {companies.length} confirmed.
          </p>
        )}
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
