import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { PortalApi } from '../../api';

/**
 * What the portal sections need from where they are mounted: the alumni page
 * (alumni cookie, /api/alumni) or Admin → Alumni (member session, where a club
 * admin acts as the board).
 */
export type PortalContextValue = {
  api: PortalApi;
  /** Board members and club admins write posts and events and read the inbox. */
  canWrite: boolean;
  /** Turns a failed call into a message ('' when the failure was handled, e.g. a sign-in redirect). */
  fail: (error: unknown) => string;
};

export const PortalContext = createContext<PortalContextValue | null>(null);

export function usePortal(): PortalContextValue {
  const value = useContext(PortalContext);
  if (!value) throw new Error('Portal sections must be rendered inside a PortalContext.Provider');
  return value;
}

/** Loads `load` on mount and whenever it changes; `refresh` loads again. */
export function useLoad<T>(load: () => Promise<T>) {
  const { fail } = usePortal();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const refresh = useCallback(() => {
    load().then((result) => { setData(result); setError(''); }).catch((e: unknown) => setError(fail(e)));
  }, [load, fail]);
  useEffect(refresh, [refresh]);
  return { data, setData, error, refresh };
}

export function formatDate(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatDateTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
