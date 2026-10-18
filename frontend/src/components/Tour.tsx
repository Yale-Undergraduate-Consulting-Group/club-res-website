import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/**
 * A walk through the whole flow, on the real pages, in the order the work is
 * done: companies, people, one email each, then what happened.
 *
 * The "?" guide explains the page you are on. It could not show that the
 * pages are one pipeline, which is the thing a new member does not know. The
 * tour moves between them, dims everything but the part being explained, and
 * says what to do there. Started from the guide, never on its own: an overlay
 * that appears unasked is in the way of a member who already knows the app.
 */

type Stop = {
  /** Where the stop lives. The tour navigates there if it is not already. */
  route: string;
  /** What to light up. Missing on the page (no companies chosen yet) means
   *  the card shows centred instead. */
  target?: string;
  title: string;
  body: string;
};

export const TOUR_EVENT = 'yucg:tour';

const STOPS: Stop[] = [
  {
    route: '/',
    title: 'How YUCG Outreach works',
    body: 'Four stops, in the order the work happens: pick companies, choose the people there, write each of them their own email, then follow what came back. Nothing is sent to anyone until you release a campaign.',
  },
  {
    route: '/scraper?view=register',
    target: '[data-section="company-register"]',
    title: '1 · Companies',
    body: 'The public register: about 215,000 companies with their sector and filings. Tick the ones worth approaching and press "Find people and write to them".',
  },
  {
    route: '/scraper?view=company',
    target: '[data-step="1"]',
    title: '2 · Choose companies',
    body: 'Or type any company here. Pick it from the list rather than typing the whole name: the list carries the company\'s website, and a search with a website finds far more people.',
  },
  {
    route: '/scraper?view=company',
    target: '[data-step="2"]',
    title: '3 · Choose who gets it',
    body: 'Everyone on file at those companies, grouped by company. Working-level people are ticked for you. Board seats and the very senior - CEO, CFO, President, senior VPs - are not: they rarely answer a student\'s cold email. Nor are rows whose name is page text rather than a person. Tick any of them yourself if you mean to.',
  },
  {
    route: '/scraper?view=company',
    target: '[data-testid="company-lanes"]',
    title: 'Where each company stands',
    body: 'One lane per company: people found, people ticked, all merging into one campaign. "+ Find more people" searches that company again.',
  },
  {
    route: '/scraper?view=company',
    target: '[data-step="3"]',
    title: '4 · Write to each of them',
    body: 'The people you ticked go to Drafts together, and each gets an email of their own - not one template with their name swapped in.',
  },
  {
    route: '/studio',
    target: '.email-studio-contacts',
    title: 'Drafts',
    body: 'Tick people here and press "Write to each of these", or arrive from Find people. "Draft an advisory email for each" writes every person a note proposing two or three projects a YUCG team could build for their company.',
  },
  {
    route: '/studio',
    target: '#email-editor-section',
    title: 'Read, edit, test',
    body: 'Open anyone to read and change their draft. "Send test" mails it to you first, so you see exactly what they would. Then save the drafts as a campaign.',
  },
  {
    route: '/outreach',
    title: 'Pipeline',
    body: 'After a campaign is released: who replied, who bounced, and which follow-ups are queued. A reply stops that person\'s follow-ups on its own.',
  },
  {
    route: '/',
    title: 'That is the whole loop',
    body: 'Companies, people, one email each, then the pipeline. The "?" button explains whichever page you are on, and can start this tour again.',
  },
];

const CARD_W = 352;
const GAP = 12;

function sameRoute(route: string, pathname: string, search: string): boolean {
  const url = new URL(route, window.location.origin);
  if (url.pathname !== pathname) return false;
  const want = url.searchParams.get('view');
  return !want || new URLSearchParams(search).get('view') === want;
}

export default function Tour() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [index, setIndex] = useState<number | null>(null);
  // The target's box, tagged with the stop it belongs to, so a new stop
  // starts unlit without resetting state inside an effect.
  const [lit_, setLit] = useState<{ stop: number; rect: DOMRect } | null>(null);
  const [cardH, setCardH] = useState(200);
  const cardRef = useRef<HTMLDivElement>(null);
  const stop = index === null ? null : STOPS[index];
  const rect = lit_ && lit_.stop === index ? lit_.rect : null;

  useEffect(() => {
    const start = () => setIndex(0);
    window.addEventListener(TOUR_EVENT, start);
    return () => window.removeEventListener(TOUR_EVENT, start);
  }, []);

  // Go to the stop's page, then find its target once the page has drawn it.
  useEffect(() => {
    if (!stop) return;
    if (!sameRoute(stop.route, pathname, search)) {
      navigate(stop.route);
      return;
    }
    if (!stop.target) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      const el = document.querySelector(stop.target!);
      if (el || ++tries > 30) {
        window.clearInterval(timer);
        if (el && index !== null) {
          el.scrollIntoView({ block: 'nearest', behavior: 'instant' as ScrollBehavior });
          setLit({ stop: index, rect: el.getBoundingClientRect() });
        }
      }
    }, 100);
    return () => window.clearInterval(timer);
  }, [stop, index, pathname, search, navigate]);

  // Keep the highlight on the target as the page scrolls or resizes.
  useEffect(() => {
    if (!stop?.target || index === null) return;
    const update = () => {
      const el = document.querySelector(stop.target!);
      setLit(el ? { stop: index, rect: el.getBoundingClientRect() } : null);
    };
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [stop, index]);

  const close = useCallback(() => setIndex(null), []);
  const next = useCallback(() => setIndex((i) => (i === null || i >= STOPS.length - 1 ? null : i + 1)), []);
  const back = useCallback(() => setIndex((i) => (i === null || i === 0 ? i : i - 1)), []);

  useEffect(() => {
    if (index === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
      else if (event.key === 'ArrowRight') next();
      else if (event.key === 'ArrowLeft') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, close, next, back]);

  useLayoutEffect(() => { cardRef.current?.focus(); }, [index]);

  // The card's height decides whether it fits above or below the target.
  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    const observer = new ResizeObserver(() => setCardH(card.offsetHeight));
    observer.observe(card);
    return () => observer.disconnect();
  }, [index]);

  if (!stop || index === null) return null;

  // The highlight is the target clipped to the screen; the card goes below
  // it, else above it, else - a target taller than the screen - at the foot.
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const narrow = vw < 640;
  const lit = rect && {
    top: Math.max(8, rect.top - 6),
    left: Math.max(8, rect.left - 6),
    bottom: Math.min(vh - 8, rect.bottom + 6),
    right: Math.min(vw - 8, rect.right + 6),
  };
  let cardStyle: React.CSSProperties;
  if (narrow || !lit) {
    cardStyle = narrow
      ? { left: 12, right: 12, bottom: 12 }
      : { left: (vw - CARD_W) / 2, top: Math.max(24, (vh - cardH) / 2), width: CARD_W };
  } else {
    const left = Math.min(Math.max(12, lit.left), vw - CARD_W - 12);
    if (vh - lit.bottom >= cardH + GAP * 2) cardStyle = { left, top: lit.bottom + GAP, width: CARD_W };
    else if (lit.top >= cardH + GAP * 2) cardStyle = { left, top: lit.top - GAP - cardH, width: CARD_W };
    else cardStyle = { left: vw - CARD_W - 24, bottom: 24, width: CARD_W };
  }

  return (
    <div className="fixed inset-0 z-[60]" data-testid="tour">
      {/* Clicks on the page are held while the tour is open, so a stray click
          cannot navigate away from the stop being explained or end the tour. */}
      <div className="absolute inset-0" aria-hidden="true"
           style={lit ? undefined : { background: 'rgba(15, 23, 42, 0.55)' }} />
      {lit && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute rounded-xl ring-2 ring-white transition-all duration-200"
          style={{
            top: lit.top, left: lit.left, width: lit.right - lit.left, height: lit.bottom - lit.top,
            boxShadow: '0 0 0 9999px rgba(15, 23, 42, 0.55)',
          }}
        />
      )}
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        tabIndex={-1}
        className="absolute rounded-2xl bg-white p-4 shadow-2xl outline-none"
        style={cardStyle}
      >
        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          {index + 1} of {STOPS.length}
        </p>
        <h2 id="tour-title" className="mt-0.5 text-[15px] font-semibold text-deep-navy">{stop.title}</h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-700">{stop.body}</p>
        <div className="mt-3 flex items-center gap-2">
          <button type="button" onClick={close} className="text-xs text-slate-500 underline">
            {index === STOPS.length - 1 ? 'Close' : 'Skip the tour'}
          </button>
          <span className="flex-1" />
          {index > 0 && (
            <button type="button" onClick={back} className="ui-button ui-button--ghost ui-button--sm">Back</button>
          )}
          <button type="button" onClick={next} className="ui-button ui-button--primary ui-button--sm">
            {index === STOPS.length - 1 ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
