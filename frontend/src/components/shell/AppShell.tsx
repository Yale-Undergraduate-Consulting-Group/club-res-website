import { Suspense, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { appBarItems, appForPath, getNavItems } from '../../lib/navConfig';
import { navigationStarted, pageShown } from '../../lib/pageProgress';
import { prefetchPath, prefetchWhenIdle } from '../../lib/routePrefetch';
import { usableModules } from '../../modules';
import AppShellHeader from './AppShellHeader';
import AppShellMobile from './AppShellMobile';
import AppShellDrawer from './AppShellDrawer';
import PageProgress from './PageProgress';
import Tour from '../Tour';

type AppShellProps = {
  user: { id?: number; email: string; name?: string; picture?: string; role?: string; apps?: string[] };
  onLogout: () => void;
  pageKey?: string;
  headerExtra?: ReactNode;
};

/**
 * Home is the hub and fills the page on its own. Every other page shows the
 * bar of the app it belongs to; the bars are keyed by app, so opening a tool
 * (or moving to another) slides that tool's bar in, while moving between one
 * app's pages keeps it still.
 *
 * Moving between pages never blanks the screen: the router switches pages in a
 * React transition, and the one Suspense boundary sits here, around the page
 * and inside the shell, so the page on screen stays until the next one can
 * render (a boundary that mounted with each page would show its fallback
 * instead). A slim bar shows meanwhile; page code is fetched on hover, focus
 * and when the browser is idle.
 */
export default function AppShell({ user, onLogout, pageKey, headerExtra }: AppShellProps) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navItems = getNavItems(user.role === 'admin', user.apps);
  const app = appForPath(pathname);
  const barItems = appBarItems(app, navItems);
  const onHub = pathname === '/';
  const shown = useRef(pathname);
  // The page the app opened on appears at once (nothing to swap from); a page that replaces another fades in.
  const [openedOn] = useState(pathname);

  useLayoutEffect(() => {
    shown.current = pathname;
    pageShown();
  }, [pathname]);

  useEffect(() => {
    const modules = usableModules(user.apps);
    // The router has already moved the address when a click or Back is handled; until
    // the page on screen matches it, the next one is still loading.
    const check = () => { if (window.location.pathname !== shown.current) navigationStarted(); };
    const afterNavigation = () => { window.setTimeout(check, 0); };
    const prefetchLink = (event: Event) => {
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
      if (!(link instanceof HTMLAnchorElement) || link.origin !== window.location.origin || link.hasAttribute('download')) return;
      prefetchPath(modules, link.pathname);
    };
    document.addEventListener('click', afterNavigation, true);
    window.addEventListener('popstate', afterNavigation);
    document.addEventListener('pointerover', prefetchLink);
    document.addEventListener('focusin', prefetchLink);
    const cancelIdle = prefetchWhenIdle(modules);
    return () => {
      document.removeEventListener('click', afterNavigation, true);
      window.removeEventListener('popstate', afterNavigation);
      document.removeEventListener('pointerover', prefetchLink);
      document.removeEventListener('focusin', prefetchLink);
      cancelIdle();
    };
  }, [user.apps]);

  const handleLogout = () => {
    onLogout();
    navigate('/login');
  };

  return (
    <div className={`app-shell min-h-screen flex flex-col ${onHub ? 'app-shell--hub' : ''}`}>
      <PageProgress />
      {!onHub && <AppShellHeader key={`bar:${app.id}`} app={app} user={user} navItems={barItems} onLogout={handleLogout} />}
      {headerExtra}
      <div className="app-shell-body flex flex-1 min-h-0">
        <main className="app-shell-main flex-1 min-w-0 min-h-0">
          {/* First visit to a page (no page on screen yet): the shell stays and says so. */}
          <Suspense fallback={<p className="app-page-loading" role="status">Opening page…</p>}>
            <div key={pageKey} className={pathname === openedOn ? 'app-shell-main-inner' : 'app-shell-main-inner app-shell-main-inner--swap'}>
              <Outlet context={{ user, onLogout: handleLogout }} />
            </div>
          </Suspense>
        </main>
      </div>
      {!onHub && <AppShellMobile key={`mobile-bar:${app.id}`} items={barItems} onOpenMenu={() => setDrawerOpen(true)} />}
      <AppShellDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        user={user}
        items={navItems}
        onLogout={handleLogout}
      />
      <Tour />
    </div>
  );
}
