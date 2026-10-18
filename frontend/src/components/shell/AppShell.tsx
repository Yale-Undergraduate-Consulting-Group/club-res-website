import { useState, type ReactNode } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { getNavItems } from '../../lib/navConfig';
import AppShellHeader from './AppShellHeader';
import AppShellMobile from './AppShellMobile';
import AppShellDrawer from './AppShellDrawer';
import GuideBubble from '../GuideBubble';
import Tour from '../Tour';

type AppShellProps = {
  user: { id?: number; email: string; name?: string; picture?: string; role?: string };
  onLogout: () => void;
  pageKey?: string;
  headerExtra?: ReactNode;
};

export default function AppShell({ user, onLogout, pageKey, headerExtra }: AppShellProps) {
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navItems = getNavItems(user.role === 'admin');

  const handleLogout = () => {
    onLogout();
    navigate('/login');
  };

  return (
    <div className="app-shell min-h-screen flex flex-col">
      <AppShellHeader user={user} navItems={navItems} onLogout={handleLogout} />
      {headerExtra}
      <div className="app-shell-body flex flex-1 min-h-0">
        <main className="app-shell-main flex-1 min-w-0 min-h-0">
          <div key={pageKey} className="app-shell-main-inner">
            <Outlet context={{ user }} />
          </div>
        </main>
      </div>
      <AppShellMobile items={navItems} onOpenMenu={() => setDrawerOpen(true)} />
      <AppShellDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        user={user}
        items={navItems}
        onLogout={handleLogout}
      />
      <GuideBubble />
      <Tour />
    </div>
  );
}
