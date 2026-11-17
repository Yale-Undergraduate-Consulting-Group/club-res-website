import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { TOUR_EVENT } from './Tour';
import { Button } from './ui/Primitives';

/**
 * What this page is for, and what to do next.
 *
 * This replaced the assistant chatbot. The assistant existed to translate a
 * sentence into a form fill, which mattered when the work was spread over six
 * pages and nobody could find the right one. Now that choosing companies,
 * finding people, writing and sending are one screen, a member who can read
 * the page does not need a model to drive it - and a model that can only
 * drive it is slower than doing it.
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
        'Companies is the index: about 200,000 companies from public records, one entry each. Tick any, then "Find people and write to them".',
        'Find people is one screen: add companies by name, by pasting a list, by importing a spreadsheet of them, or from the index, then press Search.',
        'Everyone found is on the list at once. The number at the top is how many can be emailed; beside it are the reasons for everyone left out.',
        'Board seats are ticked only if you choose Board under "Who to look for", and the very senior are never ticked for you: they rarely answer a cold email.',
        'Every email is the club\'s Intro. Confirm one line for each company (suggested for you, or your own), then "Send to N". Nothing leaves until you confirm.',
      ],
      next: { to: '/campaigns', label: 'Overview' },
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
        'A contact with no saved draft opens written from the club template; confirm their company\'s line, then edit by hand if you like.',
        'To write to several people, tick them and use "Write to each of these N": open them one by one, or queue them all with the club template.',
      ],
      next: { to: '/scraper?view=company', label: 'Find people' },
    },
  },
];

const DEFAULT_GUIDE: Guide = {
  title: 'YUCG Resources',
  steps: [
    'Home lists the tools you can use. Open one from its card or from the menu above.',
    'Outreach is where work starts: Find contacts, write each person an email in Drafts, then follow replies in Pipeline.',
    'Nothing is emailed until you release a campaign.',
  ],
  next: { to: '/campaigns', label: 'Outreach overview' },
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
      <Button
        iconOnly
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="How this page works"
        size="sm"
        variant="secondary"
      >
        ?
      </Button>

      {open && (
        <div
          role="dialog"
          aria-label={`How ${guide.title} works`}
          className="fixed top-16 right-5 z-[60] w-[22rem] max-w-[calc(100vw-2.5rem)] border border-[var(--border)] bg-[var(--surface)] p-4"
        >
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-[15px] font-semibold text-deep-navy">{guide.title}</h2>
            <Button variant="ghost" size="sm" iconOnly onClick={() => setOpen(false)} aria-label="Close">
              ✕
            </Button>
          </div>
          <ol className="mt-2 space-y-2 text-[13px] leading-5 text-slate-700">
            {guide.steps.map((step, index) => (
              <li key={step} className="flex gap-2">
                <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center bg-pale-sky text-[10px] font-semibold text-deep-navy">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setOpen(false); window.dispatchEvent(new Event(TOUR_EVENT)); }}
          >
            Take the tour
          </Button>
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
