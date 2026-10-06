/**
 * Animated operations charts - 3blue1brown style with theme-aware background.
 * White in light mode, dark in dark mode. Smooth bar and line animations.
 */
import { useEffect, useState } from 'react';
import { useTheme } from '../contexts/useTheme';

type Aggregates = { by_event_type?: { event_type: string; count: number }[]; by_resource_type?: { resource_type: string; count: number }[]; days?: number };

const STAGGER_MS = 60;

export function AnimatedBarChart({
  data,
  labelKey,
  valueKey,
  maxBars = 10,
  title,
}: {
  data: { [k: string]: string | number }[];
  labelKey: string;
  valueKey: string;
  maxBars?: number;
  title: string;
}) {
  const { theme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const isDark = theme === 'dark';

  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 50);
    return () => clearTimeout(t);
  }, []);

  const sorted = [...(data || [])]
    .filter((r) => r[valueKey] != null && Number(r[valueKey]) > 0)
    .sort((a, b) => Number(b[valueKey]) - Number(a[valueKey]))
    .slice(0, maxBars);
  const max = Math.max(1, ...sorted.map((r) => Number(r[valueKey])));

  return (
    <div
      className="rounded-xl border overflow-hidden p-6 transition-colors duration-300"
      style={{
        background: isDark ? 'var(--bg-card)' : '#ffffff',
        borderColor: isDark ? '#475569' : '#e2e8f0',
      }}
    >
      <h4 className="text-sm font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h4>
      <div className="space-y-3">
        {sorted.length === 0 && (
          <p className="text-sm" style={{ color: 'var(--text-primary)' }}>No data yet.</p>
        )}
        {sorted.map((row, i) => {
          const value = Number(row[valueKey]);
          const pct = max ? (value / max) * 100 : 0;
          return (
            <div key={String(row[labelKey] || i)} className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="truncate pr-2" style={{ color: 'var(--text-primary)' }}>
                  {String(row[labelKey] || '—')}
                </span>
                <span className="font-medium tabular-nums shrink-0" style={{ color: 'var(--text-primary)' }}>
                  {value}
                </span>
              </div>
              <div
                className="h-6 rounded overflow-hidden"
                style={{
                  background: isDark ? '#334155' : '#f1f5f9',
                }}
              >
                <div
                  className="h-full rounded transition-all duration-500 ease-out flex items-center"
                  style={{
                    width: mounted ? `${pct}%` : '0%',
                    background: isDark ? 'linear-gradient(90deg, #3d5c82, #5b7fa6)' : 'linear-gradient(90deg, #3d5c82, #5b7fa6)',
                    transitionDelay: `${i * STAGGER_MS}ms`,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AnimatedEventTypeChart({ aggregates }: { aggregates: Aggregates | null }) {
  const data = (aggregates?.by_event_type || []).map((r) => ({ event_type: r.event_type, count: r.count }));
  return (
    <AnimatedBarChart
      data={data}
      labelKey="event_type"
      valueKey="count"
      maxBars={12}
      title="Events by type"
    />
  );
}

export function AnimatedResourceChart({ aggregates }: { aggregates: Aggregates | null }) {
  const data = (aggregates?.by_resource_type || [])
    .filter((r) => r.resource_type)
    .map((r) => ({ resource_type: r.resource_type, count: r.count }));
  return (
    <AnimatedBarChart
      data={data}
      labelKey="resource_type"
      valueKey="count"
      maxBars={12}
      title="By resource (page/section)"
    />
  );
}
