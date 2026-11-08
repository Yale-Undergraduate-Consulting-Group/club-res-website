import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { Admin, Analytics, CampaignDetail, Dashboard, EmailStudio, Outreach, Profile, Scraper } from './pages';

/**
 * Apps on the platform, as the browser sees them: the pages each one serves
 * and the navigation entries that lead there. The app shell mounts every
 * route and builds its menus from these; adding an app is a new manifest in
 * MODULES. The server's side of the same apps is backend/app/modules
 * (docs/PLATFORM.md).
 */

export type AppRoute = { path: string; element: ReactNode } | { index: true; element: ReactNode };

export type AppNavItem = {
  id: string;
  to: string;
  label: string;
  /** Shown under glyphs on mobile bottom nav */
  shortLabel: string;
  /** Only administrators see it. The server checks access regardless. */
  adminOnly?: boolean;
};

export type AppModule = {
  id: string;
  title: string;
  routes: AppRoute[];
  nav: AppNavItem[];
};

const core: AppModule = {
  id: 'core',
  title: 'Platform',
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'admin', element: <Admin /> },
    { path: 'profile', element: <Profile /> },
    { path: 'settings', element: <Navigate to="/profile?tab=settings" replace /> },
  ],
  nav: [
    { id: 'dashboard', to: '/', label: 'Home', shortLabel: 'Home' },
    { id: 'admin', to: '/admin', label: 'Admin', shortLabel: 'Admin', adminOnly: true },
  ],
};

const outreach: AppModule = {
  id: 'outreach',
  title: 'Outreach',
  routes: [
    { path: 'scraper', element: <Scraper /> },
    { path: 'studio', element: <EmailStudio /> },
    // The campaign list is disabled: Home shows a member's own campaigns now,
    // so a stray link or bookmark lands there instead of an empty shell - the
    // same courtesy /settings already gets.
    { path: 'campaigns', element: <Navigate to="/" replace /> },
    { path: 'campaigns/:id', element: <CampaignDetail /> },
    { path: 'analytics', element: <Analytics /> },
    { path: 'outreach', element: <Outreach /> },
  ],
  nav: [
    { id: 'studio', to: '/studio', label: 'Drafts', shortLabel: 'Drafts' },
    { id: 'outreach', to: '/outreach', label: 'Pipeline', shortLabel: 'Pipeline' },
    { id: 'scraper', to: '/scraper', label: 'Find contacts', shortLabel: 'Contacts' },
  ],
};

/** Every app, core first. */
export const MODULES: AppModule[] = [core, outreach];

/**
 * The apps this member may use, as the server reported them on sign-in.
 * Core always is; without a list (an older server) every app shows, and the
 * server still refuses a route the member may not use.
 */
export function usableModules(apps?: string[]): AppModule[] {
  return MODULES.filter((module) => module.id === 'core' || !apps || apps.includes(module.id));
}
