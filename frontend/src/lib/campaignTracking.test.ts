import { describe, expect, it } from 'vitest';
import { matchesTracking, type CampaignRecipient, type TrackingFilter } from './campaignTracking';

const recipient = (fields: Partial<CampaignRecipient> = {}): CampaignRecipient => ({
  id: 1, contact_id: 1, email: 'p@example.com', status: 'sent', ...fields,
});
const message = (...kinds: string[]) => ({
  id: 1, sent_at: '2026-09-01T10:00:00Z', events: kinds.map((kind) => ({ kind, occurred_at: '2026-09-02T10:00:00Z' })),
});
const filters: TrackingFilter[] = ['all', 'sent', 'opened', 'replied', 'bounced', 'waiting'];
const matching = (contact: CampaignRecipient) => filters.filter((filter) => matchesTracking(contact, filter));

describe('matchesTracking', () => {
  it('shows an unsent recipient only under all', () => {
    expect(matching(recipient({ status: 'draft' }))).toEqual(['all']);
  });

  it('counts a message send as sent and waiting', () => {
    expect(matching(recipient({ messages: [message()] }))).toEqual(['all', 'sent', 'waiting']);
    expect(matching(recipient({ sent_at: '2026-09-01 10:00:00' }))).toEqual(['all', 'sent', 'waiting']);
  });

  it('stops waiting once a reply or bounce is recorded', () => {
    expect(matching(recipient({ messages: [message('opened', 'replied')] }))).toEqual(['all', 'sent', 'opened', 'replied']);
    expect(matching(recipient({ sent_at: '2026-09-01 10:00:00', replied_at: '2026-09-02 10:00:00' }))).toEqual(['all', 'sent', 'replied']);
    expect(matching(recipient({ messages: [message('bounced')] }))).toEqual(['all', 'sent', 'bounced']);
    expect(matching(recipient({ status: 'bounced', sent_at: '2026-09-01 10:00:00' }))).toEqual(['all', 'sent', 'bounced']);
  });

  it('keeps an opened but unanswered recipient waiting', () => {
    expect(matching(recipient({ messages: [message('opened')] }))).toEqual(['all', 'sent', 'opened', 'waiting']);
  });
});
