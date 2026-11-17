import type { SendWarning } from '../api';
import { companyKey } from './recipients';

/**
 * What the Send panel checks before it sends: each company's line for the
 * club's Intro, and the warnings (never blocks) about writing to many people at
 * one company. Pure, so the gating is tested without a page.
 */

/** The member's line for one company: what fills {personalisation} there. */
export type CompanyLine = {
  text: string;
  /** Confirmed as it stands; an empty confirmed line sends without one. */
  confirmed: boolean;
  suggesting?: boolean;
  error?: string;
  /** What a suggestion was written from, so the member can judge it. */
  sources?: string[];
};

export const PERSONALISATION = '{personalisation}';

export function usesPersonalisation(text: string): boolean {
  return text.includes(PERSONALISATION);
}

/** The companies a send writes to, once each, in the order the people come. */
export function companiesOf(people: { company?: string | null }[]): { key: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const person of people) {
    const key = companyKey(person.company);
    if (key && !seen.has(key)) seen.set(key, (person.company || '').trim());
  }
  return [...seen].map(([key, name]) => ({ key, name }));
}

/** Companies whose line is not yet confirmed; Send waits for none. */
export function unconfirmed(companies: { key: string }[], lines: Record<string, CompanyLine>): number {
  return companies.filter((c) => !lines[c.key]?.confirmed).length;
}

/** The lines to send, keyed by company name as the server groups people. */
export function personalisationFor(companies: { key: string; name: string }[], lines: Record<string, CompanyLine>): Record<string, string> {
  return Object.fromEntries(companies.map((c) => [c.name, (lines[c.key]?.text || '').trim()]));
}

/** The server's warnings, re-counted against who would be sent to now: a
 *  warning about people since unticked or held goes as soon as they do. */
export function liveWarnings(warnings: SendWarning[], recipientIds: ReadonlySet<number>): SendWarning[] {
  return warnings.flatMap((w) => {
    const ids = w.contact_ids.filter((id) => recipientIds.has(id));
    return ids.length >= w.threshold ? [{ ...w, contact_ids: ids, count: ids.length }] : [];
  });
}

/** Who "Keep the most senior N" unticks: everyone after the first N (most senior first). */
export function beyondMostSenior(warning: SendWarning): number[] {
  return warning.contact_ids.slice(warning.keep ?? 2);
}

export function warningText(w: SendWarning): string {
  return w.kind === 'team'
    ? `You're emailing ${w.count} people on the ${w.team} team at ${w.company} — they often compare notes; consider 1–2.`
    : `You're emailing ${w.count} people at ${w.company} — several cold emails to one company read as spam there; consider fewer.`;
}
