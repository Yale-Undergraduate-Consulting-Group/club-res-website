import { describe, expect, it } from 'vitest';
import { companiesFromPaste, companiesFromRows, parseDelimited } from './companySheet';

describe('a list of companies from a sheet', () => {
  it('reads the Company and Website columns under their headings', () => {
    const rows = parseDelimited('Website,Company\nglobex.com,Globex\nhttps://www.initech.com/about,"Initech, Inc."\n');
    expect(companiesFromRows(rows)).toEqual([
      { name: 'Globex', domain: 'globex.com' },
      { name: 'Initech, Inc.', domain: 'initech.com' },
    ]);
  });

  it('takes the first column when there is no heading, and keeps the first row', () => {
    expect(companiesFromRows(parseDelimited('A24\nNEON\r\na24\n\nHBO'))).toEqual([
      { name: 'A24' }, { name: 'NEON' }, { name: 'HBO' },
    ]);
  });

  it('does not take a Website column for the names', () => {
    expect(companiesFromRows([['Website', 'Other'], ['globex.com', 'x']])).toEqual([{ name: 'x', domain: 'globex.com' }]);
  });

  it('treats pasted lines as a table only with tabs or headings', () => {
    expect(companiesFromPaste('Meta Platforms, Inc.\nA24')).toBeNull();
    expect(companiesFromPaste('Globex\tglobex.com\nA24\ta24films.com')).toEqual([{ name: 'Globex' }, { name: 'A24' }]);
    expect(companiesFromPaste('Company,Website\nGlobex,globex.com')).toEqual([{ name: 'Globex', domain: 'globex.com' }]);
  });
});
