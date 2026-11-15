/**
 * What to call a member: the first word of their Google name, or, for an
 * account whose name is missing or just its address ("andre.h.costa"), the
 * first part of that address, capitalised ("Andre").
 */
export function memberFirstName(user: { name?: string; email: string }): string {
  const local = user.email.split('@')[0];
  const name = user.name?.trim();
  if (name && name !== local) return name.split(/\s+/)[0];
  const part = local.split(/[._+-]/)[0] || local;
  return part.charAt(0).toUpperCase() + part.slice(1);
}
