import { describe, expect, it } from 'vitest';
import { canManageCampaign } from './campaignAccess';

describe('canManageCampaign', () => {
  it('requires the member to be both owner and sender', () => {
    expect(canManageCampaign({ owner_user_id: 5, sender_user_id: 5 }, 5)).toBe(true);
    expect(canManageCampaign({ owner_user_id: 5, sender_user_id: 6 }, 5)).toBe(false);
    expect(canManageCampaign({ owner_user_id: 6, sender_user_id: 5 }, 5)).toBe(false);
  });

  it('never matches a signed-out member against an unowned campaign', () => {
    expect(canManageCampaign({}, undefined)).toBe(false);
    expect(canManageCampaign({ owner_user_id: null, sender_user_id: null }, undefined)).toBe(false);
  });
});
