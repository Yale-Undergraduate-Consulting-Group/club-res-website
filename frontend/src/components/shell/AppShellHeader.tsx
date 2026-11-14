import { useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { HEADER_LAYOUT, type NavItem } from '../../lib/navConfig';
import AppNavLink from './AppNavLink';
import { Button } from '../ui/Primitives';
import GuideBubble from '../GuideBubble';

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
  return (
    <header className="app-shell-header app-top-nav sticky top-0 z-50">
      <div className="app-shell-header-inner">
        <div className="app-shell-brand">
          <img src="/yucg-logo.png" alt="YUCG" className="app-shell-logo" decoding="async" />
          <div className="min-w-0">
            <div className="app-shell-title">YUCG Resources</div>
            <div className="app-shell-subtitle hidden xl:block">Yale Undergraduate Consulting Group</div>
          </div>
        </div>

        <nav className="app-nav-menu app-shell-desktop-nav" aria-label="Main navigation">
          {HEADER_LAYOUT.map((entry, index) => {
            if (typeof entry === 'string') {
              const item = navItems.find(candidate => candidate.id === entry);
              return item ? <AppNavLink key={item.id} item={item} variant="desktop" /> : null;
            }
            const group = entry;
            const items = navItems.filter(item => group.ids.includes(item.id));
            // A member who may not use an app gets no empty dropdown for it.
            if (items.length === 0) return null;
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
            {user.name || user.email?.split('@')[0] || 'User'}
          </NavLink>
          <Button variant="secondary" size="sm" onClick={onLogout} className="app-shell-header-logout">
            Log out
          </Button>
        </div>
      </div>
    </header>
  );
}
