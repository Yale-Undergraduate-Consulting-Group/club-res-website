import { describe, expect, it } from 'vitest';
import { evidenceLabel, publicSourceUrl } from './contactEvidence';

describe('evidenceLabel', () => {
  it.each([
    ['previously_delivered', 'Delivered before'],
    ['human_reply_observed', 'Replied'],
    ['published_by_company', 'Seen on the web'],
    ['inferred_from_published_pattern', 'Format guessed'],
    ['accept_all_or_risky', 'Catch-all domain'],
    ['provider_high_confidence', 'Mailbox confirmed'],
    ['permanent_failure_observed', 'Bounced'],
    ['mail_route_available', 'Domain accepts mail'],
    ['likely_valid', 'Domain accepts mail'],
    ['web_discovery_pattern', 'Format guessed'],
  ])('maps %s to honest member wording', (value, label) => {
    expect(evidenceLabel(value)).toBe(label);
  });
});

describe('publicSourceUrl', () => {
  it('normalises web links that may be rendered as hrefs', () => {
    expect(publicSourceUrl('https://example.com/team?a=1')).toBe('https://example.com/team?a=1');
    expect(publicSourceUrl('HTTP://Example.com')).toBe('http://example.com/');
  });

  it.each(['javascript:alert(1)', ' javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x', 'file:///etc/passwd', '/relative', 'not a url', ''])(
    'refuses %j', (value) => expect(publicSourceUrl(value)).toBeNull(),
  );
});
