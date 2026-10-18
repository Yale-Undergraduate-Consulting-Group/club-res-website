/** Presentation guard only. The API independently enforces authorization. */
export function canManageCampaign(campaign: { owner_user_id?: number | null; sender_user_id?: number | null }, userId?: number): boolean {
  return Number.isInteger(userId) && campaign.owner_user_id === userId && campaign.sender_user_id === userId;
}
export function campaignAccessLabel(campaign: { owner_user_id?: number | null; sender_user_id?: number | null }, userId?: number): string {
  if (!campaign.owner_user_id || !campaign.sender_user_id) return 'Ownership needs review';
  return canManageCampaign(campaign, userId) ? 'Your campaign' : 'Shared activity · read only';
}
