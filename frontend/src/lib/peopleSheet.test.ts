import { describe, expect, it } from 'vitest';
import {
  companyStatus, dedupePeople, fitRank, foundAsPerson, foundNotOnFile, matchReason, matchesWanted, personKey, sheetOrder,
  sourceOf, wantedLabel,
  type PersonLike,
} from './peopleSheet';
import type { Level } from './recipients';
import type { CompanyRun, FoundPerson } from './useDiscoveryRuns';

const lv = (...levels: Level[]) => new Set<Level>(levels);
const who = (fields: PersonLike = {}): PersonLike => ({
  name: 'Jean Bartik', title: 'Director of Operations', company: 'A24', person_level: 'working', ...fields,
});
const run = (fields: Partial<CompanyRun> = {}): CompanyRun => ({ state: 'idle', pct: 0, message: '', ...fields });

describe('matching the level asked for', () => {
  it('puts the levels asked for first, then the rest', () => {
    const lead = who();
    const chief = who({ name: 'Ada Lovelace', title: 'Vice President, Partnerships', person_level: 'executive' });
    const trustee = who({ name: 'Alan Turing', title: 'Trustee', person_level: 'board' });
    const people = [trustee, chief, lead];
    expect(people.sort((a, b) => fitRank(a, lv('working')) - fitRank(b, lv('working')))).toEqual([lead, chief, trustee]);
    expect(people.sort((a, b) => fitRank(a, lv('board')) - fitRank(b, lv('board')))).toEqual([trustee, lead, chief]);
    // Both chosen levels outrank the one that is not.
    expect(people.sort((a, b) => fitRank(a, lv('executive', 'board')) - fitRank(b, lv('executive', 'board'))))
      .toEqual([chief, trustee, lead]);
  });

  it('counts a chief officer as a senior leader even when a filing reads as working level', () => {
    const cfo = who({ name: 'Anat Ashkenazi', title: 'SVP, Chief Financial Officer', person_level: 'working' });
    expect(matchesWanted(cfo, lv('working'))).toBe(false);
    expect(matchesWanted(cfo, lv('executive'))).toBe(true);
  });

  it('matches a person at any chosen level, and no title only when asked', () => {
    const lead = who();
    const trustee = who({ name: 'Alan Turing', title: 'Trustee', person_level: 'board' });
    const blank = who({ name: 'Ada Lovelace', title: '', person_level: 'unknown' });
    const wanted = lv('working', 'board');
    expect([lead, trustee, blank].map((p) => matchesWanted(p, wanted))).toEqual([true, true, false]);
    expect([lead, trustee, blank].map((p) => matchesWanted(p, lv('unknown')))).toEqual([false, false, true]);
  });

  it('never shows page text as a match', () => {
    expect(matchesWanted(who({ name: 'Transformation Leader' }), lv('working'))).toBe(false);
  });

  it('says why in plain words', () => {
    const working = lv('working');
    expect(matchReason(who(), working)).toBe('Team lead: "Director of" in the title');
    expect(matchReason(who({ title: 'Trustee', person_level: 'board' }), working)).toBe('Board seat, not a team lead');
    expect(matchReason(who({ title: 'Trustee', person_level: 'board' }), lv('working', 'executive')))
      .toBe('Board seat, not a team lead or a senior leader');
    expect(matchReason(who({ title: '', person_level: 'unknown' }), working)).toBe('No title to judge the level by');
    expect(matchReason(who({ title: '', person_level: 'unknown' }), lv('unknown'))).toBe('No title shown');
    expect(matchReason(who({ name: 'Choose People' }), working)).toMatch(/page text/);
  });
});

describe('the sheet order', () => {
  const yale = 'Education: Yale University';

  it('puts people tied to Yale first within a level, never across one', () => {
    const lead = who({ name: 'Ada Lovelace' });
    const yaleLead = who({ name: 'Grace Hopper', yale_signal: yale });
    const yaleChief = who({ name: 'Alan Turing', title: 'Vice President, Partnerships', person_level: 'executive', yale_signal: yale });
    const chief = who({ name: 'Dana Scully', title: 'Vice President, Partnerships', person_level: 'executive' });
    const people = [chief, lead, yaleChief, yaleLead];
    expect(people.sort((a, b) => sheetOrder(a, b, lv('working')))).toEqual([yaleLead, lead, yaleChief, chief]);
    expect(people.sort((a, b) => sheetOrder(a, b, lv('executive')))).toEqual([yaleChief, chief, yaleLead, lead]);
  });

  it('keeps the list order for people who tie', () => {
    const first = who({ name: 'Ada Lovelace' });
    const second = who({ name: 'Grace Hopper' });
    expect([second, first].sort((a, b) => sheetOrder(a, b, lv('working')))).toEqual([second, first]);
  });

  it('carries Yale and function from a found person to the sheet', () => {
    const found: FoundPerson = {
      id: 1, run_id: 1, first_name: 'Grace', last_name: 'Hopper', email: 'g@a24.com', company: 'A24',
      title: 'Finance Manager', person_function: 'finance', yale_signal: yale,
    };
    expect(foundAsPerson(found)).toMatchObject({ person_function: 'finance', yale_signal: yale });
  });
});

describe('where someone came from', () => {
  it('names the source and links only a real page', () => {
    expect(sourceOf(who({ contact_source: 'roster_sec' })).label).toBe('SEC filing');
    expect(sourceOf(who({ contact_source: 'roster_register' })).label).toBe('Company filing');
    expect(sourceOf(who({ contact_source: 'web_discovery', linkedin_url: 'https://www.linkedin.com/in/jean' })).link)
      .toEqual({ href: 'https://www.linkedin.com/in/jean', text: 'LinkedIn profile' });
    expect(sourceOf(who({ contact_source: 'domain_scrape', source_url: 'javascript:alert(1)' })).link).toBeNull();
    expect(sourceOf(who({ contact_source: 'domain_scrape_pattern' })).label).toBe('Company website, address guessed');
    expect(sourceOf(who({ contact_source: 'domain_scrape', source_url: 'https://a24.com/team' })).rank).toBe(4.5);
    expect(sourceOf(who({ contact_source: null })).label).toBe('On file');
  });

  it.each([
    ['web_company_page', 'Company leadership page', 4],
    ['web_press', 'Press release', 3],
    ['web_theorg', 'The Org', 3],
    ['web_crunchbase', 'Crunchbase', 3],
    ['web_speaker', 'Conference speaker page', 3],
    ['web_directory', 'Public profile directory', 2],
  ])('labels and ranks %s', (contact_source, label, rank) => {
    expect(sourceOf(who({ contact_source }))).toMatchObject({ label, rank });
    expect(sourceOf(who({ contact_source: `${contact_source}_pattern` }))).toMatchObject({ label: `${label}, address guessed`, rank: 0 });
  });
});

describe('the same person twice', () => {
  it('reads one name however it is written', () => {
    expect(personKey(who({ name: 'Jean M. Bartik' }))).toBe(personKey(who({ name: 'jean bartik60' })));
    expect(personKey(who({ name: 'Zoë Müller' }))).toBe(personKey(who({ name: 'Zoe Muller' })));
    expect(personKey(who({ name: 'Madonna' }))).toBeNull();
    expect(personKey(who({ company: 'B24' }))).not.toBe(personKey(who()));
  });

  it('keeps one row, the one from the better source', () => {
    const web = who({ contact_source: 'web_discovery', email: 'jbartik@a24.com' });
    const site = who({ name: 'Jean M. Bartik', contact_source: 'domain_scrape', email: 'jean.bartik@a24.com' });
    const other = who({ name: 'Klara Dan', contact_source: 'web_discovery' });
    expect(dedupePeople([web, other, site], (p) => p)).toEqual([other, site]);
  });

  it('never hides someone already written to, whatever their source', () => {
    const sent = { ...who({ contact_source: 'web_discovery' }), last_sent_at: '2026-09-01 10:00:00' };
    const site = who({ contact_source: 'domain_scrape' });
    expect(dedupePeople([site, sent], (p) => p, (p) => ('last_sent_at' in p ? 1 : 0))).toEqual([sent]);
  });

  it('keeps people whose names are too thin to compare', () => {
    const a = who({ name: 'Madonna' });
    expect(dedupePeople([a, { ...a }], (p) => p)).toHaveLength(2);
  });

  it('does not list a found person who is already on file, by address or by name', () => {
    const found = (id: number, first: string, last: string, email: string): FoundPerson => (
      { id, run_id: 1, first_name: first, last_name: last, email, company: 'A24', title: 'Head of Insight' });
    const onFile = [who({ email: 'jean.bartik@a24.com' }), who({ name: 'Klara Dan', email: 'klara@a24.com' })];
    const left = foundNotOnFile(onFile, [
      found(1, 'Jean', 'Bartik', 'jbartik@a24.com'),
      found(2, 'Someone', 'Else', 'klara@a24.com'),
      found(3, 'Ada', 'Lovelace', 'ada@a24.com'),
    ]);
    expect(left.map((p) => p.id)).toEqual([3]);
  });
});

describe('what a company says about its search', () => {
  const base = { wanted: lv('working') };

  it('is searching while it works', () => {
    expect(companyStatus({ ...base, run: run({ state: 'searching', pct: 40 }), total: 0, ready: 0, matching: 0 }))
      .toMatchObject({ tone: 'busy', text: 'Searching… 40%', long: 'Searching…' });
  });

  it('says how many can be emailed, and names the level when the rest are another', () => {
    expect(companyStatus({ ...base, run: run({ state: 'done' }), total: 1, ready: 1, matching: 1 }).text).toBe('1 can be emailed');
    expect(companyStatus({ ...base, run: run({ state: 'done' }), total: 5, ready: 2, matching: 2 }).long)
      .toBe('2 can be emailed · 5 people found · 2 for Team leads');
    expect(companyStatus({ ...base, run: run({ state: 'done' }), total: 3, ready: 0, matching: 0 }).long)
      .toBe('0 can be emailed · 3 people found · none for Team leads (try Senior leaders)');
  });

  it('says nobody was found only when a search ran and found nobody', () => {
    expect(companyStatus({ ...base, run: run({ state: 'done' }), total: 0, ready: 0, matching: 0 }))
      .toMatchObject({ tone: 'empty', long: 'Nobody found for Team leads (try Senior leaders)' });
    expect(companyStatus({ run: run({ state: 'done' }), total: 0, ready: 0, matching: 0, wanted: lv('working', 'executive', 'unknown') }).long)
      .toBe('Nobody found for Team leads, Senior leaders and No title shown (try Board)');
    expect(companyStatus({ run: run({ state: 'done' }), total: 2, ready: 0, matching: 0, wanted: lv('working', 'executive', 'board', 'unknown') }).long)
      .toBe('0 can be emailed · 2 people found · none for Team leads, Senior leaders, Board and No title shown');
    expect(companyStatus({ ...base, run: run(), total: 0, ready: 0, matching: 0 }).tone).toBe('idle');
  });

  it('words the chosen levels as a list', () => {
    expect(wantedLabel(lv('executive', 'working'))).toBe('Team leads and Senior leaders');
    expect(wantedLabel(lv('unknown'))).toBe('No title shown');
  });

  it('says the search is paused, never nobody found, when it could not run', () => {
    const paused = 'People search is paused: the search service is out of credit. A maintainer needs to top it up.';
    const status = companyStatus({ ...base, run: run({ state: 'done', paused }), total: 0, ready: 0, matching: 0 });
    expect(status).toMatchObject({ tone: 'paused', text: 'Search paused', problem: paused });
    expect(`${status.text} ${status.long}`).not.toMatch(/nobody/i);
    expect(companyStatus({ ...base, run: run({ state: 'done', paused }), total: 4, ready: 3, matching: 4 }).text)
      .toBe('3 can be emailed · search paused');
  });
});
