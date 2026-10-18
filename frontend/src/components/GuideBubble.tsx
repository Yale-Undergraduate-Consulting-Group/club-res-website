import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { TOUR_EVENT } from './Tour';

/**
 * What this page is for, and what to do next.
 *
 * This replaced the assistant chatbot. The assistant existed to translate a
 * sentence into a form fill, which mattered when the work was spread over six
 * pages and nobody could find the right one. Now that choosing companies,
 * finding people, writing and building are four numbered steps in one place,
 * a member who can read the page does not need a model to drive it - and a
 * model that can only drive it is slower than doing it.
 *
 * So the help is static, page-specific, and costs nothing per open.
 */

type Guide = { title: string; steps: string[]; next?: { to: string; label: string } };

const GUIDES: { match: (path: string) => boolean; guide: Guide }[] = [
  {
    match: (p) => p.startsWith('/scraper'),
    guide: {
      title: 'Find contacts',
      steps: [
        'Companies is the index: 215,000 companies from public registers. Tick any, then "Find people and write to them".',
        'Find people runs three steps in order — choose companies, choose who gets it, then hand them to Drafts to write to each.',
        'Step 2 is where people come from: search again, import a spreadsheet, or queue deep research.',
        'Board seats and the very senior are never ticked for you: they rarely answer a cold email.',
        'Drafts writes each person their own email. Nothing leaves until you release the campaign.',
      ],
      next: { to: '/', label: 'Home' },
    },
  },
  {
    match: (p) => p.startsWith('/campaigns'),
    guide: {
      title: 'Campaigns',
      steps: [
        'Every campaign is a draft until you release it. Releasing is the only thing that sends.',
        'The first email to a company you have never mailed goes alone, to prove the address works before the rest follow.',
        'A bounce stops that company and asks you to set its email format.',
      ],
      next: { to: '/outreach?view=followups', label: 'Follow-ups' },
    },
  },
  {
    match: (p) => p.startsWith('/outreach'),
    guide: {
      title: 'Pipeline',
      steps: [
        'Pipeline shows people by stage, and grouped by company it shows what the send ledger recorded: mailed, replied, bounced.',
        'Follow-ups lists what is queued to go out and what stopped, with the reason.',
        'A reply stops that person’s sequence automatically.',
      ],
    },
  },
  {
    match: (p) => p.startsWith('/studio'),
    guide: {
      title: 'Drafts',
      steps: [
        'Studio writes one bespoke email to one named person.',
        'To write to several people, tick them and use "Write to each of these N": each gets an advisory draft of their own.',
      ],
      next: { to: '/scraper?view=company', label: 'Find people' },
    },
  },
];

const DEFAULT_GUIDE: Guide = {
  title: 'YUCG Outreach',
  steps: [
    'Find contacts is where work starts: choose companies, find people, write, build.',
    'Campaigns is where you review and release. Nothing sends without that.',
    'Pipeline is what happened afterwards — replies, bounces, follow-ups.',
  ],
  next: { to: '/scraper', label: 'Find contacts' },
};

export default function GuideBubble() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const guide = GUIDES.find((g) => g.match(pathname))?.guide ?? DEFAULT_GUIDE;

  // Closing on Escape is expected of anything that covers the page.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="How this page works"
        className="fixed bottom-5 right-5 z-40 grid h-12 w-12 place-items-center rounded-full bg-deep-navy text-lg font-semibold text-white shadow-lg hover:opacity-90"
      >
        ?
      </button>

      {open && (
        <div
          role="dialog"
          aria-label={`How ${guide.title} works`}
          className="fixed bottom-20 right-5 z-40 w-[22rem] max-w-[calc(100vw-2.5rem)] rounded-2xl border border-[var(--border)] bg-white p-4 shadow-xl"
        >
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-[15px] font-semibold text-deep-navy">{guide.title}</h2>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="text-slate-500 hover:text-deep-navy"
            >
              ✕
            </button>
          </div>
          <ol className="mt-2 space-y-2 text-[13px] leading-5 text-slate-700">
            {guide.steps.map((step, index) => (
              <li key={step} className="flex gap-2">
                <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-pale-sky text-[10px] font-semibold text-deep-navy">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => { setOpen(false); window.dispatchEvent(new Event(TOUR_EVENT)); }}
            className="ui-button ui-button--secondary ui-button--sm"
          >
            Take the tour
          </button>
          {guide.next && (
            <Link
              to={guide.next.to}
              onClick={() => setOpen(false)}
              className="text-[13px] font-semibold text-steel-blue hover:underline"
            >
              Next: {guide.next.label} →
            </Link>
          )}
          </div>
        </div>
      )}
    </>
  );
}
