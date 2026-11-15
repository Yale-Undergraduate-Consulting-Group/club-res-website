import { describe, expect, it } from 'vitest';
import type { Contact } from '../api';
import {
  FUNCTIONS, companyKey, defaultSelection, functionOf, isTooSenior, levelOf, levelTitles, looksLikePerson, yaleOf,
  type Level,
} from './recipients';

const lv = (...levels: Level[]) => new Set<Level>(levels);
const TEAM_AND_SENIOR = lv('working', 'executive');

const person = (id: number, fields: Partial<Contact> = {}): Contact => ({
  id, name: 'Jane Smith', email: `p${id}@example.com`, title: 'Director of Strategy', person_level: 'working', ...fields,
});

describe('defaultSelection', () => {
  it('ticks only reachable, unsent people at a chosen level, with real names', () => {
    const people = [
      person(1),
      person(2, { email: '' }),
      person(3, { last_sent_at: '2026-09-01 10:00:00' }),
      person(4, { person_level: 'board' }),
      person(5, { title: 'SVP, Chief Financial Officer' }),
      person(6, { name: 'Transformation Leader' }),
      person(7, { title: 'Vice President, Marketing', person_level: 'executive' }),
      person(8, { title: 'Chief of Staff', person_level: 'executive' }),
      person(9, { title: 'Member, Compensation and Talent Management Committee', person_level: 'unknown' }),
    ];
    // Senior leaders were asked for, so the SVP/CFO is ticked like the VP.
    expect(defaultSelection(people, TEAM_AND_SENIOR)).toEqual([1, 5, 7, 8]);
  });

  it('leaves the very top for a deliberate tick, even when senior leaders are chosen', () => {
    const people = [
      person(1, { title: 'Chief Executive Officer', person_level: 'executive' }),
      person(2, { title: 'President and CEO', person_level: 'executive' }),
      person(3, { title: 'Co-Founder', person_level: 'executive' }),
      person(4, { title: 'Chief Technology Officer', person_level: 'executive' }),
      person(5, { title: 'Vice President, Sales', person_level: 'executive' }),
    ];
    expect(defaultSelection(people, TEAM_AND_SENIOR)).toEqual([4, 5]);
  });

  it('ticks officers named in the company filing, but not its board seats unless Board is chosen', () => {
    const people = [
      person(1, { title: 'President and CEO', contact_source: 'roster_sec' }),
      person(2, { title: 'Chief Financial Officer', contact_source: 'roster_cache_pattern' }),
      person(3, { title: 'Independent Director', contact_source: 'roster_sec' }),
      person(4, { title: 'Chairman', person_level: 'board', contact_source: 'roster_sec' }),
      person(5, { title: 'President and CEO', contact_source: 'web_discovery' }),
      person(6, { title: 'Treasurer', contact_source: 'roster_cache', email: '' }),
    ];
    expect(defaultSelection(people, TEAM_AND_SENIOR)).toEqual([1, 2]);
    expect(defaultSelection(people, lv('board'))).toEqual([3, 4]);
  });

  it('follows the chosen levels: only the people at one of them are ticked', () => {
    const people = [
      person(1),
      person(2, { title: 'Vice President, Marketing', person_level: 'executive' }),
      person(3, { title: 'Trustee', person_level: 'board' }),
      person(4, { title: '', person_level: 'unknown' }),
    ];
    expect(defaultSelection(people, lv('working'))).toEqual([1]);
    expect(defaultSelection(people, lv('executive', 'board'))).toEqual([2, 3]);
    expect(defaultSelection(people, lv('working', 'executive', 'board', 'unknown'))).toEqual([1, 2, 3, 4]);
  });

  // Before levels were chosen, anyone whose role the server could not read was
  // ticked along with the team leads; now they are ticked only on "No title shown".
  it('ticks people with no title or no level only when No title shown is chosen', () => {
    const people = [
      person(1),
      person(2, { title: '', person_level: 'unknown' }),
      person(3, { title: 'Consultant', person_level: 'unknown' }),
      person(4, { title: '', person_level: 'unknown', email: '' }),
      person(5, { title: '', person_level: 'unknown', name: 'Choose People' }),
    ];
    expect(defaultSelection(people, TEAM_AND_SENIOR)).toEqual([1]);
    expect(defaultSelection(people, lv('unknown'))).toEqual([2, 3]);
  });

  it('never ticks someone too senior to answer for Senior leaders, unless their filing names them', () => {
    const people = [
      person(1, { title: 'Chief Executive Officer', person_level: 'executive' }),
      person(2, { title: 'Chief Executive Officer', person_level: 'executive', contact_source: 'roster_sec' }),
    ];
    expect(defaultSelection(people, lv('executive'))).toEqual([2]);
    expect(defaultSelection(people, lv('working'))).toEqual([]);
  });
});

describe('levelTitles', () => {
  it('asks for the titles of every chosen level once, and none for No title shown', () => {
    expect(levelTitles(lv('working'))).toBe('Director of, Head of, Manager, Lead');
    expect(levelTitles(lv('working', 'executive'))).toBe('Director of, Head of, Manager, Lead, Chief of Staff, VP, Vice President');
    expect(levelTitles(lv('unknown'))).toBe('');
    expect(levelTitles(lv('board', 'unknown'))).toBe('Board member, Trustee, Director');
  });
});

describe('isTooSenior', () => {
  it.each([
    'CEO', 'Chief Executive Officer', 'SVP, Chief Financial Officer', 'President', 'Chairwoman', 'Co-Founder',
    'EVP Operations', 'Senior Vice President, Sales', 'Independent Director', 'Board Member', 'Trustee',
  ])('holds back %s', (title) => expect(isTooSenior(person(1, { title }))).toBe(true));

  it.each(['Vice President', 'Vice-President of Sales', 'VP Marketing', 'Chief of Staff', 'Director of Finance', 'Head of Partnerships', ''])(
    'allows %s', (title) => expect(isTooSenior(person(1, { title }))).toBe(false),
  );
});

describe('looksLikePerson', () => {
  it.each([
    ['Steve Mathias B1a579', true],
    ['José Álvarez', true],
    ["Mary-Jane O'Neil", true],
    ['Choose People', false],
    ['Madonna', false],
    ['jane smith', false],
    ['Jane\nSmith', false],
    ['A B C D E', false],
    ['', false],
  ])('%j reads as a person: %s', (name, expected) => expect(looksLikePerson(person(1, { name }))).toBe(expected));
});

describe('recipient grouping', () => {
  it('treats spelling variants of a company as one key', () => {
    expect(companyKey(' A24 ')).toBe(companyKey('a24'));
    expect(companyKey(null)).toBe('');
  });

  it('reads unrecognised server levels as unknown', () => {
    expect(levelOf(person(1, { person_level: 'Board' }))).toBe('unknown');
    expect(levelOf(person(1, { person_level: undefined }))).toBe('unknown');
    expect(levelOf(person(1, { person_level: 'executive' }))).toBe('executive');
  });
});

describe('Yale first', () => {
  it('ticks people tied to Yale ahead of the rest, in list order otherwise', () => {
    const people = [
      person(1),
      person(2, { yale_signal: 'BA, Yale University' }),
      person(3),
      person(4, { yale_signal: 'Yale \u201905' }),
      // Yale does not lift anyone the default tick holds back.
      person(5, { yale_signal: 'Yale SOM', person_level: 'board' }),
      person(6, { yale_signal: 'Yale College', email: '' }),
      person(7, { yale_signal: 'Yale College', last_sent_at: '2026-09-01 10:00:00' }),
      person(8, { yale_signal: 'Yale College', title: 'Chief Executive Officer' }),
    ];
    expect(defaultSelection(people, TEAM_AND_SENIOR)).toEqual([2, 4, 1, 3]);
  });

  it('reads blank evidence as no tie', () => {
    expect(yaleOf({ yale_signal: '  ' })).toBe('');
    expect(yaleOf({ yale_signal: null })).toBe('');
    expect(yaleOf({ yale_signal: 'Education: Yale University ' })).toBe('Education: Yale University');
  });
});

describe('function', () => {
  it('reads only the functions the server names', () => {
    expect(functionOf({ person_function: 'finance' })).toBe('finance');
    expect(functionOf({ person_function: 'general' })).toBe('general');
    expect(functionOf({ person_function: '' })).toBeNull();
    expect(functionOf({ person_function: 'Finance' })).toBeNull();
    expect(functionOf({ person_function: 'astrology' })).toBeNull();
    expect(functionOf({})).toBeNull();
  });

  it('offers every function once, with a label', () => {
    expect(new Set(FUNCTIONS.map((f) => f.id)).size).toBe(FUNCTIONS.length);
    expect(FUNCTIONS.map((f) => f.id)).toEqual([
      'finance', 'strategy', 'operations', 'marketing', 'sales', 'people', 'technology', 'legal', 'general',
    ]);
    expect(FUNCTIONS.every((f) => f.label.trim().length > 0)).toBe(true);
  });
});
