import type { CompanyLines } from '../lib/useCompanyLines';
import { Button } from './ui/Primitives';

/** Each company's line for the club's Intro: suggest, regenerate or write it,
 *  then confirm it. The Send panel and the Queue panel both show this; state
 *  and gating live in useCompanyLines. Renders nothing when the message has no
 *  {personalisation} or there is nobody to write to. */
export default function CompanyLinesField({ state, disabled }: { state: CompanyLines; disabled: boolean }) {
  const { active, companies, lines, setLine, suggest, suggestAll, unwritten, suggesting } = state;
  if (!active || companies.length === 0) return null;
  return (
    <fieldset className="space-y-2 border border-pale-sky p-3" data-testid="company-lines">
      <legend className="px-1 text-xs font-semibold text-deep-navy">A line for each company</legend>
      <p className="text-xs text-slate-600">
        Follows “I'd love to work with them on a project in the new year.” Suggest one or write your own,
        then confirm it. Confirm an empty line to send without one.
      </p>
      {unwritten > 0 && (
        <Button size="sm" variant="secondary" disabled={disabled || suggesting} onClick={() => void suggestAll()}
                data-testid="suggest-lines">
          {unwritten === 1 ? 'Suggest a line' : `Suggest lines for ${unwritten} companies`}
        </Button>
      )}
      <ul className="space-y-3">
        {companies.map((c) => {
          const line = lines[c.key] ?? { text: '', confirmed: false };
          return (
            <li key={c.key} data-testid="company-line" data-company={c.name} className="space-y-1">
              <label className="block text-xs font-medium text-slate-600">
                {c.name}
                <textarea className="ui-input mt-1 block w-full" rows={2} value={line.text} aria-label={`Line for ${c.name}`}
                          disabled={disabled || line.suggesting}
                          placeholder={line.suggesting ? 'Suggesting…' : 'One sentence about this company'}
                          onChange={(e) => setLine(c.key, { text: e.target.value, confirmed: false, sources: undefined })} />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <Button size="sm" variant="ghost" disabled={disabled || line.suggesting} onClick={() => void suggest(c)}>
                  {line.suggesting ? 'Suggesting…' : line.text.trim() ? 'Regenerate' : 'Suggest'}
                </Button>
                <label className="flex items-center gap-1.5 text-xs text-slate-700">
                  <input type="checkbox" checked={line.confirmed} disabled={disabled || line.suggesting}
                         aria-label={`Confirm the line for ${c.name}`}
                         onChange={(e) => setLine(c.key, { confirmed: e.target.checked })} />
                  {line.text.trim() ? 'Confirmed' : 'Confirmed: no line'}
                </label>
              </div>
              {line.sources && line.sources.length > 0 && (
                <p className="text-xs text-slate-500">From {line.sources.join(', ')}.</p>
              )}
              {line.error && <p className="text-xs text-[var(--error)]" role="alert">{line.error}</p>}
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}

/** Why sending is still closed: how many companies' lines are confirmed. */
export function LinesPending({ state }: { state: CompanyLines }) {
  if (state.pending === 0) return null;
  const total = state.companies.length;
  return (
    <p className="text-xs text-slate-500" data-testid="lines-pending">
      Confirm each company's line to send: {total - state.pending} of {total} confirmed.
    </p>
  );
}