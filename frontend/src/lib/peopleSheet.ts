import { publicSourceUrl } from './contactEvidence';
import { companyKey, isTooSenior, levelOf, looksLikePerson, yaleOf, type Level } from './recipients';
import type { CompanyRun, FoundPerson } from './useDiscoveryRuns';

/**
 * What the people sheet and the company lanes say about people, decided once:
 * who matches the level the member chose, why, where each person came from,
 * which rows are the same person, and what each company's search amounts to.
 */

/** The three things "Who to look for" can be. */
export type Wanted = Exclude<Level, 'unknown'>;

export const WANTED_LABEL: Record<Wanted, string> = { working: 'Team leads', executive: 'Senior leaders', board: 'Board' };
/** Where to suggest looking next when a level came up empty. */
const WANTED_NEXT: Record<Wanted, Wanted> = { working: 'executive', executive: 'working', board: 'working' };
const SINGULAR: Record<Level, string> = {
  working: 'team lead', executive: 'senior leader', board: 'board seat', unknown: 'role unclear',
};

/** What the sheet needs to know about someone, whether they are on file (a
 *  Contact) or only found (a prospect, through foundAsPerson). */
export type PersonLike = {
  name?: string | null;
  title?: string | null;
  company?: string | null;
  email?: string | null;
  person_level?: unknown;
  person_function?: unknown;
  yale_signal?: unknown;
  contact_source?: string | null;
  linkedin_url?: string | null;
  source_url?: string | null;
};

export function foundAsPerson(p: FoundPerson): PersonLike {
  return {
    name: [p.first_name, p.last_name].filter(Boolean).join(' '),
    title: p.title,
    company: p.company,
    email: p.email,
    person_level: p.person_level,
    person_function: p.person_function,
    yale_signal: p.yale_signal,
    contact_source: p.contact_source,
    linkedin_url: p.linkedin_url,
    source_url: p.contact_profile_url,
  };
}

// --- Level ---------------------------------------------------------------

/** The level a person counts as for "who to look for". A filing lists a CFO as
 *  "SVP, Chief Financial Officer", which the server reads as working level; the
 *  title decides, the same way the default tick does. */
export function fitLevel(person: PersonLike): Level {
  const level = levelOf(person);
  return level !== 'board' && isTooSenior(person) ? 'executive' : level;
}

const FIT_ORDER: Record<Wanted, Level[]> = {
  working: ['working', 'executive', 'unknown', 'board'],
  executive: ['executive', 'working', 'unknown', 'board'],
  board: ['board', 'executive', 'working', 'unknown'],
};

/** 0 is the level asked for; larger is further from it. */
export function fitRank(person: PersonLike, wanted: Wanted): number {
  return FIT_ORDER[wanted].indexOf(fitLevel(person));
}

/** The sheet's order: the level asked for first, then people tied to Yale
 *  within a level. Callers fall back to their own order (name, score) for ties. */
export function sheetOrder(a: PersonLike, b: PersonLike, wanted: Wanted): number {
  return fitRank(a, wanted) - fitRank(b, wanted) || Number(Boolean(yaleOf(b))) - Number(Boolean(yaleOf(a)));
}

/** Shown by default: the level asked for, and a name that is a person's. */
export function matchesWanted(person: PersonLike, wanted: Wanted): boolean {
  return fitLevel(person) === wanted && looksLikePerson(person);
}

const KEYWORD: Record<Wanted, RegExp> = {
  working: /\b(head of|director of|vice president|vp|manager|managing|lead|associate|analyst|coordinator|specialist|partner|principal)\b/i,
  executive: /\b(chief of staff|chief [a-z&,\s-]*officer|ceo|cfo|coo|cto|cio|cmo|chro|chief|president|executive director|treasurer|general counsel)\b/i,
  board: /\b(board member|board of directors|trustee|chair(?:man|woman|person)?|director|committee|governor)\b/i,
};

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Why this person is, or is not, at the level asked for, in plain words. */
export function matchReason(person: PersonLike, wanted: Wanted): string {
  if (!looksLikePerson(person)) return 'The name looks like page text, not a person';
  const level = fitLevel(person);
  if (level === wanted) {
    const word = KEYWORD[wanted].exec(person.title || '')?.[0];
    return word ? `${capital(SINGULAR[wanted])}: "${word}" in the title` : `${capital(SINGULAR[wanted])} by title`;
  }
  if (level === 'unknown') return person.title ? 'Role unclear from the title' : 'No title to judge the level by';
  return `${capital(SINGULAR[level])}, not a ${SINGULAR[wanted]}`;
}

// --- Source --------------------------------------------------------------

const SOURCE_LABEL: Record<string, string> = {
  web_discovery: 'Web search',
  domain_scrape: 'Company website',
  roster_sec: 'SEC filing',
  roster_cache: 'SEC filing',
  linkedin_inferred: 'LinkedIn, address guessed',
  inferred: 'Address guessed from a pattern',
  pattern_inferred: 'Address guessed from a pattern',
};
/** How far a source can be trusted to name the right person, best first. */
const SOURCE_RANK: Record<string, number> = {
  roster_sec: 5, roster_cache: 4, domain_scrape: 4, web_discovery: 3, linkedin_inferred: 2,
};

export type PersonSource = { label: string; link: { href: string; text: string } | null; rank: number };

export function sourceOf(person: PersonLike): PersonSource {
  const raw = (person.contact_source || '').trim();
  const guessed = raw.endsWith('_pattern');
  const base = guessed ? raw.slice(0, -'_pattern'.length) : raw;
  const label = guessed
    ? `${SOURCE_LABEL[base] || capital(base.replaceAll('_', ' '))}, address guessed`
    : SOURCE_LABEL[raw] || (raw ? capital(raw.replaceAll('_', ' ')) : person.linkedin_url ? 'LinkedIn' : 'On file');
  const href = publicSourceUrl(person.linkedin_url || person.source_url || '');
  const host = href ? new URL(href).hostname.replace(/^www\./, '') : '';
  return {
    label,
    link: href ? { href, text: host.endsWith('linkedin.com') ? 'LinkedIn profile' : host } : null,
    rank: guessed ? 0 : (SOURCE_RANK[raw] ?? 1) + (href ? 0.5 : 0),
  };
}

// --- The same person twice -----------------------------------------------

/** "Jean M. Bartik", "jean bartik" and "Jean Bartik60" are one name: accents,
 *  digits, punctuation and initials do not tell people apart. */
function nameWords(name: string): string[] {
  return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z\s]/g, ' ').split(/\s+/).filter((w) => w.length > 1);
}

/** The identity two rows share when they are the same person at the same
 *  company, or null when the name is too thin to say. */
export function personKey(person: PersonLike): string | null {
  const words = nameWords(person.name || '');
  return words.length >= 2 ? `${words.join(' ')}|${companyKey(person.company)}` : null;
}

/** One row per person. Among rows for the same normalized name and company the
 *  best survives: whatever ``bonus`` favours first (someone already written to,
 *  so that history is never hidden), then the more trustworthy source, then one
 *  that has an address. Order is kept. */
export function dedupePeople<T>(items: T[], person: (item: T) => PersonLike, bonus: (item: T) => number = () => 0): T[] {
  const score = (item: T) => bonus(item) * 100 + sourceOf(person(item)).rank * 10 + (person(item).email ? 1 : 0);
  const best = new Map<string, T>();
  for (const item of items) {
    const key = personKey(person(item));
    if (!key) continue;
    const current = best.get(key);
    if (!current || score(item) > score(current)) best.set(key, item);
  }
  return items.filter((item) => {
    const key = personKey(person(item));
    return key === null || best.get(key) === item;
  });
}

// --- What a company's search amounts to ------------------------------------

export type CompanyStatus = {
  tone: 'busy' | 'ok' | 'empty' | 'paused' | 'idle';
  /** Short enough for a lane. */
  text: string;
  /** The sentence for the company's own header. */
  long: string;
  /** Why the search could not run, when it could not. */
  problem?: string;
};

const people = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;

export function companyStatus({ run, total, matching, wanted }: {
  run: CompanyRun; total: number; matching: number; wanted: Wanted;
}): CompanyStatus {
  const label = WANTED_LABEL[wanted];
  const next = WANTED_LABEL[WANTED_NEXT[wanted]];
  if (run.state === 'searching') return { tone: 'busy', text: `Searching… ${Math.round(run.pct)}%`, long: 'Searching…' };
  if (run.state === 'queued') return { tone: 'busy', text: 'Queued', long: 'Queued: waiting for another search to finish' };
  if (run.paused) {
    return total > 0
      ? { tone: 'paused', text: `${people(total)} found · search paused`, long: `${people(total)} found · search paused`, problem: run.paused }
      : { tone: 'paused', text: 'Search paused', long: 'Search paused', problem: run.paused };
  }
  if (run.state === 'failed') {
    return total > 0
      ? { tone: 'paused', text: `${people(total)} found · search stopped`, long: `${people(total)} found · the search stopped early; try again` }
      : { tone: 'paused', text: 'Search stopped', long: 'The search stopped before it found anyone; try again' };
  }
  if (total > 0) {
    const text = `${people(total)} found`;
    const forLevel = matching === total ? '' : matching === 0 ? ` · none for ${label} (try ${next})` : ` · ${matching} for ${label}`;
    return { tone: 'ok', text, long: text + forLevel };
  }
  if (run.state === 'done') return { tone: 'empty', text: 'Nobody found', long: `Nobody found for ${label} (try ${next})` };
  return { tone: 'idle', text: 'Not searched yet', long: `Not searched yet. Press Find people to look for ${label}.` };
}

/** Who a search found at a company that is not already on file there, by
 *  address or as the same name: the sheet and the lanes count them alike. */
export function foundNotOnFile(rows: PersonLike[], found: FoundPerson[]): FoundPerson[] {
  const emails = new Set(rows.map((p) => (p.email || '').trim().toLowerCase()).filter(Boolean));
  const keys = new Set(rows.map(personKey).filter((k): k is string => k !== null));
  return found.filter((p) => {
    const key = personKey(foundAsPerson(p));
    return !emails.has((p.email || '').trim().toLowerCase()) && !(key && keys.has(key));
  });
}
