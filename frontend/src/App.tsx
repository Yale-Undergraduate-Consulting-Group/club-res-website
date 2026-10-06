import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense, useEffect, useState } from 'react';
import LoginPage from './pages/LoginPage';
import MainApp from './pages/MainApp';
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './contexts/ToastContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { AiModelProvider } from './contexts/AiModelContext';
import { API_BASE } from './api';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Scraper = lazy(() => import('./pages/Scraper'));
const EmailStudio = lazy(() => import('./pages/EmailStudio'));
const CampaignDetail = lazy(() => import('./pages/CampaignDetail'));
const Analytics = lazy(() => import('./pages/Analytics'));
const Outreach = lazy(() => import('./pages/Outreach'));
const Admin = lazy(() => import('./pages/Admin'));
const Profile = lazy(() => import('./pages/Profile'));

function AppContent() {
  const [user, setUser] = useState<{ id?: number; email: string; name?: string; picture?: string; role?: string } | null>(null);
  const [authLoading, setAuthLoading] = useState(true);

  const checkAuth = () => {
    const token = localStorage.getItem('yucg_token');
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    fetch(`${API_BASE}/api/auth/me`, {
      headers,
      credentials: 'include',
      signal: controller.signal,
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.authenticated && data.user) {
          setUser({ ...data.user, role: data.user.role || 'standard' });
        } else {
          localStorage.removeItem('yucg_token');
          localStorage.removeItem('yucg_token_time');
          setUser(null);
        }
      })
      .catch(() => {
        setUser(null);
      })
      .finally(() => {
        clearTimeout(timeout);
        setAuthLoading(false);
      });
  };

  useEffect(() => {
    checkAuth();
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('yucg_token');
    localStorage.removeItem('yucg_token_time');
    fetch(`${API_BASE}/api/auth/logout`, { method: 'POST', credentials: 'include' }).catch(() => {});
    setUser(null);
  };

  useEffect(() => {
    const onUnauthorized = () => {
      localStorage.removeItem('yucg_token');
      localStorage.removeItem('yucg_token_time');
      setUser(null);
    };
    window.addEventListener('yucg:unauthorized', onUnauthorized);
    return () => window.removeEventListener('yucg:unauthorized', onUnauthorized);
  }, []);

  if (authLoading) {
    return <AppLoading label="Opening your workspace…" />;
  }

  const routes = (
    <Routes>
      {/* Login page - only when NOT authenticated */}
      <Route
        path="/login"
        element={
          user ? (
            <Navigate to="/" replace />
          ) : (
            <LoginPage />
          )
        }
      />
      {/* All other routes - require auth, show MainApp */}
      <Route
        path="/*"
        element={
          user ? (
            <MainApp user={user} onLogout={handleLogout} />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="scraper" element={<Scraper />} />
        <Route path="studio" element={<EmailStudio />} />
        {/* The campaign list is disabled: Home shows a member's own
            campaigns now, so a stray link or bookmark lands there instead
            of an empty shell - the same courtesy /settings already gets. */}
        <Route path="campaigns" element={<Navigate to="/" replace />} />
        <Route path="campaigns/:id" element={<CampaignDetail />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="outreach" element={<Outreach />} />
        <Route path="admin" element={<Admin />} />
        <Route path="profile" element={<Profile />} />
        <Route path="settings" element={<Navigate to="/profile?tab=settings" replace />} />
      </Route>
    </Routes>
  );
  return <Suspense fallback={<AppLoading label="Loading section…" />}>{routes}</Suspense>;
}

function AppLoading({ label }: { label: string }) {
  return (
    <div className="app-auth-loading" role="status" aria-live="polite">
      <div className="text-center max-w-md px-6">
        <div className="animate-spin w-10 h-10 border-2 border-white border-t-transparent rounded-full mx-auto mb-4" />
        <p className="text-white font-bold uppercase tracking-wide">YUCG Outreach</p>
        <p className="text-white/80 text-sm mt-2">{label}</p>
      </div>
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AiModelProvider>
        <ToastProvider>
          <BrowserRouter>
            <AppContent />
          </BrowserRouter>
        </ToastProvider>
        </AiModelProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
