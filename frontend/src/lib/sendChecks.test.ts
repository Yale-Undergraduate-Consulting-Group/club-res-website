import { describe, expect, it } from 'vitest';
import type { SendWarning } from '../api';
import {
  beyondMostSenior, companiesOf, liveWarnings, personalisationFor, unconfirmed, usesPersonalisation, warningText,
  type CompanyLine,
} from './sendChecks';

const people = [
  { company: 'Acme Health' }, { company: 'acme health ' }, { company: 'Globex' }, { company: '' }, { company: null },
];

describe('one line for each company', () => {
  it('lists each company once, by the spelling first seen, and skips people with none', () => {
    expect(companiesOf(people)).toEqual([{ key: 'acme health', name: 'Acme Health' }, { key: 'globex', name: 'Globex' }]);
  });

  it('only asks for lines when the message has the field', () => {
    expect(usesPersonalisation('in the new year. {personalisation}')).toBe(true);
    expect(usesPersonalisation('Hi {first}')).toBe(false);
  });

  it('holds Send until every company is confirmed, an empty confirmed line included', () => {
    const companies = companiesOf(people);
    const lines: Record<string, CompanyLine> = { 'acme health': { text: 'A suggestion', confirmed: false } };
    expect(unconfirmed(companies, lines)).toBe(2);
    lines['acme health'] = { text: 'A suggestion', confirmed: true };
    expect(unconfirmed(companies, lines)).toBe(1);
    lines.globex = { text: '', confirmed: true };
    expect(unconfirmed(companies, lines)).toBe(0);
    expect(personalisationFor(companies, lines)).toEqual({ 'Acme Health': 'A suggestion', Globex: '' });
  });
});

describe('send warnings', () => {
  const team: SendWarning = {
    kind: 'team', company: 'Initech', team: 'Marketing', count: 4, threshold: 3, keep: 2, contact_ids: [2, 4, 3, 1],
  };

  it('keeps the most senior two and unticks the rest', () => {
    expect(beyondMostSenior(team)).toEqual([3, 1]);
  });

  it('re-counts against who would be sent to now, and drops a warning once below its threshold', () => {
    expect(liveWarnings([team], new Set([1, 2, 3, 4]))).toEqual([team]);
    expect(liveWarnings([team], new Set([1, 2, 4]))).toEqual([{ ...team, count: 3, contact_ids: [2, 4, 1] }]);
    expect(liveWarnings([team], new Set([2, 4]))).toEqual([]);
  });

  it('says how many, which team and where', () => {
    expect(warningText(team)).toBe("You're emailing 4 people on the Marketing team at Initech — they often compare notes; consider 1–2.");
    expect(warningText({ ...team, kind: 'company', count: 6 })).toContain('6 people at Initech');
  });
});
