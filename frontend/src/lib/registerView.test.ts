import { describe, expect, it } from 'vitest';
import { kindLabel, locationText, peopleText, summaryLine } from './registerView';

describe('kindLabel', () => {
  it('uses the plain names members know the groups by', () => {
    expect(['us_nonprofit', 'us_employer', 'us_private', 'us_public', 'uk', 'club_targets'].map(kindLabel)).toEqual([
      'Nonprofit', 'US employer', 'Recently funded', 'US listed', 'UK', 'Club target',
    ]);
  });
  it('never shows a raw id for a kind it does not know', () => {
    expect(kindLabel('some_new_tier')).toBe('some new tier');
  });
});

describe('locationText', () => {
  it('names one state, or counts several', () => {
    expect(locationText('US', ['TN'])).toBe('TN');
    expect(locationText('US', ['CT', 'NY', 'TX', 'OH'])).toBe('4 states');
    expect(locationText('US', [])).toBe('US');
  });
  it('puts the country after a UK place', () => {
    expect(locationText('GB', ['High Wycombe'])).toBe('High Wycombe, UK');
    expect(locationText('GB', ['Leeds', 'London'])).toBe('2 places, UK');
    expect(locationText('GB', [])).toBe('UK');
  });
});

describe('peopleText', () => {
  it('agrees with the count', () => {
    expect(peopleText(1)).toBe('1 named person');
    expect(peopleText(12)).toBe('12 named people');
  });
});

describe('summaryLine', () => {
  it('counts companies and drops the UK when there are none', () => {
    const tiers = [{ tier: 'us_public', n: 8031 }, { tier: 'us_nonprofit', n: 26689 }];
    expect(summaryLine({ companies: 30000, with_people: 120, tiers })).toBe(
      '30,000 companies · 8,031 US listed · 0 US employers · 26,689 nonprofits · 0 recently funded · 120 with named people',
    );
    expect(summaryLine({ companies: 1, with_people: 0, tiers: [{ tier: 'uk', n: 3 }] })).toContain('3 UK');
  });
});
