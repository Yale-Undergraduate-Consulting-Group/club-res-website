import { MODULES, usableModules, type AppNavItem } from '../modules';

/**
 * Navigation layout. The items come from the apps' manifests (src/modules);
 * this file only decides where they sit. Shells (desktop + mobile) read from
 * this module.
 */
export type NavItem = AppNavItem;
export type NavItemId = string;

const navOf = (modules: typeof MODULES): NavItem[] => modules.flatMap((module) => module.nav);

export const NAV_ITEMS: NavItem[] = navOf(MODULES).filter((item) => !item.adminOnly);

export const ADMIN_NAV_ITEMS: NavItem[] = navOf(MODULES).filter((item) => item.adminOnly);

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

/** The menu for this member: the apps they may use, and admin pages for admins. */
export function getNavItems(isAdmin: boolean, apps?: string[]): NavItem[] {
  const items = navOf(usableModules(apps));
  return items.filter((item) => !item.adminOnly || isAdmin);
}

/** Match active route for nav highlighting */
export function isNavItemActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}
