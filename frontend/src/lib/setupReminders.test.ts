import { describe, expect, it } from 'vitest';
import type { ReminderMember } from '../api';
import { defaultSelection, remindedLabel, resultLabel } from './setupReminders';

const member = (user_id: number, reminded_at: string | null): ReminderMember => ({
  user_id, name: `M${user_id}`, email: `m${user_id}@yale.edu`, missing: [{ key: 'gmail', label: 'Gmail' }], reminded_at,
});

describe('setup reminders', () => {
  it('unticks members reminded in the last week, ticks the rest', () => {
    const picked = defaultSelection([member(1, null), member(2, '2026-11-15 09:30:00'), member(3, null)]);
    expect([...picked]).toEqual([1, 3]);
  });

  it('shows the reminder date, not the time', () => {
    expect(remindedLabel('2026-11-15 09:30:00')).toBe('reminded 2026-11-15');
  });

  it.each([
    [{ status: 'sent', error: null }, 'success', 'Sent'],
    [{ status: 'not_found', error: 'users_not_found' }, 'warning', 'Not found in Slack'],
    [{ status: 'failed', error: 'account_inactive' }, 'danger', 'Failed (account_inactive)'],
    [{ status: 'failed', error: null }, 'danger', 'Failed (unknown_error)'],
    [{ status: 'skipped', error: null }, 'neutral', 'Nothing missing any more'],
  ] as const)('%o reads as %s "%s"', (result, tone, text) => {
    expect(resultLabel(result)).toEqual({ tone, text });
  });
});
