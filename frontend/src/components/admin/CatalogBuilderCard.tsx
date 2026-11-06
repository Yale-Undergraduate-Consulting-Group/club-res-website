import { useCallback, useEffect, useState } from 'react';
import { api, type CatalogBuilderStatus } from '../../api';

const REFRESH_MS = 30_000;

/**
 * The catalog builder's switch and what it is doing.
 *
 * The builder works down the company register in the background, running the
 * same Find people search a member would, so the people at most companies are
 * already found when a member opens them. It spends the club's search and AI
 * allowances, so it is off until an admin turns it on, and it pauses itself
 * whenever members are using more than its share.
 */
export default function CatalogBuilderCard() {
  const [status, setStatus] = useState<CatalogBuilderStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setStatus(await api.admin.operations.catalogBuilder());
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read the catalog builder');
    }
  }, []);

  useEffect(() => {
    const first = window.setTimeout(() => { void load(); }, 0);
    const timer = window.setInterval(() => { void load(); }, REFRESH_MS);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [load]);

  const toggle = async () => {
    if (!status) return;
    setBusy(true);
    setError('');
    try {
      setStatus(await api.admin.operations.setCatalogBuilder(!status.enabled));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change the catalog builder');
    } finally {
      setBusy(false);
    }
  };

  const state = !status ? 'Loading…'
    : !status.enabled ? 'Off'
    : status.current_company ? `Searching ${status.current_company}`
    : status.paused_reason ? `Paused: ${status.paused_reason}`
    : 'On, waiting for the next tick';

  return (
    <div className="surface-card rounded-xl p-6" data-section="catalog-builder">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)]">Catalog builder</h2>
        <button
          type="button"
          className={`ui-button ui-button--sm ${status?.enabled ? 'ui-button--secondary' : 'ui-button--primary'}`}
          disabled={!status || busy}
          aria-pressed={status?.enabled ?? false}
          onClick={() => void toggle()}
        >
          {busy ? 'Saving…' : status?.enabled ? 'Turn off' : 'Turn on'}
        </button>
      </div>
      <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
        Works down the company register day and night (largest US listed companies first, then the
        biggest UK groups, then the rest by size), running the same Find people search a member would.
        Everyone it finds shows on the Find page for that company; members still keep them before
        they reach contacts. It pauses whenever the club passes its share of searches or AI.
      </p>
      {error && <p className="text-sm text-red-700 mb-3">{error}</p>}
      <p className="text-sm font-medium text-deep-navy mb-3" data-testid="catalog-builder-state">
        {state}
        {status?.current_company && status.current_progress ? ` · ${status.current_progress}` : ''}
      </p>
      {status && (
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div className="rounded-lg border border-pale-sky/80 px-4 py-3">
            <dt className="text-xs text-slate-500">Companies today</dt>
            <dd className="font-semibold text-deep-navy tabular-nums">{status.companies_done_today}</dd>
          </div>
          <div className="rounded-lg border border-pale-sky/80 px-4 py-3">
            <dt className="text-xs text-slate-500">People found today</dt>
            <dd className="font-semibold text-deep-navy tabular-nums">{status.people_found_today}</dd>
          </div>
          <div className="rounded-lg border border-pale-sky/80 px-4 py-3">
            <dt className="text-xs text-slate-500">Club searches, last hour</dt>
            <dd className="font-semibold text-deep-navy tabular-nums">
              {status.searches_last_hour ?? '—'} / {status.limits.searches_per_hour}
            </dd>
          </div>
          <div className="rounded-lg border border-pale-sky/80 px-4 py-3">
            <dt className="text-xs text-slate-500">Companies so far</dt>
            <dd className="font-semibold text-deep-navy tabular-nums">{status.companies_done_total}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}
