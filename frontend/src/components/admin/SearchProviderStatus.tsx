import { useEffect, useState } from 'react';
import { api, type SearchProviderHealth } from '../../api';
import { StatusBadge } from '../ui/Primitives';

/**
 * Monid (the TinyFish search broker) as the last real call left it. Reading it
 * costs nothing: the server keeps what the last search or fetch showed instead
 * of probing, because a probe would spend the wallet this is reporting on.
 */

const STATE: Record<SearchProviderHealth['state'], { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger'; hint: string }> = {
  unconfigured: { label: 'Not configured', tone: 'neutral', hint: 'MONID_API_KEY is not set.' },
  unknown: { label: 'Not used yet', tone: 'neutral', hint: 'No search has run since the app started; the first one will show here.' },
  working: { label: 'Working', tone: 'success', hint: 'The last call succeeded.' },
  out_of_credit: { label: 'Out of credit', tone: 'danger', hint: 'Monid refused the last call because its wallet is empty. Top it up; searches resume by themselves.' },
  auth: { label: 'Key refused', tone: 'danger', hint: 'Monid refused MONID_API_KEY. Check the key in the app environment.' },
  rate_limited: { label: 'Rate limited', tone: 'warning', hint: 'Monid is limiting requests; the app waits a minute and tries again.' },
  timeout: { label: 'Failing: not answering', tone: 'danger', hint: 'The last call timed out.' },
  failing: { label: 'Failing', tone: 'danger', hint: 'The last call failed; the logs have the detail (logger yucg.firecrawl).' },
};

const at = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toLocaleString() : 'never');

export default function SearchProviderStatus({ refreshKey }: { refreshKey: unknown }) {
  const [health, setHealth] = useState<SearchProviderHealth | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api.admin.operations.searchProviderHealth()
      .then((res) => { if (live) { setHealth(res.monid); setFailed(false); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [refreshKey]);

  if (failed) return <p className="text-sm text-red-700 mb-3">Could not read the search provider's status.</p>;
  if (!health) return null;
  const state = STATE[health.state];
  return (
    <div className="mb-4 rounded-lg border border-pale-sky/80 px-4 py-3 text-sm" data-testid="search-provider-status">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-deep-navy dark:text-[var(--text-primary)]">Monid (TinyFish)</span>
        <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
      </div>
      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
        {state.hint}
        {health.paused_for_s > 0 && ` Calls are on hold for about ${Math.ceil(health.paused_for_s / 60)} min so a burst of searches does not hammer it.`}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        Last success: {at(health.last_ok_at)} · last failure: {at(health.last_failure_at)}. From real calls only; nothing is probed here.
      </p>
    </div>
  );
}
