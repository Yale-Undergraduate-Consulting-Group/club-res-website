/** Pure helpers for the alumni portal (components/alumni, pages/AlumniHome). */

const EVENT_FORMATTERS = new Map<string, Intl.DateTimeFormat>();
const EVENT_TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
};

/** An event's time as written where it happens, with the zone named, e.g. "Thu, Dec 3, 2026, 6:00 PM EST". */
export function formatEventTime(startsAt: number, timezone: string, locale?: string): string {
  const options = EVENT_TIME_OPTIONS;
  try {
    const key = `${locale ?? ''}\u0000${timezone}`;
    let formatter = EVENT_FORMATTERS.get(key);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat(locale, { ...options, timeZone: timezone, timeZoneName: 'short' });
      EVENT_FORMATTERS.set(key, formatter);
    }
    return formatter.format(startsAt * 1000);
  } catch {
    // An unknown zone name: say so rather than show a time in the wrong zone.
    const key = `${locale ?? ''}\u0000UTC`;
    let formatter = EVENT_FORMATTERS.get(key);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' });
      EVENT_FORMATTERS.set(key, formatter);
    }
    return `${formatter.format(startsAt * 1000)} UTC (${timezone})`;
  }
}

/** Why `value` is not an acceptable https link, or null. Mirrors the server (alumni_portal._https_url). */
export function httpsLinkProblem(value: string, label: string, hosts: readonly string[] = []): string | null {
  const text = value.trim();
  if (!text) return null;
  const message = `${label} must be an https:// link${hosts.length ? ` on ${hosts[0]}` : ''}`;
  if (/\s/.test(text)) return message;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return message;
  }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || !host) return message;
  if (hosts.length && !hosts.some((h) => host === h || host.endsWith(`.${h}`))) return message;
  return null;
}

export function linkedinProblem(value: string): string | null {
  return httpsLinkProblem(value, 'The LinkedIn link', ['linkedin.com']);
}

export function onlineLinkProblem(value: string): string | null {
  return httpsLinkProblem(value, 'The online link');
}

const FALLBACK_ZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu',
  'America/Toronto', 'America/Sao_Paulo', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Africa/Johannesburg',
  'Asia/Dubai', 'Asia/Kolkata', 'Asia/Singapore', 'Asia/Hong_Kong', 'Asia/Shanghai', 'Asia/Tokyo', 'Australia/Sydney', 'UTC',
];

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Every IANA zone the browser knows (a short list where it cannot say), with `include` kept present. */
export function timeZoneNames(include: readonly string[] = []): string[] {
  let names: string[];
  try {
    names = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : FALLBACK_ZONES;
  } catch {
    names = FALLBACK_ZONES;
  }
  const all = new Set(names);
  all.add('UTC');
  for (const name of include) if (name) all.add(name);
  return [...all].sort((a, b) => a.localeCompare(b));
}
