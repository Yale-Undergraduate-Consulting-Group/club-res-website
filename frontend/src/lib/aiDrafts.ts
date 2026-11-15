import { api, type Contact } from '../api';
import { aiDraftingUnavailable } from './aiDrafting';

/**
 * Writing an advisory email to each person, several at a time. One place for
 * the loop Drafts and the Find contacts screen both run, so they retry, stop
 * and report the same way.
 */

/** Drafts in flight at once. The server runs two model calls for the whole club
 *  and holds a third for up to 20 seconds before answering 429, which the retry
 *  below waits out - so four in flight keeps both slots full without a draft
 *  ever being abandoned for being early. */
export const DRAFT_WORKERS = 4;
/** Waits before retrying a person the server was too busy to draft. */
const BACKOFF_MS = [3000, 6000, 12000, 12000];
const TRANSIENT = /busy|unavailable|please retry shortly/i;
const BUDGET_SPENT = /budget for this month is used up/i;

export const BUDGET_NOTE = "The club's AI budget for this month is used up. Everything drafted so far is saved; an administrator can raise the budget, or the rest can be drafted next month.";

export type DraftStatus = 'waiting' | 'drafting' | 'done' | 'failed';

export type DraftEvent =
  | { id: number; status: 'waiting' | 'drafting' }
  | { id: number; status: 'done'; email: { subject: string; body: string } }
  | { id: number; status: 'failed'; reason: string };

export async function draftEach({ people, tone, model, valueProp = '', goal = '', stop, onEvent, onUnavailable, onBudgetSpent }: {
  people: Contact[];
  tone: string;
  model?: string;
  /** What the member typed as proof, if anything. */
  valueProp?: string;
  /** What the member typed as the email's purpose, if anything. */
  goal?: string;
  /** Set `current` to stop starting new drafts; the ones in flight finish. */
  stop: { current: boolean };
  onEvent: (event: DraftEvent) => void;
  /** The model is off for everyone: the rest would fail the same way. */
  onUnavailable: () => void;
  onBudgetSpent: () => void;
}): Promise<void> {
  // One citation lookup per company, not per person: it only ever returns
  // projects an admin marked discussable, so it is safe to reuse.
  const citations = new Map<string, Promise<string>>();
  const citeFor = (company: string) => {
    if (!citations.has(company)) {
      citations.set(company, api.projects.suggestCitations(company).then((res) => {
        const clients = (res.projects || []).map((p) => p.client_name).filter(Boolean);
        return clients.length ? `Past clients we can discuss: ${clients.join(', ')}.` : '';
      }).catch(() => ''));
    }
    return citations.get(company)!;
  };
  const queue = [...people];
  const work = async () => {
    for (let person = queue.shift(); person && !stop.current; person = queue.shift()) {
      const id = person.id;
      onEvent({ id, status: 'drafting' });
      try {
        const cite = person.company ? await citeFor(person.company) : '';
        const request = () => api.emails.generate({
          contact_id: id,
          angle: 'advisory',
          // Two or three proposals need the room; "short" cannot hold a list.
          length: 'standard',
          tone,
          model,
          value_proposition: [valueProp.trim(), cite].filter(Boolean).join('\n\n') || undefined,
          custom_instructions: goal.trim() ? `Email purpose: ${goal.trim()}` : undefined,
        });
        // A busy or throttled model is not a failed draft: wait and ask
        // again. Nothing is charged when the model was never reached.
        let email: { subject: string; body: string };
        for (let attempt = 0; ; attempt += 1) {
          try {
            email = await request();
            break;
          } catch (e) {
            const message = e instanceof Error ? e.message : '';
            if (aiDraftingUnavailable(e) || !TRANSIENT.test(message) || attempt >= BACKOFF_MS.length || stop.current) throw e;
            await new Promise((resolve) => window.setTimeout(resolve, BACKOFF_MS[attempt]));
          }
        }
        onEvent({ id, status: 'done', email: { subject: email.subject, body: email.body } });
      } catch (e) {
        const message = e instanceof Error ? e.message : 'Draft failed';
        if (aiDraftingUnavailable(e)) {
          // Stop, and let the caller offer the way that needs no model.
          stop.current = true;
          onEvent({ id, status: 'waiting' });
          onUnavailable();
          continue;
        }
        // A spent monthly budget fails every later request too: stop rather
        // than mark the whole remainder failed.
        if (BUDGET_SPENT.test(message)) {
          stop.current = true;
          onEvent({ id, status: 'waiting' });
          onBudgetSpent();
          continue;
        }
        onEvent({ id, status: 'failed', reason: message });
      }
    }
  };
  await Promise.all(Array.from({ length: DRAFT_WORKERS }, work));
}
