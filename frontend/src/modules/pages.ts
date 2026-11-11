import { lazy } from 'react';

/** Each page loads on first visit, so one app's code never slows another's. */
export const Dashboard = lazy(() => import('../pages/Dashboard'));
export const Scraper = lazy(() => import('../pages/Scraper'));
export const EmailStudio = lazy(() => import('../pages/EmailStudio'));
export const CampaignDetail = lazy(() => import('../pages/CampaignDetail'));
export const Analytics = lazy(() => import('../pages/Analytics'));
export const Outreach = lazy(() => import('../pages/Outreach'));
export const Directory = lazy(() => import('../pages/Directory'));
export const Admin = lazy(() => import('../pages/Admin'));
export const Profile = lazy(() => import('../pages/Profile'));
