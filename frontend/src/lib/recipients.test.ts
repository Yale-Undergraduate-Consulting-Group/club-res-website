import { describe, expect, it } from 'vitest';
import type { Contact } from '../api';
import {
  FUNCTIONS, companyKey, defaultSelection, functionOf, isTooSenior, levelOf, looksLikePerson, yaleOf,
} from './recipients';

const person = (id: number, fields: Partial<Contact> = {}): Contact => ({
  id, name: 'Jane Smith', email: `p${id}@example.com`, title: 'Director of Strategy', person_level: 'working', ...fields,
});

describe('defaultSelection', () => {
  it('ticks only reachable, unsent, working-level people with real names', () => {
    const people = [
      person(1),
      person(2, { email: '' }),
      person(3, { last_sent_at: '2026-09-01 10:00:00' }),
      person(4, { person_level: 'board' }),
      person(5, { title: 'SVP, Chief Financial Officer' }),
      person(6, { name: 'Transformation Leader' }),
      person(7, { title: 'Vice President, Marketing', person_level: 'executive' }),
      person(8, { title: 'Chief of Staff', person_level: 'unknown' }),
      person(9, { title: 'Member, Compensation and Talent Management Committee', person_level: 'unknown' }),
    ];
    expect(defaultSelection(people)).toEqual([1, 7, 8]);
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
    expect(defaultSelection(people)).toEqual([2, 4, 1, 3]);
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
