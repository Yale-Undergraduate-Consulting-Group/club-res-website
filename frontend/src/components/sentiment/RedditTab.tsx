import { useState, type FormEvent } from 'react';
import { sentimentApi, type RedditResult } from '../../api';
import { Button, EmptyState, Notice } from '../ui/Primitives';
import { DistributionBar, ScoreRows, Tiles, TrendChart } from './charts';
import { signed } from './format';
import { RunHistory, RunStatus } from './RunPanel';
import { useRunWorkspace } from './useRunWorkspace';

const WINDOWS = [
  ['hour', 'Past hour'], ['day', 'Past day'], ['week', 'Past week'],
  ['month', 'Past month'], ['year', 'Past year'], ['all', 'All time'],
] as const;

function Results({ result }: { result: RedditResult }) {
  const { summary } = result;
  if (!summary.count) return <EmptyState title="No posts found" body="Reddit returned nothing for that search. Try other subreddits, a broader search or a longer time window." />;
  const positive = Math.round((summary.distribution.positive / summary.count) * 100);
  return (
    <div className="space-y-4">
      <section className="surface-card space-y-4" aria-label="Summary">
        <h2 className="app-section-title">“{result.query}” on Reddit</h2>
        <Tiles items={[
          { label: 'Posts', value: String(summary.count) },
          { label: 'Average sentiment', value: signed(summary.avg_sentiment) },
          { label: 'Positive', value: `${positive}%` },
        ]} />
        <DistributionBar summary={summary} />
      </section>
      <section className="surface-card space-y-3" aria-label="Trend">
        <h2 className="app-section-title">Average sentiment by month</h2>
        <TrendChart
          label="Average sentiment of posts by month"
          points={result.monthly_trend.map((m) => ({ label: m.month, value: m.avg_sentiment, count: m.post_count }))}
        />
      </section>
      <section className="surface-card space-y-3" aria-label="Keywords">
        <h2 className="app-section-title">Most used words</h2>
        <ScoreRows
          rows={result.top_keywords.slice(0, 20).map((k) => ({ label: k.word, value: k.avg_sentiment, note: `${k.count} posts` }))}
          empty="No word appears in three or more posts."
        />
      </section>
      <section className="surface-card" aria-label="Subreddits">
        <h2 className="app-section-title">By subreddit</h2>
        <div className="mt-3">
          <ScoreRows rows={result.subreddit_breakdown.map((s) => ({ label: `r/${s.subreddit}`, value: s.avg_sentiment, note: `${s.count} posts` }))} empty="" />
        </div>
      </section>
      <section className="surface-card" aria-label="Posts">
        <h2 className="app-section-title">Top posts</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm sentiment-table">
            <thead>
              <tr className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-slate-500">
                <th>Post</th><th>Subreddit</th><th>Votes</th><th>Sentiment</th>
              </tr>
            </thead>
            <tbody>
              {result.posts.slice(0, 100).map((p) => (
                <tr key={p.id} className="border-t border-[var(--border)]">
                  <td><a className="underline" href={p.url} target="_blank" rel="noreferrer">{p.title}</a></td>
                  <td>r/{p.subreddit}</td>
                  <td>{p.score}</td>
                  <td>{p.label} {signed(p.score_sentiment)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default function RedditTab({ configured }: { configured: boolean | null }) {
  const ws = useRunWorkspace<RedditResult>('reddit');
  const [subreddits, setSubreddits] = useState('');
  const [query, setQuery] = useState('');
  const [timeFilter, setTimeFilter] = useState('year');
  const [limit, setLimit] = useState(50);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    ws.setError('');
    try {
      const { id } = await sentimentApi.startReddit({
        subreddits: subreddits.split(',').map((s) => s.trim()).filter(Boolean),
        query: query.trim(),
        time_filter: timeFilter,
        limit,
      });
      await ws.refresh();
      ws.select(id);
    } catch (e) {
      ws.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {ws.error && <Notice tone="danger">{ws.error}</Notice>}
      {configured === false ? (
        <EmptyState
          title="Reddit is not configured on this server"
          body="An admin can turn this on by adding REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to the server's settings."
        />
      ) : (
        <form className="surface-card space-y-4" onSubmit={(e) => void submit(e)}>
          <h2 className="app-section-title">Search Reddit</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm">
              <span className="block mb-1">Subreddits (comma separated, up to 5)</span>
              <input className="ui-input w-full" value={subreddits} placeholder="yale, consulting" required onChange={(e) => setSubreddits(e.target.value)} />
            </label>
            <label className="text-sm">
              <span className="block mb-1">Search</span>
              <input className="ui-input w-full" value={query} maxLength={200} required onChange={(e) => setQuery(e.target.value)} />
            </label>
            <label className="text-sm">
              <span className="block mb-1">Time window</span>
              <select className="ui-input w-full" value={timeFilter} onChange={(e) => setTimeFilter(e.target.value)}>
                {WINDOWS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="text-sm">
              <span className="block mb-1">Posts per subreddit (1 to 100)</span>
              <input className="ui-input w-full" type="number" min={1} max={100} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
            </label>
          </div>
          <Button type="submit" disabled={busy || configured === null || !subreddits.trim() || !query.trim() || limit < 1 || limit > 100}>
            {busy ? 'Starting…' : 'Analyse'}
          </Button>
        </form>
      )}

      <h2 className="app-section-title">Your analyses</h2>
      <RunHistory runs={ws.runs} selectedId={ws.selectedId} onSelect={ws.select} />
      {ws.run && (
        <>
          <RunStatus run={ws.run} onDelete={(id) => void ws.remove(id)} onError={ws.setError} />
          {ws.run.status === 'completed' && ws.run.result && <Results result={ws.run.result} />}
        </>
      )}
    </div>
  );
}
