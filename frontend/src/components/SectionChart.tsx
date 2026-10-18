import { useState } from 'react';
import type { BreakdownSection } from '../api';
import OutcomePie from './OutcomePie';

/**
 * One measured section, drawn as whatever the server says it is.
 *
 * The page does not know what sections exist. A new measurement is a new
 * query on the server and appears here automatically, which is the only way
 * a statistics page keeps up with a product that keeps growing.
 */

// The club's blue ladder, walked in order so adjacent slices stay distinct.
const RAMP = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)'];

export default function SectionChart({ section }: { section: BreakdownSection }) {
  const [active, setActive] = useState<string | null>(null);
  const rows = Array.isArray(section.rows) ? section.rows : [];
  const max = Math.max(...rows.map((r) => r.value), 1);
  const hasSecondary = rows.some((r) => r.secondary !== null);

  return (
    <section className="surface-card p-5" aria-label={section.title}>
      <h2 className="app-section-title">{section.title}</h2>
      {section.note && <p className="mt-0.5 text-xs text-slate-500">{section.note}</p>}

      {section.chart === 'pie' ? (
        <div className="mt-3">
          <OutcomePie
            title={section.title}
            showTitle={false}
            slices={rows.map((row, index) => ({
              label: row.label,
              value: row.value,
              colour: RAMP[index % RAMP.length],
            }))}
            empty="Nothing measured yet."
          />
        </div>
      ) : (
        <ul className="mt-3 space-y-2">
          {rows.map((row, index) => {
            const on = active === row.label;
            const share = Math.round((row.value / max) * 100);
            return (
              <li
                key={row.label}
                onMouseEnter={() => setActive(row.label)}
                onMouseLeave={() => setActive(null)}
                className="text-xs"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-slate-700" title={row.label}>{row.label}</span>
                  <span className="shrink-0 font-semibold text-deep-navy">
                    {row.value}
                    {hasSecondary && row.secondary !== null && (
                      <span className="ml-1 font-normal text-slate-500">
                        · {row.secondary} {section.id === 'weeks' ? 'replied' : section.id === 'members' ? 'replied' : 'mailed'}
                      </span>
                    )}
                  </span>
                </div>
                {/* Hovering a bar lifts it out of the ladder, so a long list
                    stays readable while a single row is being looked at. */}
                <div className="mt-1 h-2 overflow-hidden rounded-full" style={{ background: 'var(--chart-idle)' }}>
                  <div
                    className="h-2 rounded-full"
                    style={{
                      width: `${share}%`,
                      background: RAMP[index % RAMP.length],
                      opacity: active && !on ? 0.45 : 1,
                      transition: 'opacity 120ms ease-out',
                    }}
                  />
                  {hasSecondary && row.secondary !== null && row.value > 0 && (
                    <div
                      className="-mt-2 h-2 rounded-full"
                      style={{
                        width: `${Math.round((row.secondary / max) * 100)}%`,
                        background: 'var(--chart-good)',
                        opacity: active && !on ? 0.45 : 1,
                      }}
                    />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
