import { useMemo, useState } from 'react';
import { ApiError, api } from '../api';
import { personalisationFor, unconfirmed, type CompanyLine } from './sendChecks';

/** Suggestions asked for at once: the server runs two model calls for the whole club. */
const SUGGEST_WORKERS = 2;

/** The model is off, or the month's budget is spent: every later suggestion would fail the same way. */
const stopsEverySuggestion = (e: unknown) => e instanceof ApiError && (e.status === 503 || (e.status === 429 && /budget/i.test(e.message)));

export type Company = { key: string; name: string };

/** Each company's line, and what sending needs from them. */
export type CompanyLines = {
  /** Whether the message uses {personalisation}; without it nothing is asked. */
  active: boolean;
  companies: Company[];
  lines: Record<string, CompanyLine>;
  setLine: (key: string, change: Partial<CompanyLine>) => void;
  /** One suggestion; false when the rest would fail the same way. */
  suggest: (company: Company) => Promise<boolean>;
  /** A suggestion for every company with no line yet. */
  suggestAll: () => Promise<void>;
  unwritten: number;
  /** Companies whose line is not confirmed yet; sending waits for none. */
  pending: number;
  suggesting: boolean;
  /** What /build is given: each company's line by name, or nothing when the message has no {personalisation}. */
  personalisation: Record<string, string> | undefined;
};

/**
 * The club's Intro leaves room for one sentence about each company. The member
 * asks for a suggestion (one model call per company, from that company's
 * material only: backend/app/services/personalisation.py) or writes it, and
 * confirms it; an empty confirmed line sends without one, and a line the
 * member writes makes no model call. One place for the Send panel and the
 * Queue panel, so both gate sending the same way.
 *
 * `active` is whether the message uses {personalisation}; without it nothing
 * is asked of the member and nothing is sent.
 */
export function useCompanyLines(companies: Company[], active: boolean): CompanyLines {
  const [lines, setLines] = useState<Record<string, CompanyLine>>({});
  const setLine = (key: string, change: Partial<CompanyLine>) =>
    setLines((all) => ({ ...all, [key]: { ...(all[key] ?? { text: '', confirmed: false }), ...change } }));

  const suggest = async (company: Company): Promise<boolean> => {
    const previous = (lines[company.key]?.text || '').trim();
    setLine(company.key, { suggesting: true, error: undefined, confirmed: false });
    try {
      const answer = await api.campaigns.personalisation(company.name, previous);
      setLine(company.key, { text: answer.line, sources: answer.sources, suggesting: false });
      return true;
    } catch (e) {
      setLine(company.key, { suggesting: false, error: e instanceof Error && e.message ? e.message : 'Could not suggest a line' });
      return !stopsEverySuggestion(e);
    }
  };

  const unwrittenCompanies = companies.filter((c) => !lines[c.key]?.confirmed && !(lines[c.key]?.text || '').trim());
  const suggestAll = async () => {
    const queue = [...unwrittenCompanies];
    let carryOn = true;
    const work = async () => {
      for (let next = queue.shift(); next && carryOn; next = queue.shift()) carryOn = await suggest(next);
    };
    await Promise.all(Array.from({ length: SUGGEST_WORKERS }, work));
  };

  const personalisation = useMemo(
    () => (active ? personalisationFor(companies, lines) : undefined),
    [active, companies, lines],
  );
  return {
    active,
    companies,
    lines,
    setLine,
    suggest,
    suggestAll,
    unwritten: unwrittenCompanies.length,
    pending: active ? unconfirmed(companies, lines) : 0,
    suggesting: companies.some((c) => lines[c.key]?.suggesting),
    personalisation,
  };
}
