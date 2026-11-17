import { describe, expect, it, vi } from 'vitest';
import { browserTimeZone, formatEventTime, linkedinProblem, onlineLinkProblem, timeZoneNames } from './alumniPortal';

// 2026-12-03 23:00 UTC
const STARTS_AT = Date.UTC(2026, 11, 3, 23, 0) / 1000;

describe('formatEventTime', () => {
  it("shows the time in the event's own zone and names the zone", () => {
    const text = formatEventTime(STARTS_AT, 'America/New_York', 'en-US');
    expect(text).toContain('6:00');
    expect(text).toContain('EST');
    expect(text).toContain('Dec');
  });

  it('does not depend on the reader’s zone', () => {
    const tokyo = formatEventTime(STARTS_AT, 'Asia/Tokyo', 'en-US');
    expect(tokyo).toContain('Dec 4');
    expect(tokyo).toContain('8:00');
  });

  it('falls back to UTC and says which zone was asked for when the zone is unknown', () => {
    const text = formatEventTime(STARTS_AT, 'Mars/Olympus', 'en-US');
    expect(text).toContain('UTC');
    expect(text).toContain('Mars/Olympus');
  });
});

describe('linkedinProblem', () => {
  it.each([
    '', '   ', 'https://www.linkedin.com/in/jane-doe', 'https://linkedin.com/in/jane', 'https://uk.linkedin.com/in/x', 'HTTPS://WWW.LINKEDIN.COM/in/x',
  ])('accepts %j', (value) => expect(linkedinProblem(value)).toBeNull());

  it.each([
    'http://www.linkedin.com/in/jane', 'https://notlinkedin.com/in/jane', 'https://linkedin.com.evil.example/in/jane',
    'www.linkedin.com/in/jane', 'javascript:alert(1)', 'https://www.linkedin.com/in/ja ne', 'https://',
  ])('refuses %j', (value) => expect(linkedinProblem(value)).toMatch(/https:\/\/ link on linkedin\.com/));
});

describe('onlineLinkProblem', () => {
  it('needs https on any host', () => {
    expect(onlineLinkProblem('https://meet.example.com/abc')).toBeNull();
    expect(onlineLinkProblem('')).toBeNull();
    expect(onlineLinkProblem('http://meet.example.com')).toMatch(/https:\/\/ link/);
  });
});

describe('time zones', () => {
  it('has a browser zone, and lists it with the zones asked for', () => {
    const own = browserTimeZone();
    expect(own).toBeTruthy();
    const names = timeZoneNames([own, 'Europe/Zurich']);
    expect(names).toContain(own);
    expect(names).toContain('Europe/Zurich');
    expect(names).toContain('UTC');
    expect(new Set(names).size).toBe(names.length);
  });
});

describe('time zone fallback', () => {
  it('uses a practical list when supportedValuesOf is unavailable', () => {
    const supported = vi.spyOn(Intl, 'supportedValuesOf').mockImplementation(() => { throw new Error('unsupported'); });
    try {
      const names = timeZoneNames();
      expect(names).toContain('America/New_York');
      expect(names).toContain('Europe/London');
      expect(names).toContain('Asia/Tokyo');
      expect(names).toContain('UTC');
    } finally {
      supported.mockRestore();
    }
  });
});
