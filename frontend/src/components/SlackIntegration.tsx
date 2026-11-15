import { useEffect, useState } from 'react';
import { api } from '../api';
import { Button, ConfirmDialog, Notice, StatusBadge } from './ui/Primitives';

type SlackStatus = { connected: boolean; team_name?: string };

/**
 * The member's Slack connection. `allowReconnect` (Profile → Integrations, the
 * settings page) turns the Connected badge into a control: hovering it reads
 * "Reconnect", and clicking asks for confirmation before running the install
 * again, which replaces the stored token - how a member picks up scopes the
 * app gained since they connected. Elsewhere (the Overview sidebar) the card
 * only reports the status.
 */
export default function SlackIntegration({ allowReconnect = false }: { allowReconnect?: boolean }) {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

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
      setConfirming(false);
    }
  };

  // No disconnect here: leaving the workspace is done in Slack, and revoking a
  // stored token is a support action (DELETE /api/auth/slack/disconnect).
  const connected = status?.connected === true;
  return (
    <section className="surface-card mb-6" aria-labelledby="slack-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="slack-heading" className="app-section-title">Club Slack workspace</h2>
        {connected && allowReconnect ? (
          <button
            type="button"
            className="group ui-status ui-status--success cursor-pointer"
            title="Reconnect Slack"
            aria-label="Connected. Reconnect Slack"
            onClick={() => setConfirming(true)}
          >
            <span className="group-hover:hidden group-focus-visible:hidden">Connected</span>
            <span className="hidden group-hover:inline group-focus-visible:inline">Reconnect</span>
          </button>
        ) : (
          <StatusBadge tone={connected ? 'success' : 'neutral'}>
            {status == null ? 'Checking connection' : connected ? 'Connected' : 'Not connected'}
          </StatusBadge>
        )}
      </div>
      <p className="text-sm text-slate-600 my-3">
        Connect the club workspace for shared notifications and the <code>/yucg</code> command in
        Slack. {status?.team_name ? `Connected to ${status.team_name}.` : ''}
      </p>
      {error && <Notice tone="danger" className="mb-3">{error}</Notice>}
      {status != null && !connected && (
        <Button disabled={busy} onClick={connect}>
          {busy ? 'Working…' : 'Connect Slack'}
        </Button>
      )}
      <ConfirmDialog
        open={confirming}
        title="You're already connected"
        body={`Slack is already connected${status?.team_name ? ` to ${status.team_name}` : ''}. Reconnect anyway? Slack asks you to approve the app again, and your connection is replaced with a new one.`}
        confirmLabel="Reconnect"
        busy={busy}
        onConfirm={() => void connect()}
        onClose={() => setConfirming(false)}
      />
    </section>
  );
}
