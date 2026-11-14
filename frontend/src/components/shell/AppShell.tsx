import { useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { appBarItems, appForPath, getNavItems } from '../../lib/navConfig';
import AppShellHeader from './AppShellHeader';
import AppShellMobile from './AppShellMobile';
import AppShellDrawer from './AppShellDrawer';
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
 */
export default function AppShell({ user, onLogout, pageKey, headerExtra }: AppShellProps) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navItems = getNavItems(user.role === 'admin', user.apps);
  const app = appForPath(pathname);
  const barItems = appBarItems(app, navItems);
  const onHub = pathname === '/';

  const handleLogout = () => {
    onLogout();
    navigate('/login');
  };

  return (
    <div className={`app-shell min-h-screen flex flex-col ${onHub ? 'app-shell--hub' : ''}`}>
      {!onHub && <AppShellHeader key={`bar:${app.id}`} app={app} user={user} navItems={barItems} onLogout={handleLogout} />}
      {headerExtra}
      <div className="app-shell-body flex flex-1 min-h-0">
        <main className="app-shell-main flex-1 min-w-0 min-h-0">
          <div key={pageKey} className="app-shell-main-inner">
            <Outlet context={{ user, onLogout: handleLogout }} />
          </div>
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
