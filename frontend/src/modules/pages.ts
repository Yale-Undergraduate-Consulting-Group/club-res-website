import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

/** A page whose code loads on first visit, or earlier through `preload` (a hovered link, an idle moment). */
export type Page = LazyExoticComponent<ComponentType> & { preload: () => Promise<unknown> };

function page(load: () => Promise<{ default: ComponentType }>): Page {
  return Object.assign(lazy(load), { preload: load });
}

/** Each page loads on first visit, so one app's code never slows another's. */
export const Dashboard = page(() => import('../pages/Dashboard'));
export const Scraper = page(() => import('../pages/Scraper'));
export const EmailStudio = page(() => import('../pages/EmailStudio'));
export const CampaignDetail = page(() => import('../pages/CampaignDetail'));
export const Analytics = page(() => import('../pages/Analytics'));
export const Outreach = page(() => import('../pages/Outreach'));
export const Directory = page(() => import('../pages/Directory'));
export const Admin = page(() => import('../pages/Admin'));
export const Profile = page(() => import('../pages/Profile'));
export const Surveys = page(() => import('../pages/Surveys'));
export const Sentiment = page(() => import('../pages/Sentiment'));
export const Resources = page(() => import('../pages/Resources'));
export const Hub = page(() => import('../pages/Hub'));
