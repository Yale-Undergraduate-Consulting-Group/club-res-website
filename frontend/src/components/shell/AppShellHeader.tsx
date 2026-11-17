import { Link, NavLink } from 'react-router-dom';
import type { NavItem } from '../../lib/navConfig';
import type { AppModule } from '../../modules';
import { memberFirstName } from '../../lib/memberName';
import AppNavLink from './AppNavLink';
import { Button } from '../ui/Primitives';
import GuideBubble from '../GuideBubble';
import ThemeToggle from '../ThemeToggle';

type AppShellHeaderProps = {
  app: AppModule;
  user: { email: string; name?: string; picture?: string };
  /** This app's bar: Home, then its own pages. */
  navItems: NavItem[];
  onLogout: () => void;
};

/** One app's bar: its name and icon, its pages, and the member's controls. The logo leads Home. */
export default function AppShellHeader({ app, user, navItems, onLogout }: AppShellHeaderProps) {
  const isCore = app.id === 'core';
  return (
    <header className="app-shell-header app-top-nav sticky top-0 z-50" data-app={app.id}>
      <div className="app-shell-header-inner">
        <Link to="/" className="app-shell-brand" aria-label="YUCG Resources home">
          <img src="/yucg-logo.png" alt="" className="app-shell-logo" decoding="async" />
          {app.icon && <span className="app-shell-app-icon" aria-hidden="true">{app.icon}</span>}
          <span className="min-w-0">
            <span className="app-shell-title block">{isCore ? 'YUCG Resources' : app.title}</span>
            {!isCore && <span className="app-shell-subtitle hidden xl:block">YUCG Resources</span>}
          </span>
        </Link>

        <nav className="app-nav-menu app-shell-desktop-nav" aria-label="Main navigation">
          {navItems.map((item) => <AppNavLink key={item.id} item={item} variant="desktop" />)}
        </nav>

        <div className="app-shell-header-actions">
          <ThemeToggle />
          <GuideBubble />
          {user.picture && (
            <img
              src={user.picture}
              alt=""
              className="app-avatar app-shell-header-avatar"
              referrerPolicy="no-referrer"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none';
              }}
            />
          )}
          <NavLink to="/profile" className="app-shell-profile-link app-shell-header-profile" title={user.name || user.email}>
            {memberFirstName(user)}
          </NavLink>
          <Button variant="secondary" size="sm" onClick={onLogout} className="app-shell-header-logout">
            Log out
          </Button>
        </div>
      </div>
    </header>
  );
}
