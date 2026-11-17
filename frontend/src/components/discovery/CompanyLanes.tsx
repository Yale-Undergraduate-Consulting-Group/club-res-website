import { useState } from 'react';
import type { CompanyStatus } from '../../lib/peopleSheet';
import type { CompanyRun } from '../../lib/useDiscoveryRuns';

/**
 * Company progress, drawn the way a commit graph draws branches.
 *
 * A campaign to twelve companies is twelve pieces of work that merge into one
 * message. A list cannot show that: it says which companies were picked but
 * not which of them actually has people, which has anybody ticked, or which is
 * still empty and holding the campaign up. The lanes do - one per company,
 * each node a step it has or has not reached, all of them curving into the one
 * trunk that is the campaign.
 *
 * The lanes report each company's search, never start one: a lane with a
 * finished search offers its export and deletion. They stay on screen at
 * every step, above the sheet of people where the search lives, and are kept
 * short so the sheet is never pushed below the fold - beyond six companies
 * the rest fold behind a count.
 */

export type Lane = {
  company: string;
  /** Everyone listed for the company. */
  found: number;
  /** How many can be emailed there: the page's one number, for this company. */
  ready: number;
  run: CompanyRun;
  /** What this company's search amounts to, in the words the sheet uses. */
  status: CompanyStatus;
};

/** Lane colours, in the order a commit graph would assign them: the chart palette, so they follow the theme. */
const COLOURS = [1, 2, 3, 4, 5, 6, 7].map((n) => `var(--chart-${n})`);
/** The campaign trunk and the empty step circles, drawn in the page's ink and surface. */
const TRUNK = { stroke: 'var(--ink)' };
const HOLLOW = 'var(--surface)';

const ROW = 38;
/** Rows drawn before the rest fold behind a count. */
const WINDOW = 6;
const TRUNK_X = 10;
const COL_X = [40, 70, 100];
const GRAPH_W = 112;

export default function CompanyLanes({ lanes, emailed, focused, onFocus, onExport, onDelete }: {
  lanes: Lane[];
  /** The same number across every company: what Send writes to. */
  emailed: number;
  /** The lane the sheet's actions are about. */
  focused: string | null;
  onFocus: (company: string) => void;
  onExport: (runId: number) => void;
  onDelete: (runId: number) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  if (lanes.length === 0) return null;
  const shown = showAll ? lanes : lanes.slice(0, WINDOW);
  const hidden = lanes.length - shown.length;

  return (
    <section className="surface-card surface-card--compact" aria-label="Company progress"
             data-testid="company-lanes">
      {/* Sticky against the surface, the same way the sheet's own header
          pins: scroll past this panel and its title stays put until the
          panel itself has scrolled by, then the sheet's header takes over. */}
      <div className="sticky top-0 z-10 -mx-4 border-b border-pale-sky bg-[var(--surface)]/95 px-4 py-2 backdrop-blur"
           data-testid="lanes-header">
        <div className="flex items-baseline justify-between">
          <h2 className="text-[15px] font-semibold text-deep-navy">Company progress</h2>
          <span className="text-xs text-slate-500" data-testid="lanes-total">
            {lanes.length} compan{lanes.length === 1 ? 'y' : 'ies'} · {emailed} can be emailed
          </span>
        </div>
      </div>

      <ul className="m-0 list-none p-0">
        {shown.map((lane, index) => {
          const colour = COLOURS[index % COLOURS.length];
          const reached = [true, lane.found > 0, lane.ready > 0];
          const lastX = COL_X[reached.lastIndexOf(true)];
          const active = lane.run.state === 'searching' || lane.run.state === 'queued';
          const runId = active ? undefined : lane.run.runId;
          const isFocused = focused !== null && focused === lane.company;
          return (
            <li
              key={lane.company}
              data-lane={lane.company}
              data-state={lane.run.state}
              data-focused={isFocused || undefined}
              // Focus follows a click or the keyboard, never the pointer.
              // Focusing a lane re-renders the rail and can raise or drop the
              // domain warning above this panel, so a hover that focused
              // shifted the lanes under a still or scrolling pointer: the next
              // lane was entered, focused, and the panel jumped again, with
              // the hover and focus highlights on two different rows.
              onFocus={() => onFocus(lane.company)}
              onClick={() => onFocus(lane.company)}
              // The graph (112px) plus the two fixed alignment columns
              // (176px + 144px) alone is wider than a phone screen, before
              // the company name gets any room at all. Forcing that onto one
              // frozen-height row on mobile did not make it fit - it made
              // the row overflow silently, and the sibling below sat over
              // wherever the overflow landed, so a tap could land on a
              // completely different lane's button. Below `sm` the row wraps
              // to two lines and grows to fit instead.
              className={`group flex cursor-pointer flex-wrap sm:flex-nowrap sm:h-[38px] items-center gap-x-2 gap-y-1 px-1 py-1.5 sm:py-0 -mx-1 ${
                isFocused ? 'bg-pale-sky/40' : 'hover:bg-pale-sky/15'}`}
            >
              {/* Purely decorative - the commit-graph metaphor is a desktop
                  flourish with room to spare. A phone does not have that room. */}
              <svg width={GRAPH_W} height={ROW} viewBox={`0 0 ${GRAPH_W} ${ROW}`} aria-hidden="true" className="hidden sm:block shrink-0">
                {/* The campaign itself: every lane leaves it and returns to it. */}
                <line x1={TRUNK_X} y1={0} x2={TRUNK_X} y2={ROW} style={TRUNK} strokeWidth={2} />
                <path
                  d={`M ${TRUNK_X} ${ROW / 2 - 10} C ${TRUNK_X} ${ROW / 2}, ${TRUNK_X + 8} ${ROW / 2}, ${TRUNK_X + 18} ${ROW / 2} L ${lastX} ${ROW / 2}`}
                  fill="none" style={{ stroke: colour }} strokeWidth={2}
                  strokeDasharray={lane.found > 0 ? undefined : '4 4'}
                />
                {lane.ready > 0 && (
                  <path
                    d={`M ${lastX} ${ROW / 2} C ${lastX - 24} ${ROW / 2 + 10}, ${TRUNK_X} ${ROW / 2 + 12}, ${TRUNK_X} ${ROW}`}
                    fill="none" style={{ stroke: colour }} strokeWidth={1.5} opacity={0.45}
                  />
                )}
                {COL_X.map((x, step) => (
                  <circle
                    key={step} cx={x} cy={ROW / 2} r={step === 0 ? 4 : 5}
                    style={{ fill: reached[step] ? colour : HOLLOW, stroke: colour }} strokeWidth={2}
                    opacity={reached[step] || step <= 1 ? 1 : 0.5}
                  />
                ))}
              </svg>
              <span className="min-w-0 flex-1 basis-full sm:basis-auto truncate text-[13px] font-semibold text-deep-navy" title={lane.company}>
                {lane.company}
              </span>
              {/* Fixed widths, not natural ones: "Not searched yet" and "23 people
                  found · search paused · 23 ticked" are wildly different lengths, and
                  a natural-width column shifts left or right with every row's
                  own text. A fixed box keeps the column's left edge - and the
                  actions after it - in the same place on every row. */}
              <span className={`shrink-0 sm:w-60 truncate text-xs tabular-nums ${
                lane.status.tone === 'paused' ? 'text-deep-navy' : active ? 'text-deep-navy' : 'text-slate-500'}`}
                    title={lane.status.problem || (active && lane.run.message ? lane.run.message : lane.status.long)}
                    data-testid="lane-state">
                {lane.status.text}
              </span>
              {/* The lane reports; it does not search. The one search action
                  is in the sheet under it, so a company never offers two. The
                  export and deletion menu stays here, hidden until hover or
                  keyboard focus on a real pointer device (touch has no hover,
                  so below `sm` it is always visible). Fixed width and
                  right-aligned so the menu appearing never moves the status. */}
              <span className="flex shrink-0 sm:w-10 items-center sm:justify-end gap-1">
                {runId !== undefined && (
                  <details className="relative sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100 sm:focus-within:opacity-100 open:opacity-100"
                           onClick={(e) => e.stopPropagation()}>
                    <summary
                      className="ui-button ui-button--ghost ui-button--sm ui-button--icon list-none [&::-webkit-details-marker]:hidden"
                      aria-label={`More for ${lane.company}`}
                    >
                      ⋯
                    </summary>
                    <div className="absolute right-0 z-20 mt-1 w-40 border border-pale-sky bg-[var(--surface)] p-1">
                      <button
                        type="button"
                        onClick={() => onExport(runId)}
                        className="block w-full px-2 py-1 text-left text-xs text-deep-navy hover:bg-pale-sky/40"
                      >
                        Export .xlsx
                      </button>
                      {/* The catalog builder's run belongs to the club: anyone
                          may export or keep from it, nobody deletes it. */}
                      {!lane.run.shared && (
                        <button
                          type="button"
                          onClick={() => onDelete(runId)}
                          className="block w-full px-2 py-1 text-left text-xs text-deep-navy hover:bg-pale-sky/60"
                        >
                          Delete run
                        </button>
                      )}
                    </div>
                  </details>
                )}
              </span>
            </li>
          );
        })}
        {/* The merge node and the fold share a row: the panel's height is
            the sheet's headroom, so nothing here takes a row of its own
            that does not need one. A dashed trunk says lanes are hidden. */}
        <li className="flex items-center gap-2" style={{ height: ROW }}>
          <svg width={GRAPH_W} height={ROW} aria-hidden="true" className="shrink-0">
            <line x1={TRUNK_X} y1={0} x2={TRUNK_X} y2={ROW / 2} style={TRUNK} strokeWidth={2}
                  strokeDasharray={hidden > 0 ? '2 3' : undefined} />
            <circle cx={TRUNK_X} cy={ROW / 2} r={6} style={{ ...TRUNK, fill: HOLLOW }} strokeWidth={2} />
          </svg>
          <span className="text-[13px] font-semibold text-deep-navy">One campaign</span>
          {(hidden > 0 || showAll) && (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="ml-2 text-xs font-medium text-deep-navy underline"
            >
              {showAll ? 'Show fewer' : `${hidden} more`}
            </button>
          )}
        </li>
      </ul>
    </section>
  );
}
