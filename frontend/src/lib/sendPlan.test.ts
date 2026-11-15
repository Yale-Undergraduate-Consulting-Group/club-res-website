import { describe, expect, it } from 'vitest';
import type { Contact } from '../api';
import { recipientsOf, tallyPeople, tallyReasons } from './sendPlan';

const person = (id: number, fields: Partial<Contact> = {}): Contact => ({
  id, name: 'Jane Smith', email: `p${id}@example.com`, ...fields,
});

describe('tallyPeople', () => {
  const people = [
    person(1),
    person(2),
    person(3, { email: '' }),
    person(4, { last_sent_at: '2026-09-01 10:00:00' }),
    person(5),
    person(6, { last_sent_at: '2026-09-01 10:00:00' }),
  ];

  it('puts everyone in exactly one bucket, so the reasons add up to the list', () => {
    const tally = tallyPeople(people, new Set([1, 2, 6]), new Map([[2, 'no first name on record']]), 1);
    expect(tally).toEqual({ total: 7, canEmail: 2, noAddress: 1, held: 2, written: 1, notTicked: 1 });
    expect(tally.canEmail + tally.noAddress + tally.held + tally.written + tally.notTicked).toBe(tally.total);
  });

  it('counts a ticked person who was written to before as one who can be emailed', () => {
    expect(tallyPeople(people, new Set([6]), new Map()).canEmail).toBe(1);
  });

  it('leaves held people out of who is sent to', () => {
    const held = new Map([[2, 'held']]);
    expect(recipientsOf(people, new Set([1, 2, 3]), held).map((p) => p.id)).toEqual([1]);
  });

  it('names only the reasons that apply', () => {
    expect(tallyReasons(tallyPeople(people, new Set([1]), new Map())))
      .toBe('1 no address · 2 already written to · 2 not ticked');
    expect(tallyReasons(tallyPeople([person(1)], new Set([1]), new Map()))).toBe('');
  });
});
