import { describe, expect, it } from 'vitest';
import type { Contact } from '../api';
import { companyKey, defaultSelection, isTooSenior, levelOf, looksLikePerson } from './recipients';

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
