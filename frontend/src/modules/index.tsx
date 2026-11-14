import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { OutreachIcon, SentimentIcon, SurveyIcon } from '../components/AppIcons';
import { Admin, Analytics, CampaignDetail, Dashboard, Directory, EmailStudio, Hub, Outreach, Profile, Scraper, Surveys, Sentiment } from './pages';

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
  /** One line for the portal hub card; platform pages without a card omit it. */
  description?: string;
  /** The app's icon on the hub; an app without one shows its initial. */
  icon?: ReactNode;
  routes: AppRoute[];
  /** The first entry is the app's home: where the hub card leads. */
  nav: AppNavItem[];
};

const core: AppModule = {
  id: 'core',
  title: 'Platform',
  routes: [
    { index: true, element: <Hub /> },
    { path: 'admin', element: <Admin /> },
    { path: 'profile', element: <Profile /> },
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
  description: 'Find contacts at companies, draft each of them a personal email, and follow what comes back.',
  icon: <OutreachIcon />,
  routes: [
    { path: 'scraper', element: <Scraper /> },
    { path: 'studio', element: <EmailStudio /> },
    // Outreach's own home: a member's campaigns, what needs attention, the leaderboard.
    { path: 'campaigns', element: <Dashboard /> },
    { path: 'campaigns/:id', element: <CampaignDetail /> },
    { path: 'analytics', element: <Analytics /> },
    { path: 'outreach', element: <Outreach /> },
    { path: 'directory', element: <Directory /> },
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
  description: 'Turn project background and hypotheses into a survey draft, then create it in Qualtrics.',
  icon: <SurveyIcon />,
  routes: [{ path: 'surveys', element: <Surveys /> }],
  nav: [{ id: 'surveys', to: '/surveys', label: 'Survey builder', shortLabel: 'Surveys' }],
};

const sentiment: AppModule = {
  id: 'sentiment',
  title: 'Market sentiment',
  description: 'Sentiment from interview transcripts, Reddit and Google reviews.',
  icon: <SentimentIcon />,
  routes: [{ path: 'sentiment', element: <Sentiment /> }],
  nav: [{ id: 'sentiment', to: '/sentiment', label: 'Market sentiment', shortLabel: 'Sentiment' }],
};

/** Every app, core first. */
export const MODULES: AppModule[] = [core, outreach, surveys, sentiment];

/**
 * The apps this member may use, as the server reported them on sign-in.
 * Core always is; without a list (an older server) every app shows, and the
 * server still refuses a route the member may not use.
 */
export function usableModules(apps?: string[]): AppModule[] {
  return MODULES.filter((module) => module.id === 'core' || !apps || apps.includes(module.id));
}
