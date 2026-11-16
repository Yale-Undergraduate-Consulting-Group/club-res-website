import { describe, expect, it } from 'vitest';
import type { ResourceItem } from '../api';
import {
  buildResourceQuery, clientText, disclosureLabel, editableLink, facetOptions, fileCountText, formatFileSize, formatRevenue,
  groupResources, groupTeam, hasAdminDetails, hiddenFilesNote, kindLabel, kindSingular, linkLabel, orderKinds, semesterRank,
} from './resourcesView';

describe('kind names', () => {
  it('uses the filter labels the club knows the groups by', () => {
    expect(['past_project', 'template', 'interview', 'survey', 'analytics', 'other'].map(kindLabel)).toEqual([
      'Past projects', 'Templates', 'Interview', 'Survey', 'Analytics', 'Other',
    ]);
    expect(kindSingular('template')).toBe('Template');
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
});

describe('buildResourceQuery', () => {
  it('sends only what is set', () => {
    expect(buildResourceQuery({})).toBe('');
    expect(buildResourceQuery({ q: '  pricing ', kind: 'past_project', semester: 'Fall 2025', industry: 'Healthcare', disclosure: 'public', limit: 30, offset: 60 }))
      .toBe('q=pricing&kind=past_project&semester=Fall+2025&industry=Healthcare&disclosure=public&limit=30&offset=60');
  });
  it('leaves out an empty search, an unset filter and a zero offset', () => {
    expect(buildResourceQuery({ q: '   ', kind: '', semester: '', industry: '', disclosure: '', limit: 30, offset: 0 })).toBe('limit=30');
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


describe('facets and groups', () => {
  const item = (id: number, kind: ResourceItem['kind'], title: string, semester: string | null): ResourceItem => ({
    id, kind, title, semester, client: null, client_hidden: false, industry: null, industries: [], areas: null,
    description: null, code_name: null, team_size: null, disclosure: 'public', status: null, files_open: true, can_manage: false,
    files: [], files_hidden_count: 0, team: [], created_at: 0, link: null,
  });
  it('names disclosures and link providers for people, not the API', () => {
    expect(['public', 'name_only', 'private'].map(disclosureLabel)).toEqual(['Deliverable & name', 'Name only', 'Team only']);
    expect(linkLabel('sharepoint')).toBe('Open in SharePoint');
    expect(linkLabel('google_drive')).toBe('Open in Google Drive');
  });
  it('starts the admin link field with the address that opens, never a bare SharePoint path', () => {
    const sharepoint = { url: 'https://tenant.sharepoint.com/:f:/r/sites/S/x', provider: 'sharepoint' as const };
    expect(editableLink({ link: sharepoint, stored_link: '/:f:/r/sites/S/x' })).toBe(sharepoint.url);
    expect(editableLink({ link: null, stored_link: '/:f:/r/sites/S/x' })).toBe('');
    expect(editableLink({ link: null, stored_link: 'https://drive.google.com/x' })).toBe('https://drive.google.com/x');
    expect(editableLink({ link: null, stored_link: null })).toBe('');
  });
  it('keeps a selected facet present with a zero count', () => {
    expect(facetOptions([{ value: 'Healthcare', count: 3 }], 'Retail')).toEqual([
      { value: 'Retail', count: 0 }, { value: 'Healthcare', count: 3 },
    ]);
    expect(facetOptions([{ value: 'Retail', count: 1 }], 'Retail')).toHaveLength(1);
  });
  it('groups projects by semester newest first, and other resources by type', () => {
    const groups = groupResources([
      item(1, 'past_project', 'Old', 'Spring 2025'), item(2, 'template', 'T', null),
      item(3, 'past_project', 'New', 'Fall 2025'), item(4, 'interview', 'I', null),
      item(5, 'past_project', 'No semester', null),
    ]);
    expect(groups.map((group) => [group.label, group.items.map((entry) => entry.title)])).toEqual([
      ['Fall 2025', ['New']], ['Spring 2025', ['Old']], ['Past projects', ['No semester']], ['Templates', ['T']], ['Interview', ['I']],
    ]);
    expect(semesterRank('Fall 2025')).toBeGreaterThan(semesterRank('Spring 2025'));
  });
  it('counts shown and hidden files in the compact row', () => {
    const base = { files: [], files_hidden_count: 0 };
    expect(fileCountText(base)).toBeNull();
    expect(fileCountText({ files: [{ id: 1 } as ResourceItem['files'][number]], files_hidden_count: 1 })).toBe('2 files');
  });
});
