import { describe, expect, it } from 'vitest';
import { publicSourceUrl } from './contactEvidence';

describe('publicSourceUrl', () => {
  it('normalises web links that may be rendered as hrefs', () => {
    expect(publicSourceUrl('https://example.com/team?a=1')).toBe('https://example.com/team?a=1');
    expect(publicSourceUrl('HTTP://Example.com')).toBe('http://example.com/');
  });

  it.each(['javascript:alert(1)', ' javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x', 'file:///etc/passwd', '/relative', 'not a url', ''])(
    'refuses %j', (value) => expect(publicSourceUrl(value)).toBeNull(),
  );
});
