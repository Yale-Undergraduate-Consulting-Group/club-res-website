import { useRef, useState, type FormEvent } from 'react';
import { sentimentApi, type SentimentLabel, type TranscriptResult } from '../../api';
import { Button, Chip, EmptyState, Notice } from '../ui/Primitives';
import { DistributionBar, ScoreRows, Tiles } from './charts';
import { signed } from './format';
import { RunHistory, RunStatus } from './RunPanel';
import { useRunWorkspace } from './useRunWorkspace';

const MAX_FILES = 20;
const MAX_MB = 10;
const PAGE = 100;

type SentenceFilter = 'all' | SentimentLabel | 'company';

function Results({ result }: { result: TranscriptResult }) {
  const [filter, setFilter] = useState<SentenceFilter>('all');
  const [shown, setShown] = useState(PAGE);
  const { overall } = result;
  const rows = [
    { label: `${result.company} (without competitors)`, value: overall.company.avg_sentiment, note: `${overall.company.count} sentences` },
    ...overall.competitors.map((c) => ({ label: c.name, value: c.avg_sentiment, note: `${c.count} sentences` })),
    { label: 'All interviewee sentences', value: overall.all.avg_sentiment, note: `${overall.all.count} sentences` },
  ];
  const sentences = result.sentences.filter((s) => {
    if (filter === 'all') return true;
    if (filter === 'company') return s.mentions_company;
    return s.label === filter;
  });
  return (
    <div className="space-y-4">
      <section className="surface-card space-y-4" aria-label="Overall">
        <h2 className="app-section-title">{result.company} against competitors</h2>
        <Tiles items={[
          { label: `${result.company} average`, value: signed(overall.company.avg_sentiment) },
          { label: 'Sentences scored', value: String(overall.all.count) },
          { label: 'Transcripts', value: String(result.transcripts.length) },
        ]} />
        <ScoreRows rows={rows} empty="Nothing scored." />
        <DistributionBar summary={overall.all} />
        <p className="text-xs text-slate-500">
          Scores run from -1 (negative) to +1 (positive). Sentences naming both {result.company} and a competitor count only toward the competitor.
          Interviewer lines and sentences under three words are not scored.
        </p>
      </section>

      <section className="surface-card" aria-label="Per transcript">
        <h2 className="app-section-title">By transcript</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm sentiment-table">
            <thead>
              <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-slate-500">
                <th>Transcript</th><th>Interviewee</th><th>Scored</th><th>Average</th><th>{result.company}</th><th className="min-w-[12rem]">Mix</th>
              </tr>
            </thead>
            <tbody>
              {result.transcripts.map((t) => (
                <tr key={t.name} className="border-t border-[var(--border)]">
                  <td>{t.name}</td>
                  <td>{t.interviewee ?? '—'}</td>
                  <td>{t.all.count}</td>
                  <td>{signed(t.all.avg_sentiment)}</td>
                  <td>{signed(t.company.avg_sentiment)}</td>
                  <td><DistributionBar summary={t.all} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="surface-card space-y-3" aria-label="Words">
        <h2 className="app-section-title">Words in sentences about {result.company}</h2>
        <ScoreRows
          rows={result.word_stats.slice(0, 20).map((w) => ({ label: w.word, value: w.avg_sentiment, note: `${w.count}×` }))}
          empty={`No word appears at least twice in sentences that name ${result.company} without a competitor.`}
        />
      </section>

      <section className="surface-card space-y-3" aria-label="Sentences">
        <h2 className="app-section-title">Sentences</h2>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter sentences">
          {(['all', 'positive', 'neutral', 'negative', 'company'] as const).map((f) => (
            <Chip key={f} selected={filter === f} onClick={() => { setFilter(f); setShown(PAGE); }}>
              {f === 'all' ? 'All' : f === 'company' ? `Mentions ${result.company}` : f[0].toUpperCase() + f.slice(1)}
            </Chip>
          ))}
        </div>
        {sentences.length === 0 ? <p className="text-sm text-slate-500">No sentences match.</p> : (
          <ul className="space-y-2">
            {sentences.slice(0, shown).map((s, i) => (
              <li key={i} className="sentiment-quote">
                <span className="text-xs text-slate-500">
                  {result.transcripts[s.transcript].name}{s.speaker ? ` · ${s.speaker}` : ''} ·{' '}
                  {s.score === null ? 'not scored' : `${s.label} ${signed(s.score)}`}
                </span>
                <div>{s.text}</div>
              </li>
            ))}
          </ul>
        )}
        {sentences.length > shown && <Button variant="secondary" size="sm" onClick={() => setShown(shown + PAGE)}>Show more</Button>}
      </section>
    </div>
  );
}

export default function InterviewsTab() {
  const ws = useRunWorkspace<TranscriptResult>('transcripts');
  const [company, setCompany] = useState('');
  const [competitors, setCompetitors] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    ws.setError('');
    try {
      const { id } = await sentimentApi.startTranscripts(company.trim(), competitors, files);
      setFiles([]);
      if (input.current) input.current.value = '';
      await ws.refresh();
      ws.select(id);
    } catch (e) {
      ws.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const tooBig = files.some((f) => f.size > MAX_MB * 1024 * 1024);
  return (
    <div className="space-y-4">
      {ws.error && <Notice tone="danger">{ws.error}</Notice>}
      <form className="surface-card space-y-4" onSubmit={(e) => void submit(e)}>
        <h2 className="app-section-title">Analyse interview transcripts</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm">
            <span className="block mb-1">Company</span>
            <input className="ui-input w-full" value={company} maxLength={100} required onChange={(e) => setCompany(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="block mb-1">Competitors (comma separated, optional)</span>
            <input className="ui-input w-full" value={competitors} maxLength={1000} onChange={(e) => setCompetitors(e.target.value)} />
          </label>
        </div>
        <label className="block text-sm">
          <span className="block mb-1">Transcripts (.docx, .txt or .pdf; up to {MAX_FILES} files of {MAX_MB} MB)</span>
          <input
            ref={input}
            className="ui-input w-full"
            type="file"
            multiple
            accept=".docx,.txt,.pdf"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        <p className="text-xs text-slate-500">
          Files are read once and are not kept; only the scored sentences are stored, for you alone. Name each file after the
          interviewee (Jane_Doe.docx) so the interviewee can be told from the interviewer.
        </p>
        {files.length > MAX_FILES && <Notice tone="warning">Choose at most {MAX_FILES} files.</Notice>}
        {tooBig && <Notice tone="warning">A file is larger than {MAX_MB} MB.</Notice>}
        <Button type="submit" disabled={busy || !company.trim() || files.length === 0 || files.length > MAX_FILES || tooBig}>
          {busy ? 'Uploading…' : 'Analyse'}
        </Button>
      </form>

      <h2 className="app-section-title">Your analyses</h2>
      <RunHistory runs={ws.runs} selectedId={ws.selectedId} onSelect={ws.select} />
      {ws.run ? (
        <>
          <RunStatus run={ws.run} onDelete={(id) => void ws.remove(id)} onError={ws.setError} />
          {ws.run.status === 'completed' && ws.run.result && <Results result={ws.run.result} />}
        </>
      ) : ws.selectedId === null && !!ws.runs?.length && <EmptyState title="Choose an analysis" body="Pick one above to see its results." />}
    </div>
  );
}
