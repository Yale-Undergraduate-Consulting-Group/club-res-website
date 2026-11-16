/**
 * A list of companies from a spreadsheet or a pasted table: one company per
 * row, its name in the first column or under a heading called company, name
 * or company name, and optionally its website under domain or website.
 *
 * CSV is read here; an .xlsx is turned into the same rows by the server
 * (api.yucgoutreach.companySheet), so both go through one reader.
 */

export type SheetCompany = { name: string; domain?: string };

const NAME_HEADINGS = new Set(['company', 'name', 'company name', 'organization', 'organisation']);
const DOMAIN_HEADINGS = new Set(['domain', 'website', 'web site', 'url', 'site', 'company domain', 'company website']);

/** Text as rows of cells. Quoted cells may hold the delimiter, quotes ("")
 *  and line breaks. The delimiter is the commonest of tab, semicolon and
 *  comma on the first line. */
export function parseDelimited(text: string): string[][] {
  const clean = text.replace(/^\uFEFF/, '');
  const first = clean.split(/\r?\n/, 1)[0];
  const count = (ch: string) => first.split(ch).length - 1;
  const delimiter = [',', '\t', ';'].reduce((best, ch) => (count(ch) > count(best) ? ch : best), ',');
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i];
    if (quoted) {
      if (ch === '"' && clean[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && !cell) {
      quoted = true;
    } else if (ch === delimiter) {
      row.push(cell.trim()); cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && clean[i + 1] === '\n') i++;
      row.push(cell.trim()); cell = '';
      rows.push(row); row = [];
    } else {
      cell += ch;
    }
  }
  if (cell || row.length) { row.push(cell.trim()); rows.push(row); }
  return rows;
}

const heading = (cell: string | undefined) => (cell || '').trim().toLowerCase();
const isHeadingRow = (row: string[]) => row.some((c) => NAME_HEADINGS.has(heading(c)) || DOMAIN_HEADINGS.has(heading(c)));

/** A website as a bare domain: no scheme, "www." or path. */
function bareDomain(value: string): string | undefined {
  const domain = value.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/^www\./, '').split(/[/?#\s]/)[0];
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(domain) ? domain : undefined;
}

/** The companies in rows of cells, once each, in order. A first row with a
 *  recognised heading names the columns and is not a company; without one the
 *  first column is the name. */
export function companiesFromRows(rows: string[][]): SheetCompany[] {
  const hasHeading = rows.length > 0 && isHeadingRow(rows[0]);
  const head = hasHeading ? rows[0].map(heading) : [];
  const domainCol = head.findIndex((h) => DOMAIN_HEADINGS.has(h));
  let nameCol = head.findIndex((h) => NAME_HEADINGS.has(h));
  if (nameCol < 0) nameCol = domainCol === 0 ? 1 : 0;
  const seen = new Set<string>();
  const out: SheetCompany[] = [];
  for (const row of hasHeading ? rows.slice(1) : rows) {
    const name = (row[nameCol] || '').trim();
    const key = name.toLowerCase();
    if (!name || seen.has(key)) continue;
    seen.add(key);
    const domain = domainCol >= 0 ? bareDomain(row[domainCol] || '') : undefined;
    out.push(domain ? { name, domain } : { name });
  }
  return out;
}

/** Pasted text as a table of companies, only when it is one: cells separated
 *  by tabs (copied from a spreadsheet) or a first line of headings. Plain
 *  lines - "Meta Platforms, Inc." included - are not a table. */
export function companiesFromPaste(text: string): SheetCompany[] | null {
  const rows = parseDelimited(text);
  if (!rows.length) return null;
  return text.includes('\t') || isHeadingRow(rows[0]) ? companiesFromRows(rows) : null;
}
