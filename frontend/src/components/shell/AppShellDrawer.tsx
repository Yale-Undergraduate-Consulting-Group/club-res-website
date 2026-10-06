import { NavLink } from 'react-router-dom';
import type { NavItem } from '../../lib/navConfig';

type AppShellDrawerProps = {
  open: boolean;
  onClose: () => void;
  user: { email: string; name?: string; picture?: string };
  onLogout: () => void;
  items: NavItem[];
};

export default function AppShellDrawer({ open, onClose, user, onLogout, items }: AppShellDrawerProps) {
  if (!open) return null;

  return (
    <div className="app-shell-drawer-root" role="dialog" aria-modal="true" aria-label="Menu">
      <button type="button" className="app-shell-drawer-backdrop" onClick={onClose} aria-label="Close menu" />
      <aside className="app-shell-drawer">
        <div className="app-shell-drawer-header">
          <div className="flex items-center gap-2 min-w-0">
            {user.picture && (
              <img src={user.picture} alt="" className="app-avatar" referrerPolicy="no-referrer" />
            )}
            <div className="min-w-0">
              <div className="font-bold text-sm truncate">{user.name || 'User'}</div>
              <div className="text-xs text-slate-500 truncate">{user.email}</div>
            </div>
          </div>
          <button type="button" className="app-nav-util-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="app-shell-drawer-body">
          <nav aria-label="All sections" className="space-y-1 mb-5">
            {items.map((item) => (
              <NavLink
                key={item.id}
                to={item.to}
                onClick={onClose}
                className={({ isActive }) => `app-sidebar-link ${isActive ? 'app-sidebar-link--active' : ''}`}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="border-t border-[var(--border)] pt-4">
            <NavLink to="/profile" onClick={onClose} className="app-sidebar-link mb-2">
              Profile &amp; preferences
            </NavLink>
            <button
              type="button"
              className="app-sidebar-link w-full text-left"
              onClick={() => {
                onClose();
                onLogout();
              }}
            >
              Log out
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}
