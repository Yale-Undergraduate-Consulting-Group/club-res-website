import { useCallback, useState, type FormEvent } from 'react';
import { ApiError, portalImageUrl, type PortalPost } from '../../api';
import { Button, ConfirmDialog, EmptyState, Notice } from '../ui/Primitives';
import PortalEditor from './PortalEditor';
import { PostCard } from './PortalCards';
import { usePortal, useLoad } from './portalContext';

/** News: published posts for everyone (pinned first); the board also sees drafts and writes. */
export default function PostsSection() {
  const { api, canWrite, fail } = usePortal();
  const load = useCallback(() => api.posts.list(100), [api]);
  const { data, error, refresh } = useLoad(load);
  const [editing, setEditing] = useState<PortalPost | 'new' | null>(null);
  const [deleting, setDeleting] = useState<PortalPost | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.posts.remove(deleting.id);
      setNotice(`“${deleting.title}” deleted.`);
      setFailure('');
      refresh();
    } catch (e) {
      setFailure(fail(e));
    } finally {
      setBusy(false);
      setDeleting(null);
    }
  };

  if (editing) {
    return (
      <PostForm
        post={editing === 'new' ? null : editing}
        onDone={(message) => { setEditing(null); if (message) setNotice(message); refresh(); }}
        onCancel={() => setEditing(null)}
      />
    );
  }

  return (
    <section className="space-y-4" aria-labelledby="portal-news">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="portal-news" className="font-semibold text-deep-navy">News</h2>
        {canWrite && <Button onClick={() => { setNotice(''); setEditing('new'); }}>New post</Button>}
      </div>
      {notice && <Notice tone="success">{notice}</Notice>}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {error && <Notice tone="danger">Could not load news: {error}</Notice>}
      {!data && !error && <p className="text-sm text-slate-500" role="status">Loading…</p>}
      {data && data.items.length === 0 && (
        <EmptyState title="No news yet" body={canWrite ? 'Write the first post for the alumni.' : 'Posts from the alumni board appear here.'} />
      )}
      {data && data.items.map((post) => (
        <PostCard
          key={post.id}
          post={post}
          actions={canWrite && (
            <>
              <Button size="sm" variant="secondary" aria-label={`Edit ${post.title}`} onClick={() => { setNotice(''); setEditing(post); }}>Edit</Button>
              <Button size="sm" variant="danger" aria-label={`Delete ${post.title}`} onClick={() => setDeleting(post)}>Delete</Button>
            </>
          )}
        />
      ))}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this post?"
        body={deleting ? `“${deleting.title}” is deleted for everyone, including its cover image. This cannot be undone.` : ''}
        confirmLabel="Delete post"
        danger
        busy={busy}
        onConfirm={remove}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}

function PostForm({ post, onDone, onCancel }: { post: PortalPost | null; onDone: (message: string) => void; onCancel: () => void }) {
  const { api, fail } = usePortal();
  const [title, setTitle] = useState(post?.title ?? '');
  const [bodyHtml, setBodyHtml] = useState(post?.body_html ?? '');
  const [cover, setCover] = useState(post?.cover ?? null);
  const [status, setStatus] = useState<'draft' | 'published'>(post?.status ?? 'draft');
  const [pinned, setPinned] = useState(post?.pinned ?? false);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const chooseCover = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      setCover(await api.uploadCover(file));
    } catch (e) {
      setError(e instanceof Error && !(e instanceof ApiError) ? e.message : fail(e));
    } finally {
      setUploading(false);
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) { setError('Give the post a title.'); return; }
    setSaving(true);
    setError('');
    const input = { title: title.trim(), body_html: bodyHtml, status, pinned, cover_image_id: cover?.id ?? null };
    try {
      if (post) await api.posts.update(post.id, input);
      else await api.posts.create(input);
      onDone(status === 'published' ? 'Post published.' : 'Draft saved.');
    } catch (e) {
      setError(fail(e));
      setSaving(false);
    }
  };

  return (
    <form className="surface-card space-y-4" onSubmit={save} aria-label={post ? 'Edit post' : 'New post'}>
      <h2 className="font-semibold text-deep-navy">{post ? 'Edit post' : 'New post'}</h2>
      {error && <Notice tone="danger">{error}</Notice>}
      <label className="block space-y-1">
        <span className="block text-sm font-medium text-deep-navy">Title</span>
        <input className="ui-input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <PortalEditor label="Post body" initialHtml={post?.body_html ?? ''} onChange={setBodyHtml} />
      <div className="space-y-2">
        <label className="block space-y-1">
          <span className="block text-sm font-medium text-deep-navy">Cover image (optional)</span>
          <input
            className="ui-input"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={uploading}
            onChange={(e) => { void chooseCover(e.target.files?.[0]); e.target.value = ''; }}
          />
        </label>
        <p className="text-xs text-slate-500">PNG, JPEG or WebP, up to 5 MB.</p>
        {uploading && <p className="text-sm text-slate-500" role="status">Uploading…</p>}
        {cover && (
          <div className="space-y-2">
            <img src={portalImageUrl(cover.url)} alt="Cover preview" className="max-h-48 max-w-full object-cover" />
            <div><Button size="sm" variant="secondary" onClick={() => setCover(null)}>Remove cover</Button></div>
          </div>
        )}
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-deep-navy">Visibility</legend>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" name="post-status" checked={status === 'draft'} onChange={() => setStatus('draft')} /> Draft (only the board sees it)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" name="post-status" checked={status === 'published'} onChange={() => setStatus('published')} /> Published
          </label>
        </div>
      </fieldset>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Pinned (shown first)
      </label>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving || uploading}>{saving ? 'Saving…' : status === 'published' ? 'Publish' : 'Save draft'}</Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </form>
  );
}
