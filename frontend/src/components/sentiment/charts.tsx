import type { SentimentLabel, SentimentSummary } from '../../api';
import { signed } from './format';

export function Tiles({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="sentiment-tiles">
      {items.map((item) => (
        <div className="sentiment-tile" key={item.label}>
          <div className="sentiment-tile__label">{item.label}</div>
          <div className="sentiment-tile__value">{item.value}</div>
        </div>
      ))}
    </div>
  );
}

const LABELS: SentimentLabel[] = ['positive', 'neutral', 'negative'];

/** Positive, neutral and negative shares as one stacked bar with its counts in words. */
export function DistributionBar({ summary }: { summary: SentimentSummary }) {
  const total = summary.count;
  if (!total) return <p className="text-sm text-slate-500">Nothing scored.</p>;
  const description = LABELS.map((l) => `${summary.distribution[l]} ${l}`).join(', ');
  return (
    <div>
      <div className="sentiment-bar" role="img" aria-label={description}>
        {LABELS.map((l) => (
          <span key={l} className={`sentiment-bar__seg--${l}`} style={{ width: `${(summary.distribution[l] / total) * 100}%` }} />
        ))}
      </div>
      <div className="sentiment-legend">
        {LABELS.map((l) => (
          <span key={l}>
            <span className={`sentiment-legend__dot sentiment-bar__seg--${l}`} />
            {summary.distribution[l]} {l} ({Math.round((summary.distribution[l] / total) * 100)}%)
          </span>
        ))}
      </div>
    </div>
  );
}

export type ScoreRow = { label: string; value: number | null; note?: string };

/** Rows of scores in [-1, 1], drawn out from a centre line (right is positive). */
export function ScoreRows({ rows, empty }: { rows: ScoreRow[]; empty: string }) {
  if (!rows.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <div className="sentiment-rows">
      {rows.map((row) => {
        const value = row.value ?? 0;
        const width = Math.min(Math.abs(value), 1) * 50;
        return (
          <div className="sentiment-row" key={row.label}>
            <span className="sentiment-row__label" title={row.note ? `${row.label} (${row.note})` : row.label}>
              {row.label}{row.note ? <span className="text-slate-500"> · {row.note}</span> : null}
            </span>
            <div className="sentiment-row__track" role="img" aria-label={`${row.label}: ${signed(row.value)}`}>
              {row.value !== null && (
                <span
                  className={`sentiment-row__fill sentiment-row__fill--${value >= 0 ? 'positive' : 'negative'}`}
                  style={value >= 0 ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                />
              )}
            </div>
            <span className="sentiment-row__value">{signed(row.value)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Average score per period as a line between -1 and 1. */
export function TrendChart({ points, label }: { points: { label: string; value: number; count: number }[]; label: string }) {
  if (!points.length) return <p className="text-sm text-slate-500">No dated posts.</p>;
  const width = 560;
  const height = 180;
  const pad = { left: 34, right: 12, top: 12, bottom: 28 };
  const x = (i: number) => (points.length === 1 ? width / 2 : pad.left + (i / (points.length - 1)) * (width - pad.left - pad.right));
  const y = (v: number) => pad.top + ((1 - v) / 2) * (height - pad.top - pad.bottom);
  const path = points.map((p, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(p.value)}`).join(' ');
  const step = Math.ceil(points.length / 8);
  return (
    <svg className="sentiment-trend" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      {[1, 0, -1].map((v) => (
        <g key={v}>
          <line className={v === 0 ? 'sentiment-trend__zero' : 'sentiment-trend__grid'} x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} />
          <text x={pad.left - 6} y={y(v) + 3} textAnchor="end">{v}</text>
        </g>
      ))}
      <path className="sentiment-trend__line" d={path} />
      {points.map((p, i) => (
        <g key={p.label}>
          <circle className="sentiment-trend__dot" cx={x(i)} cy={y(p.value)} r={3}>
            <title>{`${p.label}: ${signed(p.value)} across ${p.count} posts`}</title>
          </circle>
          {i % step === 0 && <text x={x(i)} y={height - 8} textAnchor="middle">{p.label}</text>}
        </g>
      ))}
    </svg>
  );
}
