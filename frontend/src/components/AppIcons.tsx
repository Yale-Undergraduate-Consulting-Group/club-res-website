import type { ReactNode } from 'react';

/**
 * One line icon per app, drawn on a 24-unit grid in the kit's stroke style
 * (currentColor, 1.6 wide, round ends) so they take the text colour around them.
 */
function Glyph({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

/** Outreach: a letter on its way. */
export function OutreachIcon() {
  return (
    <Glyph>
      <rect x="3" y="5.5" width="18" height="13" />
      <path d="m3.5 6.5 8.5 6.5 8.5-6.5" />
    </Glyph>
  );
}

/** Survey builder: a clipboard of ticked questions. */
export function SurveyIcon() {
  return (
    <Glyph>
      <rect x="5" y="4.5" width="14" height="16.5" />
      <path d="M9 3h6v3H9z" />
      <path d="m8 11 1.2 1.2L11.5 10M13.5 11H16M8 16l1.2 1.2 2.3-2.2M13.5 16H16" />
    </Glyph>
  );
}

/** Market sentiment: a trend line over an axis. */
export function SentimentIcon() {
  return (
    <Glyph>
      <path d="M4 4v16h16" />
      <path d="m7 15 3.5-4 3 2.5L19 7" />
      <path d="M15.5 7H19v3.5" />
    </Glyph>
  );
}
