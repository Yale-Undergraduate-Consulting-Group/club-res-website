// How the company register reads to a member: plain words for kinds, one line
// for where a company is, one line per record in its log.

/** What a company is, as the small badge on its row. */
export const KIND_LABELS: Record<string, string> = {
  us_nonprofit: 'Nonprofit',
  us_employer: 'US employer',
  us_private: 'Recently funded',
  us_public: 'US listed',
  uk: 'UK',
  club_targets: 'Club target',
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, ' ');
}

const COUNTRY_NAMES: Record<string, string> = { US: 'US', GB: 'UK' };

/** "TN", "5 states", "High Wycombe, UK", "3 places, UK": where the company's records say it is. */
export function locationText(country: string, locations: string[]): string {
  const name = COUNTRY_NAMES[country] ?? country;
  const places = locations.filter(Boolean);
  if (country === 'US') {
    if (places.length === 0) return name;
    return places.length === 1 ? places[0] : `${places.length} states`;
  }
  if (places.length === 0) return name;
  return places.length === 1 ? `${places[0]}, ${name}` : `${places.length} places, ${name}`;
}

export function peopleText(count: number): string {
  return `${count.toLocaleString()} named ${count === 1 ? 'person' : 'people'}`;
}

/** The tiers' counts as the summary line reads them: companies, not rows. */
export function summaryLine(
  summary: { companies?: number; with_people?: number; tiers: { tier: string; n: number }[] },
): string {
  const counts = Object.fromEntries(summary.tiers.map((row) => [row.tier, row.n]));
  const n = (tier: string) => (counts[tier] || 0).toLocaleString();
  const parts = [
    `${(summary.companies || 0).toLocaleString()} companies`,
    `${n('us_public')} US listed`,
    `${n('us_employer')} US employers`,
    `${n('us_nonprofit')} nonprofits`,
    `${n('us_private')} recently funded`,
  ];
  if (counts.uk) parts.push(`${n('uk')} UK`);
  parts.push(`${(summary.with_people || 0).toLocaleString()} with named people`);
  return parts.join(' · ');
}
