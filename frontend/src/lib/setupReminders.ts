import type { ReminderMember, ReminderResult } from '../api';

/** Members to tick when the dialog opens: everyone except those already reminded this week. */
export function defaultSelection(members: ReminderMember[]): Set<number> {
  return new Set(members.filter((m) => !m.reminded_at).map((m) => m.user_id));
}

/** "reminded 2026-11-15" from the server's UTC stamp. */
export function remindedLabel(stamp: string): string {
  return `reminded ${stamp.slice(0, 10)}`;
}

/** What the admin reads for one member's outcome; never a success the server did not report. */
export function resultLabel(result: Pick<ReminderResult, 'status' | 'error'>): { tone: 'success' | 'warning' | 'danger' | 'neutral'; text: string } {
  switch (result.status) {
    case 'sent': return { tone: 'success', text: 'Sent' };
    case 'not_found': return { tone: 'warning', text: 'Not found in Slack' };
    case 'skipped': return { tone: 'neutral', text: 'Nothing missing any more' };
    default: return { tone: 'danger', text: `Failed (${result.error || 'unknown_error'})` };
  }
}
