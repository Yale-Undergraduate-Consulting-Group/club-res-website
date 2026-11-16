import { describe, expect, it } from 'vitest';
import type { ResourceItem } from '../api';
import {
  buildResourceQuery, clientText, formatFileSize, formatRevenue, groupTeam, hasAdminDetails, hiddenFilesNote, kindLabel, kindSingular, orderKinds,
} from './resourcesView';

describe('kind names', () => {
  it('uses the filter labels the club knows the groups by', () => {
    expect(['past_project', 'template', 'interview', 'survey', 'analytics', 'other'].map(kindLabel)).toEqual([
      'Past projects', 'Slide templates', 'Interview guides', 'Survey guides', 'Analytics', 'Other',
    ]);
    expect(kindSingular('template')).toBe('Slide template');
  });
  it('never shows a raw id for a kind it does not know', () => {
    expect(kindLabel('case_study')).toBe('Case study');
  });
  it('lists the kinds in the filter order, unknown kinds last', () => {
    expect(orderKinds(['other', 'case_study', 'past_project', 'survey'])).toEqual(['past_project', 'survey', 'other', 'case_study']);
  });
});

describe('formatFileSize', () => {
  it.each([
    [0, '0 B'],
    [512, '512 B'],
    [1536, '1.5 KB'],
    [20 * 1024, '20 KB'],
    [5 * 1024 * 1024, '5 MB'],
    [1.25 * 1024 ** 3, '1.3 GB'],
  ])('%d bytes is %s', (bytes, text) => expect(formatFileSize(bytes)).toBe(text));
  it('shows nothing for a size that cannot be real', () => {
    expect(formatFileSize(-1)).toBe('');
    expect(formatFileSize(Number.NaN)).toBe('');
  });
});

describe('formatRevenue', () => {
  it('writes whole dollars with separators', () => {
    expect(formatRevenue(125000)).toBe('$125,000');
    expect(formatRevenue(0)).toBe('$0');
  });
  it('says so when no revenue is on record', () => {
    expect(formatRevenue(null)).toBe('Not recorded');
    expect(formatRevenue(undefined)).toBe('Not recorded');
  });
});

describe('buildResourceQuery', () => {
  it('sends only what is set', () => {
    expect(buildResourceQuery({})).toBe('');
    expect(buildResourceQuery({ q: '  pricing ', kind: 'past_project', semester: 'Fall 2025', industry: 'Healthcare', limit: 30, offset: 60 }))
      .toBe('q=pricing&kind=past_project&semester=Fall+2025&industry=Healthcare&limit=30&offset=60');
  });
  it('leaves out an empty search, an unset filter and a zero offset', () => {
    expect(buildResourceQuery({ q: '   ', kind: '', semester: '', industry: '', limit: 30, offset: 0 })).toBe('limit=30');
  });
  it('escapes what a member types', () => {
    expect(buildResourceQuery({ q: 'a&b=c' })).toBe('q=a%26b%3Dc');
  });
});

describe('clientText', () => {
  it('names a confidential client without the name', () => {
    expect(clientText({ client: null, client_hidden: true })).toBe('Confidential client');
    expect(clientText({ client: 'Acme', client_hidden: true })).toBe('Confidential client');
  });
  it('shows the name when the viewer may see it, and nothing when there is none', () => {
    expect(clientText({ client: ' Northwind Clinic ', client_hidden: false })).toBe('Northwind Clinic');
    expect(clientText({ client: null, client_hidden: false })).toBeNull();
    expect(clientText({ client: '', client_hidden: false })).toBeNull();
  });
});

describe('hiddenFilesNote', () => {
  it('counts files the viewer may not open', () => {
    expect(hiddenFilesNote({ files_open: false, files_hidden_count: 3 })).toBe('3 files available to the project team');
    expect(hiddenFilesNote({ files_open: false, files_hidden_count: 1 })).toBe('1 file available to the project team');
  });
  it('is silent when files are open or none are hidden', () => {
    expect(hiddenFilesNote({ files_open: true, files_hidden_count: 3 })).toBeNull();
    expect(hiddenFilesNote({ files_open: false, files_hidden_count: 0 })).toBeNull();
  });
});

describe('groupTeam', () => {
  it('groups by role, directors first, keeping each person\'s member flag', () => {
    const groups = groupTeam([
      { name: 'Cara', role: 'Associate', member: false },
      { name: 'Ana', role: 'Engagement director', member: true },
      { name: 'Ben', role: 'Associate', member: true },
    ]);
    expect(groups.map((g) => [g.role, g.members.map((m) => m.name)])).toEqual([
      ['Engagement director', ['Ana']],
      ['Associate', ['Cara', 'Ben']],
    ]);
    expect(groups[1].members.map((m) => m.member)).toEqual([false, true]);
  });
  it('is empty for no team', () => {
    expect(groupTeam([])).toEqual([]);
  });
});

describe('hasAdminDetails', () => {
  const base = { id: 1 } as ResourceItem;
  it('is true only when the server sent revenue or contacts', () => {
    expect(hasAdminDetails(base)).toBe(false);
    expect(hasAdminDetails({ ...base, revenue: null })).toBe(true);
    expect(hasAdminDetails({ ...base, contacts: [] })).toBe(true);
  });
});
