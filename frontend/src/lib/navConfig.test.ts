import { describe, expect, it } from 'vitest';
import { MODULES } from '../modules';
import { ADMIN_NAV_ITEMS, MOBILE_PRIMARY_IDS, NAV_GROUPS, NAV_ITEMS, TOP_LEVEL_IDS, getNavItems, isNavItemActive } from './navConfig';

describe('navigation', () => {
  it('reaches every member destination from the desktop header', () => {
    const headerIds = [...TOP_LEVEL_IDS, ...NAV_GROUPS.flatMap((group) => group.ids)];
    expect(headerIds.toSorted()).toEqual(NAV_ITEMS.map((item) => item.id).toSorted());
    expect(MOBILE_PRIMARY_IDS.every((id) => headerIds.includes(id))).toBe(true);
  });

  it('shows Admin only to administrators', () => {
    const admin = ADMIN_NAV_ITEMS.find((item) => item.id === 'admin');
    expect(admin).toBeDefined();
    expect(getNavItems(false)).not.toContain(admin);
    expect(getNavItems(true)).toContain(admin);
  });

  it('every app has a unique id and paths, and every menu link opens a page an app serves', () => {
    expect(new Set(MODULES.map((m) => m.id)).size).toBe(MODULES.length);
    const paths = MODULES.flatMap((m) => m.routes.map((r) => ('index' in r ? '/' : `/${r.path}`)));
    expect(new Set(paths).size).toBe(paths.length);
    const navIds = MODULES.flatMap((m) => m.nav.map((n) => n.id));
    expect(new Set(navIds).size).toBe(navIds.length);
    for (const item of MODULES.flatMap((m) => m.nav)) expect(paths).toContain(item.to);
  });

  it.each([
    ['/', '/', true],
    ['/studio', '/', false],
    ['/studio', '/studio', true],
    ['/studio/42', '/studio', true],
    ['/studio-archive', '/studio', false],
    ['/', '/studio', false],
  ])('%s highlights %s: %s', (pathname, to, active) => expect(isNavItemActive(pathname, to)).toBe(active));
});
