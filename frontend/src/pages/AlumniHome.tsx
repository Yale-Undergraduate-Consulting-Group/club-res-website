/**
 * The alumni portal: news, events, an opt-in directory, a profile, and a way to
 * write to the board. Alumni get no club data. Board members (marked by a club
 * admin) also post news and events and read the board inbox; the server
 * decides what each person may do (backend/app/routers/alumni_portal.py).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, api } from '../api';
import AppTabMenu from '../components/AppTabMenu';
import ContactBoard from '../components/alumni/ContactBoard';
import DirectorySection from '../components/alumni/DirectorySection';
import EventsSection from '../components/alumni/EventsSection';
import HomeSection from '../components/alumni/HomeSection';
import InboxSection from '../components/alumni/InboxSection';
import PostsSection from '../components/alumni/PostsSection';
import ProfileSection from '../components/alumni/ProfileSection';
import { PortalContext, type PortalContextValue } from '../components/alumni/portalContext';
import { Button, Notice, StatusBadge } from '../components/ui/Primitives';
import { useUrlTab } from '../lib/useUrlTab';

const VIEWS = ['home', 'news', 'events', 'directory', 'profile', 'contact', 'inbox'] as const;
type View = (typeof VIEWS)[number];
const LABELS: Record<View, string> = {
  home: 'Home', news: 'News', events: 'Events', directory: 'Directory', profile: 'My profile', contact: 'Contact the board', inbox: 'Board inbox',
};

export default function AlumniHome() {
  const navigate = useNavigate();
  const [me, setMe] = useState<{ email: string; name: string | null; board: boolean } | null>(null);
  const [loadError, setLoadError] = useState('');
  const [unread, setUnread] = useState<number | null>(null);
  const [portalError, setPortalError] = useState('');

  // A 401 means the alumni session ended; anything else is shown where it happened.
  const fail = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      navigate('/alumni/login', { replace: true });
      return '';
    }
    return error instanceof Error ? error.message : 'Something went wrong.';
  }, [navigate]);

  useEffect(() => {
    api.alumni.me().then(setMe).catch((e: unknown) => setLoadError(fail(e)));
  }, [fail]);

  const board = me?.board ?? false;
  useEffect(() => {
    if (!board) return;
    api.alumniPortal.inbox.list(false)
      .then((inbox) => setUnread(inbox.unread))
      .catch((e: unknown) => { const message = fail(e); if (message) setPortalError(message); });
  }, [board, fail]);

  const allowed = useMemo(() => VIEWS.filter((view) => view !== 'inbox' || board), [board]);
  const [view, setView] = useUrlTab(allowed, 'home');
  const context = useMemo<PortalContextValue>(() => ({ api: api.alumniPortal, canWrite: board, fail }), [board, fail]);

  const signOut = async () => {
    await api.alumni.logout().catch(() => {});
    navigate('/alumni/login', { replace: true });
  };

  if (loadError) return <div className="app-workspace max-w-4xl mx-auto px-4 py-6"><Notice tone="danger">Could not open the alumni portal: {loadError}</Notice></div>;
  if (!me) return <p className="app-page-loading" role="status">Opening…</p>;

  return (
    <PortalContext.Provider value={context}>
      <div className="app-workspace max-w-4xl mx-auto px-4 py-6 space-y-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <img src="/yucg-logo.png" alt="YUCG" className="h-10 w-auto" decoding="async" />
            <div className="min-w-0">
              <h1 className="text-xl font-bold text-deep-navy">YUCG Alumni</h1>
              <p className="text-sm text-slate-600 break-all">{me.name || me.email}</p>
            </div>
            {me.board && <StatusBadge tone="info">Board</StatusBadge>}
          </div>
          <Button variant="secondary" size="sm" onClick={signOut}>Sign out</Button>
        </header>

        <AppTabMenu
          tabs={allowed.map((id) => ({ id, label: LABELS[id], count: id === 'inbox' && unread ? unread : undefined }))}
          active={view}
          onChange={(id) => setView(id as View)}
          label="Alumni portal sections"
        />
        {portalError && <Notice tone="danger">{portalError}</Notice>}

        {view === 'home' && <HomeSection onOpen={setView} />}
        {view === 'news' && <PostsSection />}
        {view === 'events' && <EventsSection />}
        {view === 'directory' && <DirectorySection />}
        {view === 'profile' && <ProfileSection defaultName={me.name ?? ''} />}
        {view === 'contact' && <ContactBoard />}
        {view === 'inbox' && board && <InboxSection onUnread={setUnread} />}
      </div>
    </PortalContext.Provider>
  );
}
