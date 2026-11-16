import type { ResourceFacet, ResourceItem, ResourceKind, ResourceQuery, ResourceTeamMember } from '../api';

/** The kinds in the order the filter lists them, with the plural name the filter and group headers show and the singular name a row shows. */
export const RESOURCE_KINDS: { kind: ResourceKind; label: string; singular: string }[] = [
  { kind: 'past_project', label: 'Past projects', singular: 'Past project' },
  { kind: 'template', label: 'Templates', singular: 'Template' },
  { kind: 'interview', label: 'Interview', singular: 'Interview guide' },
  { kind: 'survey', label: 'Survey', singular: 'Survey guide' },
  { kind: 'analytics', label: 'Analytics', singular: 'Analytics' },
  { kind: 'other', label: 'Other', singular: 'Other' },
];

/** The club's "Public Disclosure?" answers, as the filter and a project's badge name them. */
const DISCLOSURE_LABELS: Record<string, string> = {
  public: 'Deliverable & name',
  name_only: 'Name only',
  private: 'Team only',
};

/** The name of a disclosure value; an unknown one is never shown as a raw id. */
export function disclosureLabel(disclosure: string): string {
  return DISCLOSURE_LABELS[disclosure] ?? titleCase(disclosure);
}

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
  if (query.disclosure) params.set('disclosure', query.disclosure);
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

/** The link button's label. */
export function linkLabel(provider: string): string {
  return provider === 'google_drive' ? 'Open in Google Drive' : 'Open in SharePoint';
}

/** A facet's values with the chosen one kept on offer (at 0) even when nothing else now matches it. */
export function facetOptions(values: ResourceFacet[], chosen: string): ResourceFacet[] {
  if (!chosen || values.some((entry) => entry.value === chosen)) return values;
  return [{ value: chosen, count: 0 }, ...values];
}

/** What an admin's link field starts with: the address that opens now, else a saved address (a SharePoint path waiting for its setting is not one). */
export function editableLink(item: Pick<ResourceItem, 'link' | 'stored_link'>): string {
  return item.link?.url ?? (item.stored_link?.startsWith('https://') ? item.stored_link : '');
}

const SEASONS: Record<string, number> = { spring: 1, summer: 2, fall: 3 };

/** "Fall 2025" -> 2025 * 10 + 3, so later semesters sort first; anything else is 0. */
export function semesterRank(semester: string): number {
  const match = /^\s*(spring|summer|fall)\s+(\d{4})/i.exec(semester);
  return match ? Number(match[2]) * 10 + SEASONS[match[1].toLowerCase()] : 0;
}

export type ResourceGroup = { key: string; label: string; items: ResourceItem[] };

/** The list's sections: past projects by semester, newest first; everything else by type, in the filter's order.
 *  Within a section the server's order (newest, or best match) is kept. */
export function groupResources(items: ResourceItem[]): ResourceGroup[] {
  const groups = new Map<string, ResourceGroup>();
  for (const item of items) {
    const bySemester = item.kind === 'past_project' && Boolean(item.semester);
    const key = bySemester ? `semester:${item.semester}` : `kind:${item.kind}`;
    const label = bySemester ? item.semester! : kindLabel(item.kind);
    if (!groups.has(key)) groups.set(key, { key, label, items: [] });
    groups.get(key)!.items.push(item);
  }
  const kinds = orderKinds([...new Set(items.map((item) => item.kind))]);
  const rank = (group: ResourceGroup): [number, number] => (group.key.startsWith('semester:')
    ? [0, -semesterRank(group.label)]
    : [1, kinds.indexOf(group.key.slice('kind:'.length))]);
  return [...groups.values()].sort((a, b) => {
    const [ga, ra] = rank(a);
    const [gb, rb] = rank(b);
    return ga - gb || ra - rb || a.label.localeCompare(b.label);
  });
}

/** How many files a row says it has: the ones the viewer may open and the ones kept for the team. */
export function fileCountText(item: Pick<ResourceItem, 'files' | 'files_hidden_count'>): string | null {
  const count = item.files.length + item.files_hidden_count;
  return count > 0 ? `${count} ${count === 1 ? 'file' : 'files'}` : null;
}
