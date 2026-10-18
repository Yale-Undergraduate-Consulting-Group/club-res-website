export const evidenceLabels: Record<string, string> = {
  unreviewed: 'Unreviewed', plausible: 'Plausible', corroborated: 'Corroborated', conflicted: 'Conflicting sources', rejected: 'Rejected',
  current_source_observed: 'Current employment observed', current_inferred: 'Current employment inferred', stale: 'Stale evidence', former: 'Former employee', unknown: 'Unknown',
  published_by_company: 'Published by company', published_by_independent_source: 'Published by independent source', inferred_from_published_pattern: 'Address inferred from published pattern', user_supplied: 'Member supplied', imported_without_evidence: 'Imported without evidence',
  not_checked: 'Mailbox not checked', bad_syntax: 'Invalid address syntax', domain_has_no_mail_route: 'No mail domain route', mail_route_available: 'Mail domain available', provider_high_confidence: 'Mailbox confidence: high', provider_medium_confidence: 'Mailbox confidence: medium', accept_all_or_risky: 'Accept-all or risky mailbox', recipient_rejected: 'Recipient rejected by server', inconclusive: 'Mailbox check inconclusive', previously_delivered: 'Previously delivered', human_reply_observed: 'Human reply observed', permanent_failure_observed: 'Permanent delivery failure observed',
  strong: 'Strong fit', possible: 'Possible fit', weak: 'Weak fit', excluded: 'Excluded', ready_to_review: 'Ready to review', needs_evidence: 'Needs evidence',
  strong_match: 'Strong match', possible_match: 'Possible match', needs_review: 'Needs review',
  queued: 'Queued', running: 'Researching', paused: 'Paused', completed: 'Completed', partially_completed: 'Partially completed', cancelled: 'Cancelled', failed: 'Failed', accepted: 'Accepted',
};
export function evidenceLabel(value?: string | null): string {
  return value ? evidenceLabels[value] || value.replaceAll('_', ' ') : 'Not recorded';
}

export function evidenceTime(value?: string | number | null): string {
  if (!value) return 'Not recorded';
  const date = new Date(typeof value === 'number' ? value * 1000 : /^\d{4}-\d{2}-\d{2} \d/.test(value) ? `${value.replace(' ', 'T')}Z` : value);
  return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date) + ` (${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
}
export function publicSourceUrl(value: string): string | null {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
