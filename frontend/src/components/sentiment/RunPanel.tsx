import { useState } from 'react';
import { sentimentApi, type SentimentRun, type SentimentRunRow } from '../../api';
import { Button, EmptyState, Notice, StatusBadge } from '../ui/Primitives';

const TONE = { queued: 'neutral', running: 'info', completed: 'success', failed: 'danger' } as const;
const WORD = { queued: 'Queued', running: 'Running', completed: 'Done', failed: 'Failed' } as const;

function when(iso: string): string {
  const date = new Date(iso.includes('T') ? iso : `${iso.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

/** The member's earlier analyses of one kind; choosing one opens it. */
export function RunHistory({ runs, selectedId, onSelect }: {
  runs: SentimentRunRow[] | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  if (runs === null) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!runs.length) return <EmptyState title="No analyses yet" body="Start one above; it appears here and keeps running if you leave the page." />;
  return (
    <div className="surface-card surface-card--flush" aria-label="Your analyses">
      {runs.map((run) => (
        <button key={run.id} type="button" className="sentiment-run" aria-current={run.id === selectedId} onClick={() => onSelect(run.id)}>
          <span>
            {run.title}
            <span className="block text-xs text-slate-500">{when(run.created_at)}</span>
          </span>
          <StatusBadge tone={TONE[run.status]}>{WORD[run.status]}</StatusBadge>
        </button>
      ))}
    </div>
  );
}

/** Progress while a run works, the reason when it failed, and the actions on a finished one. */
export function RunStatus<R>({ run, onDelete, onError }: {
  run: SentimentRun<R>;
  onDelete: (id: number) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const working = run.status === 'queued' || run.status === 'running';
  const download = async () => {
    setBusy(true);
    try {
      await sentimentApi.downloadCsv(run.id, `sentiment-${run.kind}-${run.id}.csv`);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="surface-card space-y-3" aria-label="Selected analysis">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="app-section-title">{run.title}</h2>
        <div className="flex gap-2">
          {run.status === 'completed' && <Button size="sm" variant="secondary" onClick={() => void download()} disabled={busy}>Download CSV</Button>}
          {!working && <Button size="sm" variant="ghost" onClick={() => onDelete(run.id)}>Delete</Button>}
        </div>
      </div>
      {working && (
        <div role="status">
          <div className="sentiment-progress" role="progressbar" aria-valuenow={run.progress_pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="sentiment-progress__fill" style={{ width: `${run.progress_pct}%` }} />
          </div>
          <p className="mt-1 text-xs text-slate-500">{run.progress_message || 'Working'} ({run.progress_pct}%)</p>
        </div>
      )}
      {run.status === 'failed' && <Notice tone="danger">{run.error_message || 'The analysis failed.'}</Notice>}
    </section>
  );
}
