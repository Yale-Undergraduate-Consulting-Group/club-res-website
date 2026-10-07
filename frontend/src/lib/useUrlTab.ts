import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

export function useUrlTab<T extends string>(allowed: readonly T[], fallback: T, key = 'view') {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);
  const active = allowed.includes(raw as T) ? raw as T : fallback;

  const setActive = useCallback((next: T) => {
    setParams((current) => {
      const updated = new URLSearchParams(current);
      if (next === fallback) updated.delete(key);
      else updated.set(key, next);
      return updated;
    }, { replace: true });
  }, [fallback, key, setParams]);

  return [active, setActive] as const;
}
