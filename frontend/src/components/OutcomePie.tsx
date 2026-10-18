import { useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Where outreach ended up, as a chart you can interrogate.
 *
 * Interactive rather than a picture: hovering or focusing a slice pulls it
 * out and names the number, and activating it opens the list of those exact
 * people. A chart that cannot be clicked makes a member read a percentage and
 * then go hunting for the rows behind it.
 *
 * Drawn as inline SVG rather than a charting library: six slices do not
 * justify the bundle weight, and the same markup is keyboard reachable and
 * screen-reader labelled, which most chart libraries make harder, not easier.
 */

export type Slice = {
  label: string;
  value: number;
  /** A CSS colour, normally one of the --chart-* club tokens. */
  colour: string;
  /** Where the rows behind this slice live. */
  to?: string;
};

function arcPath(cx: number, cy: number, r: number, from: number, to: number): string {
  const point = (angle: number) => [
    cx + r * Math.cos(angle - Math.PI / 2),
    cy + r * Math.sin(angle - Math.PI / 2),
  ];
  const [x1, y1] = point(from);
  const [x2, y2] = point(to);
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${to - from > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`;
}

export default function OutcomePie({ title, slices, empty, size = 116, showTitle = true }: {
  title: string;
  slices: Slice[];
  empty: string;
  size?: number;
  /** Off when the surrounding card already carries this heading. */
  showTitle?: boolean;
}) {
  const navigate = useNavigate();
  const titleId = useId();
  const [active, setActive] = useState<string | null>(null);

  const shown = slices.filter((s) => s.value > 0);
  const total = shown.reduce((sum, s) => sum + s.value, 0);
  const centre = size / 2;
  const radius = centre - 6;

  // Geometry once, so the wedge, its hit area and the legend all agree.
  // Reduced rather than accumulated into a mutable cursor: the compiler
  // rejects writing to an outer binding during render.
  const wedges = shown.reduce<{ slice: Slice; from: number; to: number }[]>((acc, slice) => {
    const from = acc.length ? acc[acc.length - 1].to : 0;
    acc.push({ slice, from, to: from + (slice.value / total) * Math.PI * 2 });
    return acc;
  }, []);

  const open = (slice: Slice) => { if (slice.to) navigate(slice.to); };
  const activeSlice = shown.find((s) => s.label === active);

  return (
    <figure className="m-0 flex items-center gap-4">
      {total === 0 ? (
        <div
          className="grid shrink-0 place-items-center rounded-full border-2 border-dashed text-[11px] text-slate-400"
          style={{ width: size, height: size, borderColor: 'var(--chart-idle)' }}
        >
          none yet
        </div>
      ) : (
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-labelledby={titleId}
          className="shrink-0 overflow-visible"
          onMouseLeave={() => setActive(null)}
        >
          <title id={titleId}>
            {`${title}: ${shown.map((s) => `${s.value} ${s.label}`).join(', ')}`}
          </title>
          {wedges.map(({ slice, from, to }) => {
            const on = active === slice.label;
            const whole = shown.length === 1;
            // Deliberately no motion on hover: a wedge that slides out from
            // under the cursor triggers mouseleave, slides back, and flickers.
            // Dimming the others marks the active slice and stays still.
            return (
              <g
                key={slice.label}
                onMouseEnter={() => setActive(slice.label)}
                onFocus={() => setActive(slice.label)}
                onBlur={() => setActive(null)}
                onClick={() => open(slice)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(slice); }
                }}
                tabIndex={slice.to ? 0 : -1}
                role={slice.to ? 'link' : undefined}
                aria-label={slice.to ? `${slice.value} ${slice.label} — open the list` : undefined}
                className={slice.to ? 'cursor-pointer focus:outline-none' : undefined}
              >
                {whole ? (
                  <circle
                    cx={centre} cy={centre} r={radius} fill={slice.colour}
                    stroke={on ? 'var(--ink)' : 'var(--surface)'}
                    strokeWidth={on ? 2 : 1}
                    opacity={active && !on ? 0.45 : 1}
                    style={{ transition: 'opacity 120ms ease-out' }}
                  />
                ) : (
                  <path
                    d={arcPath(centre, centre, radius, from, to)}
                    fill={slice.colour}
                    stroke={on ? 'var(--ink)' : 'var(--surface)'}
                    strokeWidth={on ? 2 : 1}
                    opacity={active && !on ? 0.45 : 1}
                    style={{ transition: 'opacity 120ms ease-out' }}
                  />
                )}
              </g>
            );
          })}
        </svg>
      )}

      <figcaption className="min-w-0">
        {showTitle && <p className="text-[13px] font-semibold text-deep-navy">{title}</p>}
        {total === 0 ? (
          <p className="mt-1 text-xs text-slate-500">{empty}</p>
        ) : (
          <>
            <ul className="mt-1 space-y-0.5">
              {shown.map((slice) => {
                const on = active === slice.label;
                return (
                  <li key={slice.label}>
                    <button
                      type="button"
                      disabled={!slice.to}
                      onMouseEnter={() => setActive(slice.label)}
                      onMouseLeave={() => setActive(null)}
                      onFocus={() => setActive(slice.label)}
                      onBlur={() => setActive(null)}
                      onClick={() => open(slice)}
                      className={`flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs ${
                        on ? 'bg-pale-sky/40' : ''} ${slice.to ? 'hover:bg-pale-sky/40' : 'cursor-default'}`}
                    >
                      <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: slice.colour }} />
                      <span className="font-semibold text-deep-navy">{slice.value}</span>
                      <span className="text-slate-700">{slice.label}</span>
                      <span className="ml-auto text-slate-400">
                        {Math.round((slice.value / total) * 100)}%
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <p className="mt-1 h-4 text-[11px] text-slate-500" aria-live="polite">
              {activeSlice
                ? `${activeSlice.value} of ${total}${activeSlice.to ? ' — click to open' : ''}`
                : `${total} people`}
            </p>
          </>
        )}
      </figcaption>
    </figure>
  );
}
