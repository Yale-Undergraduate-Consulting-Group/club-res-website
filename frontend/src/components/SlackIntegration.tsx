import { useEffect, useState } from 'react';
import { api } from '../api';
import { Button, Notice, StatusBadge } from './ui/Primitives';

type SlackStatus = { connected: boolean; team_name?: string };

export default function SlackIntegration() {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    api.auth.slack.status()
      .then(setStatus)
      .catch((requestError: Error) => setError(requestError.message));
  };

  useEffect(() => {
    refresh();
    window.addEventListener('slack-integration-updated', refresh);
    return () => window.removeEventListener('slack-integration-updated', refresh);
  }, []);

  const connect = async () => {
    setBusy(true);
    setError('');
    try {
      const { redirect_url } = await api.auth.slack.connectUrl();
      window.location.assign(redirect_url);
    } catch (requestError) {
      setError((requestError as Error).message);
      setBusy(false);
    }
  };

  // No disconnect here: leaving the workspace is done in Slack, and a member
  // who reconnects simply reinstalls. Revoking a stored token is a support
  // action (DELETE /api/auth/slack/disconnect), not a button beside Connect.

  // Same shape as the Gmail card beside it: title and status on one row, the
  // explanation under it, then the action only when there is one to take.
  return (
    <section className="surface-card p-5 mb-6" aria-labelledby="slack-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="slack-heading" className="app-section-title">Club Slack workspace</h2>
        <StatusBadge tone={status?.connected ? 'success' : 'neutral'}>
          {status == null ? 'Checking connection' : status.connected ? 'Connected' : 'Not connected'}
        </StatusBadge>
      </div>
      <p className="text-sm text-slate-600 my-3">
        Connect the club workspace for shared notifications and the <code>/yucg</code> command in
        Slack. {status?.team_name ? `Connected to ${status.team_name}.` : ''}
      </p>
      {error && <Notice tone="danger" className="mb-3">{error}</Notice>}
      {!status?.connected && (
        <Button disabled={busy || status == null} onClick={connect}>
          {busy ? 'Working…' : 'Connect Slack'}
        </Button>
      )}
    </section>
  );
}
