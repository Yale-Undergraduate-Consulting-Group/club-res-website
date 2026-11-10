export const evidenceLabels: Record<string, string> = {
  unreviewed: 'Unreviewed', plausible: 'Plausible', corroborated: 'Corroborated', conflicted: 'Conflicting sources', rejected: 'Rejected',
  current_source_observed: 'Current employment observed', current_inferred: 'Current employment inferred', stale: 'Stale evidence', former: 'Former employee', unknown: 'Unknown',
  published_by_company: 'Seen on the web', published_by_independent_source: 'Seen on the web', inferred_from_published_pattern: 'Format guessed', user_supplied: 'Member supplied', imported_without_evidence: 'No address evidence',
  not_checked: 'Mailbox not checked', bad_syntax: 'Invalid address', domain_has_no_mail_route: 'No mail route', mail_route_available: 'Domain accepts mail', provider_high_confidence: 'Mailbox confirmed', provider_medium_confidence: 'Mailbox confirmed', accept_all_or_risky: 'Catch-all domain', recipient_rejected: 'Mailbox rejected', inconclusive: 'Mailbox unconfirmed', previously_delivered: 'Delivered before', human_reply_observed: 'Replied', permanent_failure_observed: 'Bounced',
  likely_valid: 'Domain accepts mail', valid: 'Domain accepts mail', dead: 'Bounced', bounced: 'Bounced',
  domain_scrape: 'Seen on the web', web_discovery: 'Seen on the web', inferred: 'Format guessed', pattern_inferred: 'Format guessed', inferred_pattern: 'Format guessed',
  strong: 'Strong fit', possible: 'Possible fit', weak: 'Weak fit', excluded: 'Excluded', ready_to_review: 'Ready to review', needs_evidence: 'Needs evidence',
  strong_match: 'Strong match', possible_match: 'Possible match', needs_review: 'Needs review',
  queued: 'Queued', running: 'Researching', paused: 'Paused', completed: 'Completed', partially_completed: 'Partially completed', cancelled: 'Cancelled', failed: 'Failed', accepted: 'Accepted',
};
export function evidenceLabel(value?: string | null): string {
  if (!value) return 'Not recorded';
  if (value.endsWith('_pattern')) return 'Format guessed';
  return evidenceLabels[value] || value.replaceAll('_', ' ');
}

export function evidenceTime(value?: string | number | null): string {
  if (!value) return 'Not recorded';
  const date = new Date(typeof value === 'number' ? value * 1000 : /^\d{4}-\d{2}-\d{2} \d/.test(value) ? `${value.replace(' ', 'T')}Z` : value);
  return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date) + ` (${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
}
export function publicSourceUrl(value: string): string | null {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
