import { describe, expect, it } from 'vitest';
import { ADMIN_NAV_ITEM, MOBILE_PRIMARY_IDS, NAV_GROUPS, NAV_ITEMS, TOP_LEVEL_IDS, getNavItems, isNavItemActive } from './navConfig';

describe('navigation', () => {
  it('reaches every member destination from the desktop header', () => {
    const headerIds = [...TOP_LEVEL_IDS, ...NAV_GROUPS.flatMap((group) => group.ids)];
    expect(headerIds.toSorted()).toEqual(NAV_ITEMS.map((item) => item.id).toSorted());
    expect(MOBILE_PRIMARY_IDS.every((id) => headerIds.includes(id))).toBe(true);
  });

  it('shows Admin only to administrators', () => {
    expect(getNavItems(false)).not.toContain(ADMIN_NAV_ITEM);
    expect(getNavItems(true)).toContain(ADMIN_NAV_ITEM);
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
