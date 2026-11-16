import type { ResourceItem, ResourceKind, ResourceQuery, ResourceTeamMember } from '../api';

/** The kinds in the order the filter lists them, with the plural name the filter shows and the singular name a card shows. */
export const RESOURCE_KINDS: { kind: ResourceKind; label: string; singular: string }[] = [
  { kind: 'past_project', label: 'Past projects', singular: 'Past project' },
  { kind: 'template', label: 'Slide templates', singular: 'Slide template' },
  { kind: 'interview', label: 'Interview guides', singular: 'Interview guide' },
  { kind: 'survey', label: 'Survey guides', singular: 'Survey guide' },
  { kind: 'analytics', label: 'Analytics', singular: 'Analytics' },
  { kind: 'other', label: 'Other', singular: 'Other' },
];

const titleCase = (kind: string) => {
  const words = kind.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/** The filter's name for a kind. A kind the page does not know yet is never shown as a raw id. */
export function kindLabel(kind: string): string {
  return RESOURCE_KINDS.find((entry) => entry.kind === kind)?.label ?? titleCase(kind);
}

/** The name a card shows for its own kind. */
export function kindSingular(kind: string): string {
  return RESOURCE_KINDS.find((entry) => entry.kind === kind)?.singular ?? titleCase(kind);
}

/** The kinds the server reports, in the filter's order; unknown kinds follow. */
export function orderKinds(kinds: string[]): string[] {
  const known = RESOURCE_KINDS.map((entry) => entry.kind as string);
  return [...known.filter((kind) => kinds.includes(kind)), ...kinds.filter((kind) => !known.includes(kind))];
}

/** 1536 -> "1.5 KB". */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? Math.round(value) : Math.round(value * 10) / 10} ${units[unit]}`;
}

/** Whole dollars with separators; a project with no revenue on record says so. */
export function formatRevenue(revenue: number | null | undefined): string {
  if (revenue === null || revenue === undefined || !Number.isFinite(revenue)) return 'Not recorded';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(revenue);
}

/** The query string for the list request: only what is set, so the server's defaults apply to the rest. */
export function buildResourceQuery(query: ResourceQuery): string {
  const params = new URLSearchParams();
  const q = query.q?.trim();
  if (q) params.set('q', q);
  if (query.kind) params.set('kind', query.kind);
  if (query.semester) params.set('semester', query.semester);
  if (query.industry) params.set('industry', query.industry);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.offset) params.set('offset', String(query.offset));
  return params.toString();
}

/** What a card shows for the client: the name, a notice when the viewer may not see it, or nothing. */
export function clientText(item: Pick<ResourceItem, 'client' | 'client_hidden'>): string | null {
  if (item.client_hidden) return 'Confidential client';
  return item.client?.trim() || null;
}

/** The line shown when files exist that this viewer cannot open; only the project team and admins can. */
export function hiddenFilesNote(item: Pick<ResourceItem, 'files_open' | 'files_hidden_count'>): string | null {
  const count = item.files_hidden_count;
  if (item.files_open || count <= 0) return null;
  return `${count} ${count === 1 ? 'file' : 'files'} available to the project team`;
}

const TEAM_ROLES: ResourceTeamMember['role'][] = ['Engagement director', 'Project manager', 'Associate'];

/** The team grouped by role, directors first; a role with nobody is left out. */
export function groupTeam(team: ResourceTeamMember[]): { role: string; members: ResourceTeamMember[] }[] {
  const roles = [...TEAM_ROLES, ...team.map((person) => person.role).filter((role) => !TEAM_ROLES.includes(role))];
  return [...new Set(roles)]
    .map((role) => ({ role, members: team.filter((person) => person.role === role) }))
    .filter((group) => group.members.length > 0);
}

/** The admin-only block is drawn only when the server sent those keys at all (it leaves them out for everyone else). */
export function hasAdminDetails(item: ResourceItem): boolean {
  return item.revenue !== undefined || item.contacts !== undefined;
}
