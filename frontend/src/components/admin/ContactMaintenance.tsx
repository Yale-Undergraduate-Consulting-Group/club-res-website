import { useState } from 'react';
import { api } from '../../api';

/**
 * Destructive maintenance on the shared contact store.
 *
 * These three actions used to sit inside a collapsed "Company email formats"
 * panel on Find contacts, one click from a member's daily work - including
 * one that permanently deletes every contact in the club's database. They are
 * rare, irreversible and club-wide, which is what Admin is for.
 */
export default function ContactMaintenance() {
  const [domain, setDomain] = useState('');
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const scope = domain.trim();
  const scopeLabel = scope ? `contacts at ${scope}` : 'ALL contacts in the database';

  const run = async (label: string, work: () => Promise<string>) => {
    setBusy(label);
    setError('');
    setNote('');
    try {
      setNote(await work());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm space-y-4"
         data-section="contact-maintenance">
      <div>
        <h3 className="font-semibold text-deep-navy">Contact data maintenance</h3>
        <p className="text-[13px] text-slate-600 mt-1">
          Club-wide and irreversible. Leave the domain empty to act on every contact.
        </p>
      </div>

      <input
        type="text"
        value={domain}
        onChange={(event) => setDomain(event.target.value)}
        placeholder="Limit to a company domain (optional)"
        aria-label="Limit to a company domain"
        className="w-full max-w-sm px-3 py-2 rounded-xl border border-pale-sky text-sm"
      />

      {note && <p className="text-sm text-emerald-800">{note}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          disabled={!!busy}
          className="ui-button ui-button--secondary ui-button--sm"
          onClick={() => void run('identity', async () => {
            const res = await api.contacts.reconcileIdentity(scope || undefined);
            return `Identity pass: ${res.fixed} fixed, ${res.removed} removed, ${res.unchanged} unchanged.`;
          })}
        >
          {busy === 'identity' ? 'Reconciling…' : 'Fix identity mismatches'}
        </button>

        <button
          type="button"
          disabled={!!busy}
          className="ui-button ui-button--danger ui-button--sm"
          onClick={() => {
            if (!window.confirm('Delete saved contacts that look like nav or product labels (Gift Cards, Mac Studio)?')) return;
            void run('purge', async () => {
              const res = await api.contacts.purgeJunkContacts(scope || undefined);
              return `Removed ${res.removed} junk contact(s).`;
            });
          }}
        >
          {busy === 'purge' ? 'Purging…' : 'Remove nav junk contacts'}
        </button>

        <button
          type="button"
          disabled={!!busy}
          className="ui-button ui-button--danger ui-button--sm"
          onClick={() => {
            if (!window.confirm(`Clear ${scopeLabel}?\n\nThis permanently deletes those contacts plus related campaign rows, notes and generated emails.`)) return;
            const alsoCaches = window.confirm('Also clear learned email formats and discovery logs?\n\nOK = clear everything.\nCancel = delete contacts only, keeping the formats the club has learned.');
            if (!window.confirm(`Last chance: permanently delete ${scopeLabel}${alsoCaches ? ', email formats and discovery logs' : ''}. This cannot be undone.`)) return;
            void run('clear', async () => {
              const res = await api.contacts.clearAll({
                confirm: true,
                domain: scope || undefined,
                clear_pattern_cache: alsoCaches,
                clear_discovery_logs: alsoCaches,
              });
              return `Deleted ${res.contacts_deleted} contact(s), ${res.patterns_deleted} format(s), ${res.discovery_logs_deleted} log(s).`;
            });
          }}
        >
          {busy === 'clear' ? 'Clearing…' : 'Clear contacts & cache…'}
        </button>
      </div>
    </div>
  );
}
