import { MODULES, type AppNavItem } from '../modules';

/**
 * Navigation layout. The items come from the apps' manifests (src/modules);
 * this file only decides where they sit. Shells (desktop + mobile) read from
 * this module.
 */
export type NavItem = AppNavItem;
export type NavItemId = string;

const ALL_ITEMS: NavItem[] = MODULES.flatMap((module) => module.nav);

export const NAV_ITEMS: NavItem[] = ALL_ITEMS.filter((item) => !item.adminOnly);

export const ADMIN_NAV_ITEMS: NavItem[] = ALL_ITEMS.filter((item) => item.adminOnly);

/**
 * Header layout. Every destination is reachable from TOP_LEVEL_IDS plus the
 * NAV_GROUPS dropdowns - Home does not need to mirror this list with its
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
  return isAdmin ? [...NAV_ITEMS, ...ADMIN_NAV_ITEMS] : NAV_ITEMS;
}

/** Match active route for nav highlighting */
export function isNavItemActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}
