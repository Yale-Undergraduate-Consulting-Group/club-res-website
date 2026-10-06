import { useEffect, useRef, useState } from 'react';
import { api, type RoleSuggestions } from '../../api';

const SOURCE_LABEL: Record<string, string> = {
  roster: 'SEC filing',
  run: 'earlier search',
  catalog: 'club contacts',
  search: 'LinkedIn profile',
  jobs: 'job posting',
};

/**
 * Company-specific role vocabulary under the titles field. Chips are titles
 * actually observed at the company (click to add); the equivalents strip
 * tells the member how the company labels what they asked for, so hints
 * match reality before the search runs. Debounced; aborts stale requests.
 */
export default function RoleSuggestionBubbles({
  company,
  domain,
  hints,
  onAdd,
  onObserved,
}: {
  company: string;
  domain: string;
  hints: string;
  onAdd: (title: string) => void;
  /** The titles this company actually uses, once known. */
  onObserved?: (titles: string[]) => void;
}) {
  const [rawData, setData] = useState<RoleSuggestions | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastKey = useRef('');

  useEffect(() => {
    const c = company.trim();
    if (c.length < 2) {
      lastKey.current = '';
      return;
    }
    const key = `${c.toLowerCase()}|${domain.trim().toLowerCase()}|${hints.trim().toLowerCase()}`;
    if (key === lastKey.current) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const next = await api.yucgoutreach.roleSuggestions(
          { company: c, domain: domain.trim() || undefined, hints: hints.trim() || undefined },
          controller.signal,
        );
        lastKey.current = key;
        setData(next);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === 'AbortError')) {
          setError(e instanceof Error ? e.message : 'Suggestions unavailable');
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 700);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [company, domain, hints]);

  // While a new company's request is in flight, the previous company's
  // suggestions must not be shown under the new name. A payload missing
  // these fields is treated as no data rather than crashing the page.
  const usable = rawData && typeof rawData.company === 'string'
    && rawData.company.toLowerCase() === company.trim().toLowerCase()
    && Array.isArray(rawData.roles) && Array.isArray(rawData.equivalents);
  const data = usable ? rawData : null;
  // Titles this company is observed to use, handed up so the field can fill
  // itself rather than making the member guess the house vocabulary.
  const observedKey = data ? data.roles.map((r) => r.title).join('|') : '';
  useEffect(() => {
    if (!observedKey || !onObserved) return;
    const timer = window.setTimeout(() => onObserved(observedKey.split('|')), 0);
    return () => window.clearTimeout(timer);
  }, [observedKey, onObserved]);

  if (company.trim().length < 2) return null;
  // A limit is not the member's problem to read about: the search already
  // looks for the level they chose, so the suggestions just stay quiet.
  const quiet = (text: string | null | undefined) => !text || /limit|try again later/i.test(text);
  const present = new Set(
    hints.split(/[,;/]|\band\b|\bor\b/i).map((s) => s.trim().toLowerCase()).filter(Boolean),
  );

  return (
    <div className="space-y-2" data-testid="role-suggestions" aria-live="polite">
      {loading && !data && <p className="text-xs text-slate-500">Looking up roles at {company.trim()}…</p>}
      {!quiet(error) && <p className="text-xs text-slate-500">{error}</p>}

      {data && data.equivalents.length > 0 && (
        <ul className="space-y-1.5">
          {data.equivalents.map((eq) => (
            <li key={eq.asked} className="rounded-lg border border-pale-sky/70 bg-pale-sky/20 px-3 py-2 text-xs text-deep-navy">
              <span className="font-medium">{eq.asked}</span>
              {eq.at_company.length > 0 ? (
                <>
                  <span className="text-slate-500"> → at {data.company}: </span>
                  {eq.at_company.map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => onAdd(t)}
                      disabled={present.has(t.toLowerCase())}
                      className="ml-1 rounded-full border border-deep-navy/30 bg-white px-2 py-0.5 text-[11px] font-medium hover:bg-deep-navy hover:text-white disabled:opacity-40"
                    >
                      + {t}
                    </button>
                  ))}
                </>
              ) : (
                <span className="text-slate-500"> — no matching title seen at {data.company}</span>
              )}
              {eq.note && <div className="mt-0.5 text-slate-600">{eq.note}</div>}
            </li>
          ))}
        </ul>
      )}

      {data && data.roles.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
            Roles seen at {data.company}
            {loading && <span className="normal-case tracking-normal"> · updating…</span>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {data.roles.map((r) => (
              <button
                key={r.title}
                type="button"
                onClick={() => onAdd(r.title)}
                disabled={present.has(r.title.toLowerCase())}
                title={`${r.count} seen · ${SOURCE_LABEL[r.source] || r.source}`}
                className="rounded-full border border-pale-sky bg-white px-2.5 py-1 text-xs text-deep-navy hover:border-deep-navy disabled:opacity-40"
              >
                {r.title}
                <span className="ml-1 text-[10px] text-slate-400">{r.count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {data && !quiet(data.note) && <p className="text-xs text-slate-500">{data.note}</p>}
      {data && data.roles.length === 0 && !loading && !data.note && (
        <p className="text-xs text-slate-500">No roles seen yet at {data.company}; the search looks for the level you chose.</p>
      )}
    </div>
  );
}
