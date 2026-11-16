import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { OutreachIcon, ResourcesIcon, SentimentIcon, SurveyIcon } from '../components/AppIcons';
import { Admin, Analytics, CampaignDetail, Dashboard, Directory, EmailStudio, Hub, Outreach, Profile, Resources, Scraper, Surveys, Sentiment, type Page } from './pages';

/**
 * Apps on the platform, as the browser sees them: the pages each one serves
 * and the navigation entries that lead there. The app shell mounts every
 * route and builds its menus from these; adding an app is a new manifest in
 * MODULES. The server's side of the same apps is backend/app/modules
 * (docs/PLATFORM.md).
 */

/** `preload` fetches the page's code ahead of a visit (lib/routePrefetch). */
export type AppRoute = ({ path: string } | { index: true }) & { element: ReactNode; preload?: () => Promise<unknown> };

function pageRoute(path: string, Component: Page): AppRoute {
  return { path, element: <Component />, preload: Component.preload };
}

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
  /** The app's icon on the Home hub, where every app but core has a tile; without one it shows its initial. */
  icon?: ReactNode;
  routes: AppRoute[];
  /** The first entry is the app's home: where the hub card leads. */
  nav: AppNavItem[];
};

const core: AppModule = {
  id: 'core',
  title: 'Platform',
  routes: [
    { index: true, element: <Hub />, preload: Hub.preload },
    pageRoute('admin', Admin),
    pageRoute('profile', Profile),
    { path: 'settings', element: <Navigate to="/profile?tab=settings" replace /> },
  ],
  nav: [
    { id: 'home', to: '/', label: 'Home', shortLabel: 'Home' },
    { id: 'admin', to: '/admin', label: 'Admin', shortLabel: 'Admin', adminOnly: true },
  ],
};

const outreach: AppModule = {
  id: 'outreach',
  title: 'Outreach',
  icon: <OutreachIcon />,
  routes: [
    pageRoute('scraper', Scraper),
    pageRoute('studio', EmailStudio),
    // Outreach's own home: a member's campaigns, what needs attention, the leaderboard.
    pageRoute('campaigns', Dashboard),
    pageRoute('campaigns/:id', CampaignDetail),
    pageRoute('analytics', Analytics),
    pageRoute('outreach', Outreach),
    pageRoute('directory', Directory),
  ],
  nav: [
    { id: 'campaigns', to: '/campaigns', label: 'Overview', shortLabel: 'Outreach' },
    { id: 'scraper', to: '/scraper', label: 'Find contacts', shortLabel: 'Contacts' },
    { id: 'studio', to: '/studio', label: 'Drafts', shortLabel: 'Drafts' },
    { id: 'outreach', to: '/outreach', label: 'Pipeline', shortLabel: 'Pipeline' },
    { id: 'directory', to: '/directory', label: 'Directory', shortLabel: 'Directory' },
  ],
};

const surveys: AppModule = {
  id: 'surveys',
  title: 'Survey builder',
  icon: <SurveyIcon />,
  routes: [pageRoute('surveys', Surveys)],
  nav: [{ id: 'surveys', to: '/surveys', label: 'Survey builder', shortLabel: 'Surveys' }],
};

const sentiment: AppModule = {
  id: 'sentiment',
  title: 'Market sentiment',
  icon: <SentimentIcon />,
  routes: [pageRoute('sentiment', Sentiment)],
  nav: [{ id: 'sentiment', to: '/sentiment', label: 'Market sentiment', shortLabel: 'Sentiment' }],
};

const resources: AppModule = {
  id: 'resources',
  title: 'Resources',
  icon: <ResourcesIcon />,
  routes: [pageRoute('resources', Resources)],
  nav: [{ id: 'resources', to: '/resources', label: 'Resources', shortLabel: 'Resources' }],
};

/** Every app, core first. */
export const MODULES: AppModule[] = [core, outreach, surveys, resources, sentiment];

/**
 * The apps this member may use, as the server reported them on sign-in.
 * Core always is; without a list (an older server) every app shows, and the
 * server still refuses a route the member may not use.
 */
export function usableModules(apps?: string[]): AppModule[] {
  return MODULES.filter((module) => module.id === 'core' || !apps || apps.includes(module.id));
}
