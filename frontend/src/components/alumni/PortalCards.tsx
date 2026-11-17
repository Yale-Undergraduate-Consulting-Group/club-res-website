import type { ReactNode } from 'react';
import { portalImageUrl, type PortalEvent, type PortalPost } from '../../api';
import { formatEventTime, onlineLinkProblem } from '../../lib/alumniPortal';
import { portalExcerpt } from '../../lib/portalHtml';
import { StatusBadge } from '../ui/Primitives';
import PortalHtml from './PortalHtml';
import { formatDate } from './portalContext';

/** A post: title, badges, byline, cover and text (a plain excerpt when `excerpt` is set). */
export function PostCard({ post, excerpt = false, actions }: { post: PortalPost; excerpt?: boolean; actions?: ReactNode }) {
  const when = post.published_at ?? post.updated_at;
  return (
    <article className="surface-card space-y-3 min-w-0" aria-label={post.title}>
      <div className="flex flex-wrap items-start gap-3 min-w-0">
        {post.cover && (
          <img
            src={portalImageUrl(post.cover.url)}
            alt=""
            loading="lazy"
            className={excerpt ? 'h-20 w-28 shrink-0 object-cover' : 'max-h-72 w-full object-cover'}
          />
        )}
        <div className="min-w-0 flex-[1_1_16rem] space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {post.pinned && <StatusBadge tone="info">Pinned</StatusBadge>}
            {post.status === 'draft' && <StatusBadge tone="warning">Draft</StatusBadge>}
          </div>
          <h3 className="font-semibold text-deep-navy break-words">{post.title}</h3>
          <p className="text-xs text-slate-500 break-words">{post.author} · {post.status === 'draft' ? 'edited' : 'published'} {formatDate(when)}</p>
        </div>
      </div>
      {excerpt
        ? <p className="text-sm text-slate-700 break-words">{portalExcerpt(post.body_html)}</p>
        : <PortalHtml html={post.body_html} />}
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </article>
  );
}

/** An event: its time in its own zone, where, and the description. */
export function EventCard({ event, actions }: { event: PortalEvent; actions?: ReactNode }) {
  const online = event.online_url && !onlineLinkProblem(event.online_url) ? event.online_url : null;
  return (
    <article className="surface-card space-y-2 min-w-0" aria-label={event.title}>
      <h3 className="font-semibold text-deep-navy break-words">{event.title}</h3>
      <p className="text-sm text-slate-700 break-words">{formatEventTime(event.starts_at, event.timezone)}</p>
      {event.place && <p className="text-sm text-slate-600 break-words">{event.place}</p>}
      {online && (
        <p className="text-sm break-all">
          <a className="text-[var(--link)] underline" href={online} target="_blank" rel="noopener noreferrer">Join online</a>
          <span className="text-slate-500"> · {online}</span>
        </p>
      )}
      <PortalHtml html={event.description_html} />
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </article>
  );
}
