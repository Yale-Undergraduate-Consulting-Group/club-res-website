import { useCallback, useEffect, useRef, useState } from 'react';
import { sentimentApi, type SentimentKind, type SentimentRun, type SentimentRunRow } from '../../api';

const POLL_MS = 2500;

/**
 * One tab's runs: the member's history, the run they have open, and polling
 * while that run is queued or running.
 */
export function useRunWorkspace<R>(kind: SentimentKind) {
  const [runs, setRuns] = useState<SentimentRunRow[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loaded, setLoaded] = useState<SentimentRun<R> | null>(null);
  const [error, setError] = useState('');
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const rows = await sentimentApi.runs(kind);
      if (mounted.current) setRuns(rows);
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    }
  }, [kind]);

  useEffect(() => {
    mounted.current = true;
    sentimentApi.runs(kind)
      .then((rows) => { if (mounted.current) setRuns(rows); })
      .catch((e: Error) => { if (mounted.current) setError(e.message); });
    return () => { mounted.current = false; };
  }, [kind]);

  useEffect(() => {
    if (selectedId === null) return undefined;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const run = await sentimentApi.run<R>(selectedId);
        if (stopped) return;
        setLoaded(run);
        if (run.status === 'queued' || run.status === 'running') timer = setTimeout(() => void tick(), POLL_MS);
        else void refresh();
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void tick();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [selectedId, refresh]);

  const select = (id: number | null) => { setError(''); setSelectedId(id); };
  const remove = async (id: number) => {
    try {
      await sentimentApi.remove(id);
      if (selectedId === id) setSelectedId(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return {
    runs,
    run: loaded && loaded.id === selectedId ? loaded : null,
    selectedId,
    select,
    remove,
    refresh,
    error,
    setError,
  };
}
