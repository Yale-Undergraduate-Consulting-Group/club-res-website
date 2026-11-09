import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Readiness } from '../api';
import { StatusBadge } from './ui/Primitives';

/** One setup step; `to` is where the member does it, absent when an admin does. */
type Step = { key: keyof Readiness; label: string; why: string; to?: string; required?: boolean };

const STEPS: Step[] = [
  { key: 'gmail', label: 'Connect your Gmail', why: 'Outreach goes out from your own account, so nothing can be sent until it is connected.', to: '/profile?view=integrations', required: true },
  { key: 'sign_off', label: 'Set your email sign-off', why: 'Your name, role and links under every email. Until then your Google name signs it.', to: '/profile?view=settings' },
  { key: 'slack', label: 'Connect Slack', why: 'Ask the club assistant about your outreach and get updates in Slack.', to: '/profile?view=integrations' },
  { key: 'project', label: 'Join a project team', why: 'An admin adds you to your project, which shares its contacts and documents with you.' },
];

/** Home's setup checklist: only the steps still missing; gone once all are done. */
export default function GettingStarted() {
  const [facts, setFacts] = useState<Partial<Readiness> | null>(null);

  useEffect(() => {
    // Setup status is a convenience: if it fails to load, Home shows without it.
    api.auth.readiness().then(setFacts).catch(() => setFacts(null));
  }, []);

  if (!facts) return null;
  const missing = STEPS.filter((step) => !facts[step.key]);
  if (missing.length === 0) return null;
  const done = STEPS.length - missing.length;

  return (
    <section className="surface-card p-5" aria-labelledby="getting-started-title" data-testid="getting-started">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="getting-started-title" className="app-section-title">Getting started</h2>
        <StatusBadge tone={facts.ready_to_send ? 'success' : 'warning'}>
          {facts.ready_to_send ? 'Ready to send' : 'Not ready to send'} · {done} of {STEPS.length} done
        </StatusBadge>
      </div>
      <ul className="space-y-3">
        {missing.map((step) => (
          <li key={step.key} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              {step.to ? <Link to={step.to} className="font-semibold text-deep-navy underline">{step.label}</Link> : <span className="font-semibold">{step.label}</span>}
              {step.required && <StatusBadge tone="danger">Required to send</StatusBadge>}
            </div>
            <p className="text-slate-600">{step.why}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
