import { useEffect, useState } from 'react';
import { api, type TemplatePreview as Preview } from '../../api';
import { Notice } from '../ui/Primitives';

/** Wait for a pause in typing before asking the server to render. */
const TYPING_PAUSE_MS = 300;

/**
 * Beside the template editor: the merge fields on offer, and the draft as one
 * made-up recipient would read it. The server renders it, with the same code
 * that renders a real send, so a typo in a field name shows here as the error
 * the member would otherwise meet when building a campaign.
 */
export default function TemplatePreview({ subject, body }: { subject: string; body: string }) {
  const [result, setResult] = useState<Preview | null>(null);
  const [failed, setFailed] = useState('');

  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      api.outreach.templates.preview({ subject, body })
        .then((r) => { if (live) { setResult(r); setFailed(''); } })
        .catch((e) => { if (live) setFailed(e instanceof Error ? e.message : 'Preview unavailable'); });
    }, TYPING_PAUSE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [subject, body]);

  return (
    <div className="space-y-2" data-testid="template-preview">
      {result && (
        <p className="text-xs text-slate-600">
          Fields: {result.fields.map((f, index) => (
            <span key={f.name}>{index > 0 && ', '}<code title={f.help}>{`{${f.name}}`}</code> ({f.help})</span>
          ))}. A person missing a field your template uses is held back and named, never mailed with a gap.
        </p>
      )}
      {failed && <Notice tone="danger">{failed}</Notice>}
      {result?.error && <Notice tone="danger">{result.error}</Notice>}
      {result && !result.error && (subject.trim() || body.trim()) && (
        <div className="rounded-lg border border-pale-sky p-3 text-sm">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Preview for {result.recipient.name}, {result.recipient.title} at {result.recipient.company}
          </div>
          <div className="mt-1 font-medium text-[var(--ink)]">{result.subject || '(no subject)'}</div>
          <div className="mt-1 whitespace-pre-wrap text-[var(--ink)]">{result.body}</div>
        </div>
      )}
    </div>
  );
}
