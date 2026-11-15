import type { NavItem } from '../../lib/navConfig';
import AppNavLink from './AppNavLink';

type AppShellMobileProps = {
  /** This app's bar: Home, then its own pages. */
  items: NavItem[];
  onOpenMenu: () => void;
};

/** Phones show at most this many bar entries; the drawer covers the rest. */
const MOBILE_SLOTS = 4;

/**
 * Mobile shell chrome: the app's bottom bar + menu button (drawer trigger).
 * Hidden on lg+ via styles/shell.css — do not render a duplicate menu in the header.
 */
export default function AppShellMobile({ items, onOpenMenu }: AppShellMobileProps) {
  return (
    <div className="app-mobile-shell">
      <nav className="app-mobile-nav" aria-label="Main navigation">
        <div className="app-mobile-nav-scroll">
          {items.slice(0, MOBILE_SLOTS).map((item) => (
            <AppNavLink key={item.id} item={item} variant="mobile" />
          ))}
        </div>
        <button
          type="button"
          className="app-mobile-nav-menu-btn"
          onClick={onOpenMenu}
          aria-label="Open all sections"
        >
          <span className="app-mobile-nav-menu-glyph" aria-hidden />
          <span className="app-mobile-nav-label">More</span>
        </button>
      </nav>
    </div>
  );
}
