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
 * Header layout, left to right: Home (the portal hub), then each app - a lone
 * entry, or a dropdown for an app with several pages. Every destination is
 * reachable from here; the hub adds a card per app, not a second menu.
 *
 * Both shells read this: desktop renders HEADER_LAYOUT (admin follows it),
 * mobile renders MOBILE_PRIMARY_IDS with the drawer covering the rest.
 */
export type NavGroup = { label: string; ids: NavItemId[] };

export const HEADER_LAYOUT: (NavItemId | NavGroup)[] = [
  'home',
  { label: 'Outreach', ids: ['campaigns', 'scraper', 'studio', 'outreach', 'directory'] },
  'surveys',
  'sentiment',
];

export const MOBILE_PRIMARY_IDS: NavItemId[] = ['home', 'campaigns', 'surveys', 'sentiment'];

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
