export type TrackingEvent = { kind: string; occurred_at: string; detail?: string | null };
export type TrackedMessage = { id: number; sent_at?: string | null; events: TrackingEvent[] };
export type CampaignRecipient = {
  id: number; contact_id: number; name?: string | null; email: string; company?: string | null;
  status: string; sent_at?: string | null; opened_at?: string | null; replied_at?: string | null;
  email_subject?: string | null; email_body?: string | null; last_error?: string | null; messages?: TrackedMessage[];
};
export type TrackingFilter = 'all' | 'sent' | 'opened' | 'replied' | 'bounced' | 'waiting';

export function hasEvent(contact: CampaignRecipient, kind: string): boolean {
  return Boolean(contact.messages?.some(message => message.events.some(event => event.kind === kind)));
}

export function matchesTracking(contact: CampaignRecipient, filter: TrackingFilter): boolean {
  const sent = Boolean(contact.sent_at || contact.messages?.some(message => message.sent_at));
  const replied = Boolean(contact.replied_at || hasEvent(contact, 'replied'));
  const bounced = contact.status === 'bounced' || hasEvent(contact, 'bounced');
  switch (filter) {
    case 'sent': return sent;
    case 'opened': return Boolean(contact.opened_at || hasEvent(contact, 'opened'));
    case 'replied': return replied;
    case 'bounced': return bounced;
    case 'waiting': return sent && !replied && !bounced;
    default: return true;
  }
}

export const eventLabels: Record<string, string> = {
  opened: 'Open detected', replied: 'Reply received', auto_reply: 'Automatic reply',
  bounced: 'Delivery failed', delayed: 'Delivery delayed',
};
