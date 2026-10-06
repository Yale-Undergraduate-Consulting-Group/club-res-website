import { type Contact } from '../api';

/** What a title makes someone, decided by the server so the register, the
 *  outreach gate and this picker all mean the same thing by "board". */
export type Level = 'working' | 'executive' | 'board' | 'unknown';

/** Board first is the one thing this club must not do, so the order is the
 *  reverse of seniority: the people who answer, then the ones who might. */
export const LEVEL_RANK: Record<Level, number> = { working: 0, executive: 1, unknown: 2, board: 3 };

export const LEVEL_LABEL: Record<Level, string> = {
  working: 'Working level',
  executive: 'Executive',
  board: 'Board seat',
  unknown: 'Unknown role',
};

/** What each band asks a search to look for, in the company's own words. */
export const LEVEL_TITLES: Record<Exclude<Level, 'unknown'>, string> = {
  working: 'Director of, Head of, Manager, Lead',
  executive: 'Chief of Staff, VP, Vice President, Head of',
  board: 'Board member, Trustee, Director',
};

/** The one spelling of a company that the picker's groups, the lanes and the
 *  step counts all agree on. "A24" and "a24 " are the same company; without
 *  this a found person could land in a group beside their own lane. */
export function companyKey(name: string | null | undefined): string {
  return (name || '').trim().toLowerCase();
}

export function levelOf(person: Contact): Level {
  const raw = typeof person.person_level === 'string' ? person.person_level : '';
  return raw === 'working' || raw === 'executive' || raw === 'board' ? raw : 'unknown';
}

/** Anyone this campaign could actually reach. No address is not a choice the
 *  member gets to make, so those rows are shown but never selectable. */
export function isWritable(person: Contact): boolean {
  return Boolean(person.email);
}

/** The people at the very top: chief officers, presidents, chairs, founders.
 *  A student cold email to the CEO of a large company is not answered, and
 *  the server's level is not enough to catch them - a filing lists a CFO's
 *  title as "SVP, Chief Financial Officer", which reads as working level.
 *  So the title decides. "Vice President" and "Chief of Staff" are not this. */
const TOP_OFFICER = /\b(?:c[efot]o|cmo|cio|ciso|cro|cpo|clo|chief\s+[a-z&,\s-]*officer|(?<!vice[\s-])(?<!vice\s)president|chair(?:man|woman|person)?|(?:co-?)?founder)\b/i;
/** The vice presidents a large company layers above the people who answer:
 *  EVP, SVP, CVP and their spelled-out forms. A plain "VP" or "Vice
 *  President" is often the right person at a smaller company and stays. */
const SENIOR_VP = /\b(?:[escg]vp|(?:executive|senior|corporate|group)\s+vice[\s-]president)\b/i;

/** Board work, however a filing words it: a proxy lists directors by their
 *  committee ("Compensation and Talent Management Committee"), which read as
 *  an unknown role and was ticked. */
const BOARD_SEAT = /\b(?:committee|board\s+(?:of\s+directors|member|director)|independent\s+director|non-executive|trustee)\b/i;

/** Too senior to answer a student's cold email: never ticked by default. */
export function isTooSenior(person: Contact): boolean {
  const title = person.title || '';
  return TOP_OFFICER.test(title) || SENIOR_VP.test(title) || BOARD_SEAT.test(title);
}

/** Words that a scraped page puts where a name should be - a job, a
 *  heading, a button - and no one is called. */
const NOT_A_NAME = new Set([
  'activity', 'careers', 'choose', 'company', 'consultant', 'contact', 'contacts', 'copilot', 'director',
  'extensibility', 'find', 'founding', 'group', 'image', 'leader', 'life', 'manager', 'neurodiversity',
  'officer', 'people', 'principal', 'services', 'solutions', 'support', 'team', 'title', 'trainer',
  'transformation',
]);

/** Whether the name on file reads as a person's. Search results sometimes
 *  store a page heading as the name ("Choose People", "Transformation
 *  Leader"), and a draft to one opens "Dear Transformation,". A token with a
 *  digit is a profile id stuck on a real name ("Steve Mathias B1a579") and is
 *  ignored rather than counted against it. */
export function looksLikePerson(person: Contact): boolean {
  const name = (person.name || '').trim();
  if (!name || /[\n\r]/.test(name)) return false;
  const words = name.split(/\s+/).filter((w) => !/\d/.test(w));
  if (words.length < 2 || words.length > 4) return false;
  return words.every((w) => /^[A-Z\u00C0-\u024F][\p{L}'’.-]*$/u.test(w) && !NOT_A_NAME.has(w.toLowerCase()));
}

/** The default tick: people who work there and can be reached, minus anyone
 *  already written to. A board seat, someone too senior to answer, or a row
 *  whose name is not a person's is never ticked by default - reaching one is
 *  a deliberate act here, not a side effect of pressing "select all". */
export function defaultSelection(people: Contact[]): number[] {
  return people
    .filter((p) => isWritable(p) && !p.last_sent_at && levelOf(p) !== 'board'
      && !isTooSenior(p) && looksLikePerson(p))
    .map((p) => p.id);
}
