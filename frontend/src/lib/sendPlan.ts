import type { Contact } from '../api';

/**
 * Who a send reaches, counted once: the header, every company's lane and the
 * Send button all read these numbers, so none of them can disagree with
 * another or with what is sent. Each person is in exactly one bucket, in this
 * order of precedence: no address, held, ticked, written to before, left unticked.
 */
export type Tally = {
  /** Everyone listed, including people a search found that could not be added. */
  total: number;
  /** Ticked, reachable and not held: exactly the people Send writes to. */
  canEmail: number;
  noAddress: number;
  /** The server will not take them: a refused import, or a hold the build reported. */
  held: number;
  /** Written to before and not ticked again. */
  written: number;
  /** Reachable and fresh, but the member (or the default tick) left them out. */
  notTicked: number;
};

export function tallyPeople(
  people: Contact[],
  selected: ReadonlySet<number>,
  held: ReadonlyMap<number, string>,
  /** Found people the club's contacts refused (someone else works them). */
  refused = 0,
): Tally {
  const tally: Tally = { total: people.length + refused, canEmail: 0, noAddress: 0, held: refused, written: 0, notTicked: 0 };
  for (const person of people) {
    if (!person.email) tally.noAddress += 1;
    else if (selected.has(person.id) && held.has(person.id)) tally.held += 1;
    else if (selected.has(person.id)) tally.canEmail += 1;
    else if (person.last_sent_at) tally.written += 1;
    else tally.notTicked += 1;
  }
  return tally;
}

/** The people the header's number is made of: ticked, reachable, not held. */
export function recipientsOf(people: Contact[], selected: ReadonlySet<number>, held: ReadonlyMap<number, string>): Contact[] {
  return people.filter((p) => p.email && selected.has(p.id) && !held.has(p.id));
}

/** The reasons for everyone the number leaves out, in the words the page uses. */
export function tallyReasons(tally: Tally): string {
  return [
    tally.noAddress ? `${tally.noAddress} no address` : '',
    tally.written ? `${tally.written} already written to` : '',
    tally.held ? `${tally.held} held` : '',
    tally.notTicked ? `${tally.notTicked} not ticked` : '',
  ].filter(Boolean).join(' · ');
}
