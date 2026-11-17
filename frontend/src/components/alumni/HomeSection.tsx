import { useCallback } from 'react';
import { api } from '../../api';
import { Button, EmptyState, Notice } from '../ui/Primitives';
import ContactBoard from './ContactBoard';
import { EventCard, PostCard } from './PortalCards';
import { useLoad } from './portalContext';

/** The portal's front page: latest news, upcoming events, a nudge to fill in a profile, and the contact form. */
export default function HomeSection({ onOpen }: { onOpen: (view: 'news' | 'events' | 'directory' | 'profile') => void }) {
  const load = useCallback(() => api.alumni.home(), []);
  const { data, error } = useLoad(load);

  if (error) return <Notice tone="danger">Could not load the portal: {error}</Notice>;
  if (!data) return <p className="text-sm text-slate-500" role="status">Loading…</p>;

  return (
    <div className="space-y-6">
      {!data.profile.exists && (
        <Notice>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="min-w-0 break-words">Complete your profile so classmates can find you. It stays private until you choose to be listed.</span>
            <Button size="sm" onClick={() => onOpen('profile')}>Complete your profile</Button>
          </div>
        </Notice>
      )}

      <section className="space-y-3" aria-labelledby="portal-home-news">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="portal-home-news" className="font-semibold text-deep-navy">Latest news</h2>
          <Button size="sm" variant="secondary" onClick={() => onOpen('news')}>All news</Button>
        </div>
        {data.posts.length === 0
          ? <EmptyState title="No news yet" body="Posts from the alumni board appear here." />
          : data.posts.map((post) => <PostCard key={post.id} post={post} excerpt />)}
      </section>

      <section className="space-y-3" aria-labelledby="portal-home-events">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="portal-home-events" className="font-semibold text-deep-navy">Upcoming events</h2>
          <Button size="sm" variant="secondary" onClick={() => onOpen('events')}>All events</Button>
        </div>
        {data.events.length === 0
          ? <EmptyState title="No upcoming events" body="Events the alumni board schedules appear here." />
          : data.events.map((event) => <EventCard key={event.id} event={event} />)}
      </section>

      <section className="surface-card space-y-2" aria-labelledby="portal-home-directory">
        <h2 id="portal-home-directory" className="font-semibold text-deep-navy">Alumni directory</h2>
        <p className="text-sm text-slate-600">Find alumni who chose to be listed{data.profile.listed ? '. You are listed.' : '.'}</p>
        <div><Button variant="secondary" onClick={() => onOpen('directory')}>Open the directory</Button></div>
      </section>

      <ContactBoard />
    </div>
  );
}
