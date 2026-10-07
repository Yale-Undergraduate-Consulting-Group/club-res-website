import { NavLink, useLocation } from 'react-router-dom';
import type { NavItem } from '../../lib/navConfig';
import { isNavItemActive } from '../../lib/navConfig';

type AppNavLinkProps = {
  item: NavItem;
  variant: 'desktop' | 'mobile';
};

export default function AppNavLink({ item, variant }: AppNavLinkProps) {
  const location = useLocation();
  const active = isNavItemActive(location.pathname, item.to);
  const label = variant === 'mobile' ? item.shortLabel : item.label;

  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      title={item.label}
      aria-label={item.label}
      className={
        variant === 'mobile'
          ? `app-mobile-nav-item${active ? ' app-mobile-nav-item--active' : ''}`
          : `app-nav-link${active ? ' app-nav-link--active' : ''}`
      }
    >
      <span className={variant === 'mobile' ? 'app-mobile-nav-label' : undefined}>{label}</span>
    </NavLink>
  );
}
