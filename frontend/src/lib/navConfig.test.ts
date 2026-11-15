import { describe, expect, it } from 'vitest';
import { MODULES } from '../modules';
import { ADMIN_NAV_ITEMS, NAV_ITEMS, appBarItems, appForPath, getNavItems, isNavItemActive } from './navConfig';

describe('navigation', () => {
  it('reaches every member destination from Home or an app bar', () => {
    const reachable = new Set(MODULES.flatMap((app) => appBarItems(app, NAV_ITEMS).map((item) => item.id)));
    expect([...reachable].toSorted()).toEqual(NAV_ITEMS.map((item) => item.id).toSorted());
  });

  it.each([
    ['/', 'core'],
    ['/profile', 'core'],
    ['/campaigns/42', 'outreach'],
    ['/scraper', 'outreach'],
    ['/surveys', 'surveys'],
    ['/sentiment', 'sentiment'],
  ])('%s belongs to %s', (pathname, app) => expect(appForPath(pathname).id).toBe(app));

  it('an app bar holds Home and only that app\'s pages', () => {
    const surveys = MODULES.find((app) => app.id === 'surveys')!;
    expect(appBarItems(surveys, getNavItems(true)).map((item) => item.id)).toEqual(['home', 'surveys']);
    const core = MODULES[0];
    expect(appBarItems(core, getNavItems(false)).map((item) => item.id)).toEqual(['home']);
    expect(appBarItems(core, getNavItems(true)).map((item) => item.id)).toEqual(['home', 'admin']);
  });

  it('gives every app on the hub a home page it serves', () => {
    const paths = MODULES.flatMap((m) => m.routes.map((r) => ('index' in r ? '/' : `/${r.path}`)));
    for (const module of MODULES.filter((m) => m.id !== 'core')) expect(paths).toContain(module.nav[0].to);
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
