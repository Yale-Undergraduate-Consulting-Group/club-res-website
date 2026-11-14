import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { api, type SurveyLibrary } from '../api';
import PageHeader from '../components/PageHeader';
import { Button, ConfirmDialog, EmptyState, Notice } from '../components/ui/Primitives';
import { parseSurveyText, questionsMissingChoices, type SurveyDraft } from '../lib/surveyImport';

type ChatTurn = { role: 'user' | 'assistant'; content: string };
type LogEntry = { kind: 'user' | 'assistant' | 'note' | 'error'; text: string };

const TYPE_LABEL: Record<string, string> = {
  single_choice: 'Single choice',
  multi_choice: 'Multiple choice',
  likert: 'Agree scale (5-point)',
  slider: 'Slider',
  text: 'Open text',
};
const LIKERT = ['Strongly disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly agree'];
const GREETING =
  "Paste your project background and the hypotheses this survey should test. I'll ask only about what's missing, then draft the survey on the right. When it's ready, choose Create in Qualtrics.";
const IMPORT_EXAMPLE = 'Survey: My survey\n1. How often do you visit? [single]\n- Daily\n- Weekly\n2. Rate the coffee [slider 0-10]\n3. Any other feedback? [text]';

/** Survey builder: drafts a client survey with the club's model and builds it in Qualtrics. */
export default function Surveys() {
  const [library, setLibrary] = useState<SurveyLibrary | null>(null);
  const [libraryError, setLibraryError] = useState('');
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [log, setLog] = useState<LogEntry[]>([{ kind: 'assistant', text: GREETING }]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState<SurveyDraft | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ url: string; added: number; total: number } | null>(null);
  const [createError, setCreateError] = useState('');
  const logEnd = useRef<HTMLDivElement>(null);

  const loadLibrary = useCallback(async () => {
    try {
      const status = await api.surveys.library();
      setLibrary(status);
      setLibraryError('');
      return status;
    } catch (e) {
      setLibraryError((e as Error).message);
      return null;
    }
  }, []);

  useEffect(() => {
    void loadLibrary();
  }, [loadLibrary]);

  // While the server copies past surveys, check back until it finishes.
  useEffect(() => {
    if (!library?.syncing) return;
    const timer = window.setTimeout(() => void loadLibrary(), 3000);
    return () => window.clearTimeout(timer);
  }, [library, loadLibrary]);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: 'nearest' });
  }, [log]);

  const refreshLibrary = async () => {
    setLibraryError('');
    try {
      await api.surveys.syncLibrary();
    } catch (e) {
      setLibraryError((e as Error).message);
    }
    await loadLibrary();
  };

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    const next: ChatTurn[] = [...turns, { role: 'user', content: text }];
    setInput('');
    setSending(true);
    setLog((rows) => [...rows, { kind: 'user', text }]);
    try {
      const reply = await api.surveys.chat(next);
      // The full reply stays in the history so the model keeps the current draft.
      setTurns([...next, { role: 'assistant', content: JSON.stringify({ message: reply.message, survey: reply.survey }) }]);
      setLog((rows) => [
        ...rows,
        { kind: 'assistant', text: reply.message || 'Here is the draft.' },
        ...(reply.sources.length ? [{ kind: 'note' as const, text: `Drew on past surveys: ${reply.sources.join(', ')}` }] : []),
      ]);
      if (reply.survey) {
        setDraft(reply.survey);
        setCreated(null);
        setCreateError('');
      }
    } catch (e) {
      // The analyst's message was not answered; keep it out of the history so a retry stays valid.
      setInput(text);
      setLog((rows) => [...rows, { kind: 'error', text: (e as Error).message }]);
    } finally {
      setSending(false);
    }
  };

  const onInputKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  const importDraft = () => {
    const parsed = parseSurveyText(importText);
    if (!parsed.questions.length) {
      setLog((rows) => [...rows, { kind: 'error', text: 'No questions found. Number each question, like "1. Your question [single]".' }]);
      return;
    }
    const missing = questionsMissingChoices(parsed).length;
    setDraft(parsed);
    setCreated(null);
    setCreateError('');
    setImportOpen(false);
    // Let the model know about the imported draft so later edits build on it.
    // History always ends with the model's turn: an unanswered message is never kept.
    setTurns((rows) => [
      ...rows,
      { role: 'user', content: `I imported this survey draft:\n${JSON.stringify(parsed)}` },
      { role: 'assistant', content: JSON.stringify({ message: 'Got it, this is the current draft.', survey: parsed }) },
    ]);
    setLog((rows) => [
      ...rows,
      {
        kind: 'note',
        text: `Imported ${parsed.questions.length} questions into the draft.${missing ? ` ${missing} choice question(s) have no answer choices; add lines starting with "-".` : ''}`,
      },
    ]);
  };

  const createInQualtrics = async () => {
    if (!draft) return;
    setCreating(true);
    setCreateError('');
    try {
      const result = await api.surveys.create(draft);
      setCreated({ url: result.edit_url, added: result.added, total: draft.questions.length });
      if (result.failed.length) {
        setLog((rows) => [
          ...rows,
          { kind: 'error', text: `Some questions were not added:\n${result.failed.map((f) => `• ${f.question}: ${f.error}`).join('\n')}` },
        ]);
      }
    } catch (e) {
      setCreateError((e as Error).message);
    } finally {
      setCreating(false);
      setConfirming(false);
    }
  };

  const qualtricsReady = library?.qualtrics === true;
  const libraryLine = !library
    ? 'Loading past-survey library…'
    : library.syncing
      ? 'Copying past surveys from Qualtrics…'
      : library.surveys
        ? `Learning from ${library.surveys} past surveys (${library.questions} questions)`
        : 'No past surveys in the library yet';

  return (
    <div className="app-workspace space-y-4">
      <PageHeader
        title="Survey builder"
        subtitle="Draft a client survey from project background and hypotheses, then build it in Qualtrics"
        actions={
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span title={library?.names.join('\n') || undefined}>{libraryLine}</span>
            {qualtricsReady && (
              <Button variant="secondary" size="sm" disabled={!!library?.syncing} onClick={() => void refreshLibrary()}>
                Refresh from Qualtrics
              </Button>
            )}
          </div>
        }
      />
      {libraryError && <Notice tone="danger">{libraryError}</Notice>}
      {library && !qualtricsReady && (
        <Notice tone="warning">
          Qualtrics is not configured on this server. Drafting works; the past-survey library and Create in Qualtrics need
          the club&apos;s Qualtrics client.
        </Notice>
      )}
      {library?.last_sync && !library.syncing && !library.last_sync.ok && (
        <Notice tone="danger">Library refresh failed. {library.last_sync.error}</Notice>
      )}
      {library?.last_sync?.ok && !library.syncing && library.last_sync.failed.length > 0 && (
        <Notice tone="warning">Some surveys could not be read: {library.last_sync.failed.join('; ')}</Notice>
      )}

      <div className="survey-builder grid gap-4 lg:grid-cols-2">
        <section className="surface-card flex min-h-[32rem] flex-col" aria-label="Chat">
          <div className="survey-panel-head">
            <h2>Chat</h2>
            <Button variant="ghost" size="sm" aria-expanded={importOpen} onClick={() => setImportOpen((open) => !open)}>
              Import questions
            </Button>
          </div>
          <div className="ui-disclosure" data-open={importOpen}>
            <div>
              <div className="survey-import">
                <p>Paste or upload questions as text; no AI involved. Number each question and put its type in brackets.</p>
                <textarea
                  className="ui-input w-full font-mono text-sm"
                  rows={6}
                  value={importText}
                  placeholder={IMPORT_EXAMPLE}
                  aria-label="Questions to import"
                  onChange={(e) => setImportText(e.target.value)}
                />
                <div className="flex flex-wrap items-center gap-3">
                  <input
                    type="file"
                    accept=".txt,text/plain"
                    aria-label="Questions file"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (file) setImportText(await file.text());
                    }}
                  />
                  <Button size="sm" disabled={!importText.trim()} onClick={importDraft}>
                    Build draft
                  </Button>
                </div>
              </div>
            </div>
          </div>
          <div className="survey-log" role="log" aria-live="polite">
            {log.map((entry, index) => (
              <p key={index} className={`survey-msg survey-msg--${entry.kind}`}>
                {entry.kind === 'error' ? `Error: ${entry.text}` : entry.text}
              </p>
            ))}
            {sending && <p className="survey-msg survey-msg--note">Thinking…</p>}
            <div ref={logEnd} />
          </div>
          <form className="survey-compose" onSubmit={(e) => void send(e)}>
            <textarea
              className="ui-input w-full"
              rows={3}
              value={input}
              placeholder="Paste background and hypotheses, or ask for changes…"
              aria-label="Message"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onInputKey}
            />
            <Button type="submit" className="shrink-0" disabled={sending || !input.trim()}>
              {sending ? 'Sending…' : 'Send'}
            </Button>
          </form>
        </section>

        <section className="surface-card flex min-h-[32rem] flex-col" aria-label="Survey draft">
          <div className="survey-panel-head">
            <h2>Survey draft</h2>
          </div>
          <div className="survey-preview">
            {!draft?.questions.length ? (
              <EmptyState title="No draft yet" body="The draft appears here once there's enough to go on, or import questions." />
            ) : (
              <>
                <h3>{draft.name}</h3>
                <ol>
                  {draft.questions.map((q, i) => (
                    <li key={i} className="survey-question">
                      <div className="survey-question__meta">
                        Q{i + 1} · {TYPE_LABEL[q.type] || q.type}
                        {q.hypothesis ? ` · ${q.hypothesis}` : ''}
                      </div>
                      <div className="survey-question__text">{q.text}</div>
                      {q.type === 'slider' ? (
                        <div className="survey-question__scale">
                          <span>{q.min ?? 0}</span>
                          <span>slider</span>
                          <span>{q.max ?? 10}</span>
                        </div>
                      ) : q.type !== 'text' ? (
                        <ul>
                          {(q.type === 'likert' ? LIKERT : q.choices || []).map((choice, j) => (
                            <li key={j}>{choice}</li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
          <div className="survey-panel-foot">
            <div className="min-w-0 flex-1 text-sm" role="status">
              {createError && <span className="text-[var(--danger)]">Error: {createError}</span>}
              {created && (
                <span>
                  Created with {created.added} of {created.total} questions.{' '}
                  <a href={created.url} target="_blank" rel="noopener noreferrer">
                    Open in Qualtrics
                  </a>
                </span>
              )}
            </div>
            <Button
              disabled={!draft?.questions.length || !qualtricsReady || creating}
              title={qualtricsReady ? undefined : 'Qualtrics is not configured on this server'}
              onClick={() => setConfirming(true)}
            >
              {creating ? 'Creating…' : 'Create in Qualtrics'}
            </Button>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Create in Qualtrics?"
        body={draft ? `"${draft.name}" with ${draft.questions.length} questions becomes a draft survey in the club's Qualtrics account.` : ''}
        confirmLabel="Create"
        busy={creating}
        onConfirm={() => void createInQualtrics()}
        onClose={() => setConfirming(false)}
      />
    </div>
  );
}
