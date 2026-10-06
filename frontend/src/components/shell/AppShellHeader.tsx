import { useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { NAV_GROUPS, TOP_LEVEL_IDS, type NavItem } from '../../lib/navConfig';
import AppNavLink from './AppNavLink';

type AppShellHeaderProps = {
  user: { email: string; name?: string; picture?: string };
  navItems: NavItem[];
  onLogout: () => void;
};

export default function AppShellHeader({ user, navItems, onLogout }: AppShellHeaderProps) {
  const dropdowns = useRef<Array<HTMLDetailsElement | null>>([]);
  const { pathname } = useLocation();
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      dropdowns.current.forEach(dropdown => {
        if (dropdown && !dropdown.contains(event.target as Node)) dropdown.open = false;
      });
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, []);
  const groups = NAV_GROUPS;
  const primary = TOP_LEVEL_IDS.map(id => navItems.find(item => item.id === id)).filter(
    (item): item is NavItem => !!item,
  );
  return (
    <header className="app-shell-header app-top-nav sticky top-0 z-50">
      <div className="app-shell-header-inner">
        <div className="app-shell-brand">
          <img src="/yucg-logo.png" alt="YUCG" className="app-shell-logo" decoding="async" />
          <div className="min-w-0">
            <div className="app-shell-title">YUCG Outreach</div>
            <div className="app-shell-subtitle hidden xl:block">Yale Undergraduate Consulting Group</div>
          </div>
        </div>

        <nav className="app-nav-menu app-shell-desktop-nav" aria-label="Main navigation">
          {primary.map((item) => (
            <AppNavLink key={item.id} item={item} variant="desktop" />
          ))}
          {groups.map((group, index) => {
            const items = navItems.filter(item => group.ids.includes(item.id));
            const active = items.some(item => pathname === item.to || pathname.startsWith(item.to + '/'));
            return <details key={`${pathname}:${group.label}`} ref={node => { dropdowns.current[index] = node; }} className="app-nav-group" onKeyDown={event => {
              if (event.key === 'Escape') {
                const dropdown = dropdowns.current[index];
                if (dropdown) { dropdown.open = false; dropdown.querySelector('summary')?.focus(); }
              }
            }}>
              <summary className={`app-nav-link ${active ? 'app-nav-link--active' : ''}`}>{group.label} ▾</summary>
              <div className="app-nav-group-panel" onClick={() => { const dropdown = dropdowns.current[index]; if (dropdown) dropdown.open = false; }}>
                {items.map(item => <AppNavLink key={item.id} item={item} variant="desktop" />)}
              </div>
            </details>;
          })}
          {navItems.filter(item => item.id === 'admin').map(item => <AppNavLink key={item.id} item={item} variant="desktop" />)}
        </nav>

        <div className="app-shell-header-actions">
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
          <NavLink to="/profile" className="app-shell-profile-link app-shell-header-profile">
            {user.name || user.email?.split('@')[0] || 'User'}
          </NavLink>
          <button type="button" onClick={onLogout} className="app-nav-util-btn app-shell-header-logout">
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
