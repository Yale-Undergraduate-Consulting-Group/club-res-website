/**
 * Main app - shown when user IS authenticated.
 * Layout lives in AppShell; this file owns auth-adjacent side effects only.
 */
import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import AppShell from '../components/shell/AppShell';
import { api } from '../api';
import { applyStoredPreferences } from '../lib/userPreferences';

type MainAppProps = {
  user: { email: string; name?: string; picture?: string; role?: string };
  onLogout: () => void;
};


export default function MainApp({ user, onLogout }: MainAppProps) {
  const location = useLocation();

  useEffect(() => {
    applyStoredPreferences();
  }, []);

  useEffect(() => {
    const path = location.pathname || '/';
    const resource = path === '/' ? 'dashboard' : path.slice(1).split('/')[0];
    api.telemetry.event({ event_type: 'page_view', resource_type: resource });
  }, [location.pathname]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName || '')) {
        e.preventDefault();
        const first = document.querySelector<HTMLInputElement>('[data-search-input]');
        if (first) first.focus();
      }
      if (e.key === 'Escape') {
        (e.target as HTMLElement)?.blur?.();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);


  return <AppShell user={user} onLogout={onLogout} pageKey={location.pathname} />;
}
