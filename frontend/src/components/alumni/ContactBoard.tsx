import { useState, type FormEvent } from 'react';
import { api } from '../../api';
import { Button, Notice } from '../ui/Primitives';
import { usePortal } from './portalContext';

/** A message to the alumni board's inbox. No email is sent; only the board reads it. */
export default function ContactBoard() {
  const { fail } = usePortal();
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (!subject.trim() || !body.trim()) { setError('Write a subject and a message.'); return; }
    setBusy(true);
    setError('');
    setSent(false);
    try {
      await api.alumni.contactBoard({ subject: subject.trim(), body: body.trim() });
      setSent(true);
      setSubject('');
      setBody('');
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="surface-card space-y-3" aria-labelledby="portal-contact">
      <div>
        <h2 id="portal-contact" className="font-semibold text-deep-navy">Contact the board</h2>
        <p className="text-sm text-slate-600">Your message goes to the alumni board’s inbox in this portal, with your name and sign-in address. Nothing is emailed.</p>
      </div>
      {sent && <Notice tone="success">Sent to the alumni board</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
      <form className="space-y-3" onSubmit={send}>
        <label className="block space-y-1">
          <span className="block text-sm font-medium text-deep-navy">Subject</span>
          <input className="ui-input" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label className="block space-y-1">
          <span className="block text-sm font-medium text-deep-navy">Message</span>
          <textarea className="ui-input" rows={5} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <Button type="submit" disabled={busy}>{busy ? 'Sending…' : 'Send message'}</Button>
      </form>
    </section>
  );
}
