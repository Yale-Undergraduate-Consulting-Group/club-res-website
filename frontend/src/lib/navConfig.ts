import { MODULES, usableModules, type AppModule, type AppNavItem } from '../modules';

/**
 * Navigation. Home (`/`) is the full-page hub with no bar; every other page
 * belongs to one app, and its bar holds Home and that app's own pages. The
 * items come from the apps' manifests (src/modules); shells (desktop +
 * mobile) read them from here.
 */
export type NavItem = AppNavItem;

const navOf = (modules: AppModule[]): NavItem[] => modules.flatMap((module) => module.nav);

export const NAV_ITEMS: NavItem[] = navOf(MODULES).filter((item) => !item.adminOnly);

export const ADMIN_NAV_ITEMS: NavItem[] = navOf(MODULES).filter((item) => item.adminOnly);

/** The menu for this member: the apps they may use, and admin pages for admins. */
export function getNavItems(isAdmin: boolean, apps?: string[]): NavItem[] {
  const items = navOf(usableModules(apps));
  return items.filter((item) => !item.adminOnly || isAdmin);
}

/** The app a page belongs to: the one serving its first path segment; core (Home, Profile, Admin) otherwise. */
export function appForPath(pathname: string): AppModule {
  const segment = pathname.split('/')[1] ?? '';
  const core = MODULES[0];
  return MODULES.find((module) => module !== core && module.routes.some(
    (route) => !('index' in route) && route.path.split('/')[0] === segment,
  )) ?? core;
}

/** One app's bar: Home, then that app's pages this member may open. */
export function appBarItems(app: AppModule, items: NavItem[]): NavItem[] {
  const home = items.find((item) => item.to === '/');
  const own = items.filter((item) => item.to !== '/' && app.nav.some((entry) => entry.id === item.id));
  return home ? [home, ...own] : own;
}

/** Match active route for nav highlighting */
export function isNavItemActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}
