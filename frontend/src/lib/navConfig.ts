/**
 * Single source of truth for app navigation.
 * Update here when adding routes — shells (desktop + mobile) read from this module.
 */
export type NavItemId =
  | 'dashboard'
  | 'scraper'
  | 'studio'
  | 'analytics'
  | 'outreach'
  | 'admin';

export type NavItem = {
  id: NavItemId;
  to: string;
  label: string;
  /** Shown under glyphs on mobile bottom nav */
  shortLabel: string;
};

export const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', to: '/', label: 'Home', shortLabel: 'Home' },
  { id: 'studio', to: '/studio', label: 'Drafts', shortLabel: 'Drafts' },
  { id: 'outreach', to: '/outreach', label: 'Pipeline', shortLabel: 'Pipeline' },
  { id: 'scraper', to: '/scraper', label: 'Find contacts', shortLabel: 'Contacts' },
];

export const ADMIN_NAV_ITEM: NavItem = {
  id: 'admin',
  to: '/admin',
  label: 'Admin',
  shortLabel: 'Admin',
};

/**
 * Header layout. Every destination is reachable from TOP_LEVEL_IDS plus the
 * two NAV_GROUPS dropdowns - Home does not need to mirror this list with its
 * own tile grid. Home instead surfaces live work state (needs attention,
 * pipeline snapshot, leaderboard) that links into these same destinations.
 *
 * Both shells read this: desktop renders TOP_LEVEL_IDS then NAV_GROUPS, mobile
 * renders MOBILE_PRIMARY_IDS with the drawer covering the rest.
 */
export const TOP_LEVEL_IDS: NavItemId[] = ['dashboard', 'scraper'];

export const NAV_GROUPS: { label: string; ids: NavItemId[] }[] = [
  { label: 'Outreach', ids: ['studio', 'outreach'] },
];

export const MOBILE_PRIMARY_IDS: NavItemId[] = ['dashboard', 'scraper', 'studio'];

export function getNavItems(isAdmin: boolean): NavItem[] {
  return isAdmin ? [...NAV_ITEMS, ADMIN_NAV_ITEM] : NAV_ITEMS;
}

/** Match active route for nav highlighting */
export function isNavItemActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}
