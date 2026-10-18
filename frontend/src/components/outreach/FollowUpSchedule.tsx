import { useEffect, useState } from 'react';
import { api, type FollowUpScheduleRow } from '../../api';

/**
 * What the club is about to say next, and to whom.
 *
 * A sequence runs unattended once a campaign is released. Until now a member
 * could define steps and read a campaign, but could not answer "who hears
 * from us tomorrow" - the steps were a form at the bottom of a resources tab
 * and the schedule existed only inside the job. Both halves belong on one
 * screen, and the stopped list matters as much as the due list: a sequence
 * that quietly paused because its campaign needs attention looks identical to
 * one that finished.
 */
export default function FollowUpSchedule() {
  const [scheduled, setScheduled] = useState<FollowUpScheduleRow[]>([]);
  const [stopped, setStopped] = useState<FollowUpScheduleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.outreach.followUps.schedule()
      .then((res) => {
        if (cancelled) return;
        setScheduled(Array.isArray(res?.scheduled) ? res.scheduled : []);
        setStopped(Array.isArray(res?.stopped) ? res.stopped : []);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not read the schedule'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const overdue = scheduled.filter((row) => row.overdue);

  return (
    <div className="space-y-5" data-section="follow-up-schedule">
      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm space-y-2">
        <h2 className="text-lg font-semibold text-deep-navy">Follow-ups</h2>
        <p className="text-sm text-slate-600">
          Every follow-up queued across your campaigns. A step only sends while the person has
          not replied and their campaign is still sending — anything else is listed below as
          stopped, with the reason.
        </p>
        {!loading && (
          <p className="text-[13px] text-slate-500">
            {scheduled.length} queued
            {overdue.length > 0 ? ` · ${overdue.length} due now` : ''}
            {stopped.length > 0 ? ` · ${stopped.length} stopped` : ''}
          </p>
        )}
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm">
        <h3 className="font-semibold text-deep-navy mb-3">Queued</h3>
        {loading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : scheduled.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nothing queued. Follow-ups appear here once a campaign with a sequence has been released.
          </p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {scheduled.map((row) => (
              <li key={row.campaign_contact_id} className="py-3 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium text-deep-navy">
                    {row.contact_name || row.email}
                    {row.company ? <span className="text-slate-500 font-normal"> · {row.company}</span> : null}
                  </div>
                  <div className="text-xs text-slate-500">
                    {[row.sequence_name, row.campaign_name, row.next_subject].filter(Boolean).join(' · ')}
                  </div>
                </div>
                <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${
                  row.overdue ? 'bg-amber-100 text-amber-900' : 'bg-pale-sky/60 text-slate-700'}`}>
                  {row.overdue ? 'due now' : `due ${row.due_on ?? 'once sent'}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {stopped.length > 0 && (
        <div className="surface-card rounded-2xl border border-[var(--border)] p-5 sm:p-6 shadow-sm">
          <h3 className="font-semibold text-deep-navy mb-1">Stopped</h3>
          <p className="text-[13px] text-slate-500 mb-3">
            A reply is the reason you want. The others are worth reading: a paused campaign stops
            its follow-ups without saying so anywhere else.
          </p>
          <ul className="divide-y divide-[var(--border)]">
            {stopped.map((row) => (
              <li key={row.campaign_contact_id} className="py-2.5 flex flex-wrap items-baseline justify-between gap-3">
                <span className="text-sm text-deep-navy">
                  {row.contact_name || row.email}
                  {row.company ? <span className="text-slate-500"> · {row.company}</span> : null}
                </span>
                <span className="text-xs text-slate-600">{row.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
