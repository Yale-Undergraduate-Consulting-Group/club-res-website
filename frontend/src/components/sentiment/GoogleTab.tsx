import { useState, type FormEvent } from 'react';
import { sentimentApi, type GooglePlace, type GoogleResult, type GoogleReview } from '../../api';
import { Button, EmptyState, Notice } from '../ui/Primitives';
import { DistributionBar, ScoreRows, Tiles } from './charts';
import { signed } from './format';
import { RunHistory, RunStatus } from './RunPanel';
import { useRunWorkspace } from './useRunWorkspace';

const MAX_PLACES = 5;

function Quotes({ title, reviews }: { title: string; reviews: GoogleReview[] }) {
  return (
    <div>
      <h4 className="text-sm font-semibold mb-2">{title}</h4>
      {reviews.length === 0 ? <p className="text-sm text-slate-500">None.</p> : (
        <ul className="space-y-2">
          {reviews.map((r, i) => (
            <li key={i} className="sentiment-quote">
              <span className="text-xs text-slate-500">{r.author}{r.rating ? ` · ${r.rating}★` : ''} · {signed(r.score)}</span>
              <div>{r.text}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Results({ result }: { result: GoogleResult }) {
  if (!result.summary.count && result.places.every((p) => !p.error)) {
    return <EmptyState title="No written reviews" body="These places have no reviews with text to analyse." />;
  }
  return (
    <div className="space-y-4">
      <section className="surface-card space-y-4" aria-label="Places compared">
        <h2 className="app-section-title">Places compared</h2>
        <Tiles items={[
          { label: 'Reviews scored', value: String(result.summary.count) },
          { label: 'Average sentiment', value: signed(result.summary.avg_sentiment) },
          { label: 'Places', value: String(result.places.length) },
        ]} />
        <ScoreRows rows={result.places.map((p) => ({ label: p.name, value: p.avg_sentiment, note: `${p.count} reviews` }))} empty="" />
      </section>
      {result.places.map((place) => (
        <section key={place.data_id} className="surface-card space-y-3" aria-label={place.name}>
          <div>
            <h3>{place.name}</h3>
            <p className="text-xs text-slate-500">
              {place.address}{place.rating ? ` · Google rating ${place.rating}` : ''}{place.review_count ? ` · ${place.review_count} reviews on Google` : ''}
            </p>
          </div>
          {place.error ? <Notice tone="danger">{place.error}</Notice> : (
            <>
              <DistributionBar summary={place} />
              <div className="grid gap-4 md:grid-cols-2">
                <Quotes title="Most positive" reviews={place.top_positive} />
                <Quotes title="Most negative" reviews={place.top_negative} />
              </div>
            </>
          )}
        </section>
      ))}
    </div>
  );
}

export default function GoogleTab({ configured }: { configured: boolean | null }) {
  const ws = useRunWorkspace<GoogleResult>('google');
  const [query, setQuery] = useState('');
  const [places, setPlaces] = useState<GooglePlace[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [perPlace, setPerPlace] = useState(20);
  const [busy, setBusy] = useState(false);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    ws.setError('');
    try {
      setPlaces((await sentimentApi.searchPlaces(query.trim())).places);
      setPicked([]);
    } catch (e) {
      ws.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    ws.setError('');
    try {
      const { id } = await sentimentApi.startGoogle((places ?? []).filter((p) => picked.includes(p.data_id)), perPlace);
      await ws.refresh();
      ws.select(id);
    } catch (e) {
      ws.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length < MAX_PLACES ? [...cur, id] : cur));

  return (
    <div className="space-y-4">
      {ws.error && <Notice tone="danger">{ws.error}</Notice>}
      {configured === false ? (
        <EmptyState
          title="Google reviews are not configured on this server"
          body="This tab uses a paid search service. An admin can turn it on by adding SERPAPI_KEY to the server's settings."
        />
      ) : (
        <section className="surface-card space-y-4">
          <h2 className="app-section-title">Find places</h2>
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => void search(e)}>
            <label className="flex-1 min-w-[14rem] text-sm">
              <span className="block mb-1">Business and city</span>
              <input className="ui-input w-full" value={query} minLength={2} maxLength={200} placeholder="Pika Coffee New Haven" required onChange={(e) => setQuery(e.target.value)} />
            </label>
            <Button type="submit" variant="secondary" disabled={busy || configured === null || query.trim().length < 2}>Search</Button>
          </form>
          {places !== null && (places.length === 0 ? <p className="text-sm text-slate-500">No places found.</p> : (
            <>
              <fieldset className="space-y-2">
                <legend className="text-sm mb-1">Choose up to {MAX_PLACES} places. Each search of reviews uses the club's paid quota.</legend>
                {places.map((p) => (
                  <label key={p.data_id} className="flex items-start gap-2 text-sm">
                    <input type="checkbox" checked={picked.includes(p.data_id)} onChange={() => toggle(p.data_id)} />
                    <span>{p.name}<span className="block text-xs text-slate-500">{p.address}{p.rating ? ` · ${p.rating}★` : ''}{p.review_count ? ` · ${p.review_count} reviews` : ''}</span></span>
                  </label>
                ))}
              </fieldset>
              <label className="block text-sm max-w-xs">
                <span className="block mb-1">Reviews per place (5 to 40)</span>
                <input className="ui-input w-full" type="number" min={5} max={40} value={perPlace} onChange={(e) => setPerPlace(Number(e.target.value))} />
              </label>
              <Button disabled={busy || picked.length === 0 || perPlace < 5 || perPlace > 40} onClick={() => void start()}>
                {busy ? 'Starting…' : picked.length === 1 ? 'Analyse 1 place' : `Analyse ${picked.length} places`}
              </Button>
            </>
          ))}
        </section>
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
