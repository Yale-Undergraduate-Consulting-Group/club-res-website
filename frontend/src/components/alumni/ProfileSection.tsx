import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type AlumniProfile } from '../../api';
import { linkedinProblem } from '../../lib/alumniPortal';
import { Button, ConfirmDialog, Notice } from '../ui/Primitives';
import { usePortal } from './portalContext';

type TextKey = 'display_name' | 'class_year' | 'company' | 'role' | 'city' | 'linkedin_url';
type Form = {
  display_name: string; class_year: string; company: string; role: string; city: string; linkedin_url: string; note: string;
  coffee_chats: boolean; listed: boolean; show_email: boolean;
};

const EMPTY = (name: string): Form => ({
  display_name: name, class_year: '', company: '', role: '', city: '', linkedin_url: '', note: '',
  coffee_chats: false, listed: false, show_email: false,
});

const fromProfile = (profile: AlumniProfile): Form => ({
  display_name: profile.display_name, class_year: profile.class_year == null ? '' : String(profile.class_year),
  company: profile.company ?? '', role: profile.role ?? '', city: profile.city ?? '', linkedin_url: profile.linkedin_url ?? '',
  note: profile.note ?? '', coffee_chats: profile.coffee_chats, listed: profile.listed, show_email: profile.show_email,
});

/** The alumnus's own profile. Nothing is listed or shown unless they tick it. */
export default function ProfileSection({ defaultName }: { defaultName: string }) {
  const { fail } = usePortal();
  const [loaded, setLoaded] = useState(false);
  const [exists, setExists] = useState(false);
  const [email, setEmail] = useState('');
  const [form, setForm] = useState<Form>(() => EMPTY(defaultName));
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(() => {
    api.alumni.profile.get()
      .then((result) => {
        setEmail(result.email);
        setExists(result.profile !== null);
        if (result.profile) setForm(fromProfile(result.profile));
        setLoaded(true);
        setLoadError('');
      })
      .catch((e: unknown) => setLoadError(fail(e)));
  }, [fail]);
  useEffect(load, [load]);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setNotice('');
    const year = form.class_year.trim() ? Number(form.class_year) : null;
    const problem = !form.display_name.trim() ? 'Enter the name to show.'
      : year !== null && (!Number.isInteger(year) || year < 1950 || year > 2100) ? 'The class year must be between 1950 and 2100.'
      : linkedinProblem(form.linkedin_url);
    if (problem) { setError(problem); return; }
    setBusy('save');
    setError('');
    try {
      const saved = await api.alumni.profile.save({
        display_name: form.display_name.trim(), class_year: year,
        company: form.company.trim() || null, role: form.role.trim() || null, city: form.city.trim() || null,
        linkedin_url: form.linkedin_url.trim() || null, note: form.note.trim() || null,
        coffee_chats: form.coffee_chats, listed: form.listed, show_email: form.show_email,
      });
      setExists(true);
      setForm(fromProfile(saved.profile));
      setNotice(saved.profile.listed ? 'Profile saved. You are listed in the directory.' : 'Profile saved. You are not listed in the directory.');
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy('');
    }
  };

  const hide = async () => {
    setBusy('hide');
    setError('');
    setNotice('');
    try {
      await api.alumni.profile.hide();
      set('listed', false);
      setNotice('Your profile is hidden from the directory.');
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy('');
    }
  };

  const remove = async () => {
    setBusy('delete');
    setError('');
    try {
      await api.alumni.profile.delete();
      setExists(false);
      setForm(EMPTY(defaultName));
      setNotice('Your profile was deleted.');
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy('');
      setConfirmDelete(false);
    }
  };

  if (loadError) return <Notice tone="danger">Could not load your profile: {loadError}</Notice>;
  if (!loaded) return <p className="text-sm text-slate-500" role="status">Loading…</p>;

  const field = (label: string, key: TextKey, props: { type?: string; maxLength?: number; placeholder?: string } = {}) => (
    <label className="block space-y-1 flex-[1_1_14rem] min-w-0">
      <span className="block text-sm font-medium text-deep-navy">{label}</span>
      <input className="ui-input" value={form[key]} onChange={(e) => set(key, e.target.value)} {...props} />
    </label>
  );

  return (
    <section className="space-y-4" aria-labelledby="portal-profile">
      <div>
        <h2 id="portal-profile" className="font-semibold text-deep-navy">My profile</h2>
        <p className="text-sm text-slate-600">
          Your profile is private until you choose to be listed in the alumni directory. You can hide or delete it at any time.
        </p>
      </div>
      {notice && <Notice tone="success">{notice}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
      <form className="surface-card space-y-4" onSubmit={save} noValidate>
        <div className="flex flex-wrap gap-4">
          {field('Display name', 'display_name', { maxLength: 80 })}
          {field('Class year', 'class_year', { type: 'number', placeholder: 'e.g. 2018' })}
        </div>
        <div className="flex flex-wrap gap-4">
          {field('Company', 'company', { maxLength: 120 })}
          {field('Role', 'role', { maxLength: 120 })}
          {field('City', 'city', { maxLength: 120 })}
        </div>
        {field('LinkedIn URL', 'linkedin_url', { type: 'url', maxLength: 300, placeholder: 'https://www.linkedin.com/in/…' })}
        <label className="block space-y-1">
          <span className="block text-sm font-medium text-deep-navy">Short note</span>
          <textarea className="ui-input" rows={3} maxLength={500} value={form.note} onChange={(e) => set('note', e.target.value)} />
        </label>
        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm">
            <input className="mt-1" type="checkbox" checked={form.coffee_chats} onChange={(e) => set('coffee_chats', e.target.checked)} />
            <span className="min-w-0 break-words">Open to coffee chats</span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input className="mt-1" type="checkbox" checked={form.listed} onChange={(e) => set('listed', e.target.checked)} />
            <span className="min-w-0 break-words">List me in the directory</span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input className="mt-1" type="checkbox" checked={form.show_email} onChange={(e) => set('show_email', e.target.checked)} />
            <span className="min-w-0 break-all">Show my email address ({email}) to other alumni in the directory</span>
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy !== ''}>{busy === 'save' ? 'Saving…' : 'Save'}</Button>
          {exists && (
            <>
              <Button variant="secondary" disabled={busy !== ''} onClick={hide}>Hide from directory</Button>
              <Button variant="danger" disabled={busy !== ''} onClick={() => setConfirmDelete(true)}>Delete my profile</Button>
            </>
          )}
        </div>
      </form>
      <ConfirmDialog
        open={confirmDelete}
        title="Delete your profile?"
        body="Your profile row is deleted immediately and disappears from the directory. Daily backups keep a copy for up to 30 days. Your sign-in is not affected, and you can create a new profile later."
        confirmLabel="Delete my profile"
        danger
        busy={busy === 'delete'}
        onConfirm={remove}
        onClose={() => setConfirmDelete(false)}
      />
    </section>
  );
}
