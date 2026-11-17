import { describe, expect, it } from 'vitest';
import { showsDevSignIn } from './devSignIn';

describe('showsDevSignIn', () => {
  it.each(['localhost', '127.0.0.1', '[::1]'])('a built app on %s offers them', (host) => {
    expect(showsDevSignIn(host, false)).toBe(true);
  });
  it.each(['club.example.org', 'd1234.cloudfront.net', 'localhost.example.org', '127.0.0.1.example.org'])(
    'a built app on %s does not',
    (host) => expect(showsDevSignIn(host, false)).toBe(false),
  );
  it('a dev server always does', () => expect(showsDevSignIn('club.example.org', true)).toBe(true));
});
