import GmailConnection from '../components/GmailConnection';
import PageHeader from '../components/PageHeader';
import type { MemberProfile, Project, CustomFormat, LogEntry, Attachment } from '../api';
/**
 * Profile page - projects, experience, role, handles + Settings (moved from nav)
 */
import { useEffect, useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useTheme } from '../contexts/useTheme';
import AppTabMenu from '../components/AppTabMenu';
import AppSubnav from '../components/AppSubnav';
import SlackIntegration from '../components/SlackIntegration';
import { useUrlTab } from '../lib/useUrlTab';
import { getStoredPreferences, savePreferences, applyUserPreferences, resetPreferencesToDefault, type UserPreferences } from '../lib/userPreferences';

export default function Profile() {
  const { user } = useOutletContext<{ user: { email: string; name?: string; picture?: string; role?: string } }>();
  const { theme, setTheme } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useUrlTab<'profile' | 'integrations' | 'settings'>(
    ['profile', 'integrations', 'settings'],
    'profile',
    'tab',
  );

  useEffect(() => {
    const slack = searchParams.get('slack');
    if (slack === 'connected') {
      const next = new URLSearchParams(searchParams);
      next.delete('slack');
      setSearchParams(next, { replace: true });
      window.dispatchEvent(new CustomEvent('slack-integration-updated'));
    }
  }, [searchParams, setSearchParams]);
  const [, setProfile] = useState<MemberProfile>({});
  const [assignedProjects, setAssignedProjects] = useState<Project[]>([]);
  const [saved, setSaved] = useState(false);

  // Profile fields
  const [projects, setProjects] = useState('');
  const [experience, setExperience] = useState('');
  const [roleTitle, setRoleTitle] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [slackHandle, setSlackHandle] = useState('');
  const [otherHandles, setOtherHandles] = useState('');

  // Settings state (for Settings tab)
  const isAdmin = user?.role === 'admin';
  const [signOff, setSignOff] = useState({
    name: user?.name || '',
    pronouns: '',
    role: '',
    organization: 'Yale Undergraduate Consulting Group',
    linkedin: '',
    phone: '',
    logoUrl: '',
  });
  const [dailySendLimit, setDailySendLimit] = useState('');
  const [dailySendWarnAt, setDailySendWarnAt] = useState('');
  const [customFormats, setCustomFormats] = useState<CustomFormat[]>([]);
  const [loginLog, setLoginLog] = useState<LogEntry[]>([]);
  const [notifPrefs, setNotifPrefs] = useState({ admin_digest: true, campaign_summary: false });
  const [newFormatName, setNewFormatName] = useState('');
  const [newFormatPattern, setNewFormatPattern] = useState('');
  const [attachmentLibrary, setAttachmentLibrary] = useState<Attachment[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [formatAdded, setFormatAdded] = useState(false);
  const [error, setError] = useState('');
  const [accentColor, setAccentColor] = useState(() => getStoredPreferences().accent);
  const [compactMode, setCompactMode] = useState(() => getStoredPreferences().compact);
  const [uiFontSize, setUiFontSize] = useState<UserPreferences['fontSize']>(() => getStoredPreferences().fontSize);
  const [reduceMotion, setReduceMotion] = useState(() => getStoredPreferences().reduceMotion);

  useEffect(() => {
    api.auth.profile.get().then((p) => {
      setProfile(p);
      setProjects(p.projects || '');
      setExperience(p.experience || '');
      setRoleTitle(p.role_title || user?.role || '');
      setLinkedinUrl(p.linkedin_url || '');
      setSlackHandle(p.slack_handle || '');
      setOtherHandles(p.other_handles || '');
    }).catch(() => {});
    api.auth.myProjects().then(setAssignedProjects).catch(() => setAssignedProjects([]));
  }, [user?.role]);

  useEffect(() => {
    if (activeTab !== 'settings') return;
    api.settings.get().then((s) => {
      setSignOff({
        name: s.sign_off_name || user?.name || '',
        pronouns: s.sign_off_pronouns || '',
        role: s.sign_off_role || '',
        organization: s.sign_off_organization || 'Yale Undergraduate Consulting Group',
        linkedin: s.sign_off_linkedin || '',
        phone: s.sign_off_phone || '',
        logoUrl: s.sign_off_logo_url || '',
      });
      setDailySendLimit(s.daily_send_limit != null ? String(s.daily_send_limit) : '');
      setDailySendWarnAt(s.daily_send_warn_at ? String(s.daily_send_warn_at) : '');
    }).catch(() => {});
    api.auth.notificationPrefs.get().then(setNotifPrefs).catch(() => {});
    if (isAdmin) {
      api.settings.customFormats.list().then(setCustomFormats).catch(() => []);
      api.admin.loginLog().then(setLoginLog).catch(() => []);
    }
  }, [activeTab, isAdmin, user?.name]);

  useEffect(() => {
    if (!isAdmin) return;
    api.attachments.list().then(setAttachmentLibrary).catch(() => setAttachmentLibrary([]));
  }, [isAdmin]);

  const saveProfile = async () => {
    setError('');
    try {
      await api.auth.profile.update({
        projects: projects.trim() || undefined,
        experience: experience.trim() || undefined,
        role_title: roleTitle.trim() || undefined,
        linkedin_url: linkedinUrl.trim() || undefined,
        slack_handle: slackHandle.trim() || undefined,
        other_handles: otherHandles.trim() || undefined,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setError(eMessage || 'Failed to save');
    }
  };

  const saveSettings = async () => {
    setError('');
    try {
      const limit = parseInt(dailySendLimit, 10);
      const warnAt = parseInt(dailySendWarnAt, 10);
      await api.settings.update({
        sign_off_name: signOff.name,
        sign_off_pronouns: signOff.pronouns,
        sign_off_role: signOff.role,
        sign_off_organization: signOff.organization,
        sign_off_linkedin: signOff.linkedin,
        sign_off_phone: signOff.phone,
        sign_off_logo_url: signOff.logoUrl,
        ...(Number.isFinite(limit) ? { daily_send_limit: limit } : {}),
        ...(Number.isFinite(warnAt) ? { daily_send_warn_at: warnAt } : {}),
      });
      // The server clamps the limit to the club ceiling, so reflect what stuck.
      const saved = await api.settings.get();
      setDailySendLimit(saved.daily_send_limit != null ? String(saved.daily_send_limit) : '');
      setDailySendWarnAt(saved.daily_send_warn_at ? String(saved.daily_send_warn_at) : '');
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setError(eMessage || 'Failed to save');
    }
  };

  const saveNotifPrefs = async () => {
    setError('');
    try {
      await api.auth.notificationPrefs.update(notifPrefs);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setError(eMessage || 'Failed to save');
    }
  };

  const addFormat = async () => {
    if (!newFormatName.trim() || !newFormatPattern.trim()) return;
    setError('');
    try {
      await api.settings.customFormats.add({ name: newFormatName.trim(), pattern: newFormatPattern.trim() });
      setCustomFormats(await api.settings.customFormats.list());
      setNewFormatName('');
      setNewFormatPattern('');
      setFormatAdded(true);
      setTimeout(() => setFormatAdded(false), 2000);
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setError(eMessage || 'Failed to add format');
    }
  };

  const removeFormat = async (id: number) => {
    try {
      await api.settings.customFormats.delete(id);
      setCustomFormats(await api.settings.customFormats.list());
    } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed';
      setError(eMessage || 'Failed to remove');
    }
  };

  return (
    <div className="app-workspace max-w-3xl">
      <PageHeader title="Profile & preferences" subtitle="Manage your identity, connected accounts, and personal preferences." />

      <AppTabMenu
        className="mb-6"
        tabs={[
          { id: 'profile', label: 'Profile' },
          { id: 'integrations', label: 'Integrations' },
          { id: 'settings', label: 'Preferences' },
        ]}
        active={activeTab}
        onChange={(id) => setActiveTab(id as typeof activeTab)}
        label="Profile views"
      />

      {activeTab === 'profile' && (
        <div className="space-y-6">
          {assignedProjects.length > 0 && (
            <div className="surface-card shadow-sm rounded-xl p-6">
              <h2 className="font-semibold text-deep-navy mb-4">Your Project Assignments</h2>
              <p className="text-sm text-slate-600 mb-4">Projects you&apos;re assigned to this semester (managed by admins).</p>
              <ul className="space-y-2">
                {assignedProjects.map((p) => (
                  <li key={p.id} className="flex items-center gap-2 py-2 border-b border-pale-sky/50 last:border-0">
                    <span className="font-medium">{p.semester ? `${p.semester} — ` : ''}{p.name}</span>
                    {p.role_in_project && <span className="text-sm text-slate-500">({p.role_in_project})</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="surface-card shadow-sm rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Your Profile</h2>
            <p className="text-sm text-slate-600 mb-4">Build your profile so teammates can see your projects, experience, and how to connect.</p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Projects / focus areas (free-form)</label>
                <input
                  value={projects}
                  onChange={(e) => setProjects(e.target.value)}
                  placeholder="e.g. Tech sector outreach, Healthcare initiative"
                  className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Experience</label>
                <textarea
                  value={experience}
                  onChange={(e) => setExperience(e.target.value)}
                  placeholder="e.g. 2 years consulting, finance background"
                  className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                  rows={3}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Role</label>
                <input
                  value={roleTitle}
                  onChange={(e) => setRoleTitle(e.target.value)}
                  placeholder="e.g. Analyst, Project Lead"
                  className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Handles & connections</label>
                <div className="space-y-2">
                  <input
                    value={linkedinUrl}
                    onChange={(e) => setLinkedinUrl(e.target.value)}
                    placeholder="LinkedIn URL"
                    className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                  />
                  <input
                    value={slackHandle}
                    onChange={(e) => setSlackHandle(e.target.value)}
                    placeholder="Slack handle (e.g. @username)"
                    className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                  />
                  <input
                    value={otherHandles}
                    onChange={(e) => setOtherHandles(e.target.value)}
                    placeholder="Other (Google Drive, Teams, etc.)"
                    className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                  />
                </div>
              </div>
              <button onClick={saveProfile} className="ui-button ui-button--primary">
                {saved ? 'Saved!' : 'Save Profile'}
              </button>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'integrations' && <><GmailConnection /><SlackIntegration /></>}

      {activeTab === 'settings' && (
        <div className="space-y-8">
          <div className="surface-card shadow-sm rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)] mb-4">Appearance</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">Choose light or dark theme.</p>
            <AppSubnav
              items={[
                { id: 'light', label: 'Light' },
                { id: 'dark', label: 'Dark' },
              ]}
              active={theme}
              onChange={(id) => setTheme(id as 'light' | 'dark')}
            />
            <div className="mt-6 pt-4 border-t border-pale-sky dark:border-slate-600 space-y-4">
              <div className="flex items-center gap-3">
                <label className="text-sm font-medium text-deep-navy dark:text-[var(--text-primary)]">Accent Color</label>
                <input
                  type="color"
                  value={accentColor}
                  onChange={(e) => {
                    const v = e.target.value;
                    setAccentColor(v);
                    savePreferences({ accent: v });
                    applyUserPreferences({ ...getStoredPreferences(), accent: v });
                  }}
                  className="w-10 h-10 rounded border border-pale-sky dark:border-slate-500 cursor-pointer"
                />
                <span className="text-xs text-slate-500 dark:text-slate-400">Buttons and links</span>
              </div>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={compactMode}
                  onChange={(e) => {
                    const v = e.target.checked;
                    setCompactMode(v);
                    savePreferences({ compact: v });
                    applyUserPreferences({ ...getStoredPreferences(), compact: v });
                  }}
                />
                <span className="text-sm text-deep-navy dark:text-[var(--text-primary)]">Compact Mode (tighter spacing)</span>
              </label>
              <div className="flex items-center gap-3">
                <label className="text-sm font-medium text-deep-navy dark:text-[var(--text-primary)]">UI Font Size</label>
                <select
                  value={uiFontSize}
                  onChange={(e) => {
                    const v = e.target.value as UserPreferences['fontSize'];
                    setUiFontSize(v);
                    savePreferences({ fontSize: v });
                    applyUserPreferences({ ...getStoredPreferences(), fontSize: v });
                  }}
                  className="px-3 py-2 rounded-lg border border-pale-sky dark:border-slate-600 bg-white dark:bg-slate-700 text-deep-navy dark:text-[var(--text-primary)]"
                >
                  <option value="small">Small</option>
                  <option value="medium">Medium</option>
                  <option value="large">Large</option>
                </select>
              </div>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={reduceMotion}
                  onChange={(e) => {
                    const v = e.target.checked;
                    setReduceMotion(v);
                    savePreferences({ reduceMotion: v });
                    applyUserPreferences({ ...getStoredPreferences(), reduceMotion: v });
                  }}
                />
                <span className="text-sm text-deep-navy dark:text-[var(--text-primary)]">Reduce Motion</span>
              </label>
            </div>
            <div className="mt-6 pt-4 border-t border-pale-sky dark:border-slate-600">
              <button
                type="button"
                onClick={() => {
                  const def = resetPreferencesToDefault();
                  setAccentColor(def.accent);
                  setCompactMode(def.compact);
                  setUiFontSize(def.fontSize);
                  setReduceMotion(def.reduceMotion);
                }}
                className="ui-button ui-button--secondary"
              >
                Revert to Default
              </button>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">Restore Original Appearance Settings.</p>
            </div>
          </div>
          <div className="surface-card shadow-sm rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Notification Preferences</h2>
            <label className="flex items-center gap-2 mb-2">
              <input type="checkbox" checked={notifPrefs.admin_digest} onChange={(e) => setNotifPrefs((p) => ({ ...p, admin_digest: e.target.checked }))} />
              <span className="text-sm">Admin digest</span>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={notifPrefs.campaign_summary} onChange={(e) => setNotifPrefs((p) => ({ ...p, campaign_summary: e.target.checked }))} />
              <span className="text-sm">Campaign summary</span>
            </label>
            <button onClick={saveNotifPrefs} className="ui-button ui-button--primary mt-4">Save Preferences</button>
          </div>
              <div className="surface-card shadow-sm rounded-xl p-6">
                <h2 className="font-semibold text-deep-navy mb-4">Email sign-off</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="text-sm text-slate-600">Name
                    <input value={signOff.name} onChange={(e) => setSignOff((v) => ({ ...v, name: e.target.value }))} className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600">Pronouns
                    <input value={signOff.pronouns} onChange={(e) => setSignOff((v) => ({ ...v, pronouns: e.target.value }))} placeholder="he/him" className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600">Role
                    <input value={signOff.role} onChange={(e) => setSignOff((v) => ({ ...v, role: e.target.value }))} placeholder="Director of Client Recruitment" className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600">Organization
                    <input value={signOff.organization} onChange={(e) => setSignOff((v) => ({ ...v, organization: e.target.value }))} className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600">LinkedIn URL
                    <input type="url" value={signOff.linkedin} onChange={(e) => setSignOff((v) => ({ ...v, linkedin: e.target.value }))} placeholder="https://linkedin.com/in/..." className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600">Phone
                    <input type="tel" value={signOff.phone} onChange={(e) => setSignOff((v) => ({ ...v, phone: e.target.value }))} placeholder="+1 203 555 0123" className="ui-input mt-1 w-full" />
                  </label>
                  <label className="text-sm text-slate-600 sm:col-span-2">YUCG logo URL
                    <input type="url" value={signOff.logoUrl} onChange={(e) => setSignOff((v) => ({ ...v, logoUrl: e.target.value }))} placeholder="https://..." className="ui-input mt-1 w-full" />
                  </label>
                </div>
                <div className="mt-5 border-t border-slate-200 pt-4 flex items-start gap-4 text-sm text-slate-700">
                  {signOff.logoUrl && <img src={signOff.logoUrl} alt="YUCG" className="w-[72px] h-auto object-contain" />}
                  <div>
                    <div className="font-bold text-deep-navy">{signOff.name || 'Your name'} {signOff.pronouns && <em className="font-normal">({signOff.pronouns})</em>}</div>
                    {signOff.role && <div>{signOff.role}</div>}
                    <div>{signOff.organization}</div>
                    {(signOff.linkedin || signOff.phone) && <div className="mt-0.5 text-deep-navy">{signOff.linkedin ? 'LinkedIn' : ''}{signOff.linkedin && signOff.phone ? ' | ' : ''}{signOff.phone}</div>}
                  </div>
                </div>
              </div>
              <div className="surface-card shadow-sm rounded-xl p-6">
                <h2 className="font-semibold text-deep-navy mb-1">Send pacing</h2>
                <p className="text-sm text-slate-600 mb-3">
                  The send loop claims at most this many first sends per day from your account. A
                  larger release is paced over several days rather than refused.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm text-slate-600 mb-1" htmlFor="daily-send-limit">
                      Daily send limit
                    </label>
                    <input
                      id="daily-send-limit"
                      type="number"
                      min={1}
                      value={dailySendLimit}
                      onChange={(e) => setDailySendLimit(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      You can lower this but not raise it above the club ceiling.
                    </p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-600 mb-1" htmlFor="daily-send-warn">
                      Warn me above
                    </label>
                    <input
                      id="daily-send-warn"
                      type="number"
                      min={0}
                      value={dailySendWarnAt}
                      onChange={(e) => setDailySendWarnAt(e.target.value)}
                      placeholder="No warning"
                      className="w-full px-3 py-2 rounded-lg border border-slate-300 text-slate-800"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Releasing more first sends than this shows a warning.
                    </p>
                  </div>
                </div>
              </div>
          <button onClick={saveSettings} className="ui-button ui-button--primary">{saved ? "Saved" : "Save settings"}</button>
          {isAdmin && (
            <>
              <div className="surface-card shadow-sm rounded-xl p-6">
                <h2 className="font-semibold text-deep-navy mb-4">Login Log</h2>
                {loginLog.length === 0 ? <p className="text-slate-500 text-sm">No logins yet.</p> : (
                  <ul className="space-y-2 max-h-48 overflow-y-auto">
                    {loginLog.map((l) => (
                      <li key={l.id} className="flex flex-wrap gap-2 text-sm py-2 border-b border-slate-100">
                        <span className="font-medium">{l.name || '—'}</span>
                        <span className="text-slate-500">{l.email}</span>
                        <span className="text-slate-400 text-xs ml-auto">{new Date(l.created_at).toLocaleString()}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="surface-card shadow-sm rounded-xl p-6">
                <h2 className="font-semibold text-deep-navy mb-4">Email Attachments</h2>
                <div className="mt-4 pt-4 border-t border-pale-sky">
                    <input type="file" id="att-upload" className="hidden" accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif"
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        if (!f) return;
                        setAttachmentUploading(true);
                        try {
                          await api.attachments.upload(f, f.name);
                          setAttachmentLibrary(await api.attachments.list());
                        } catch (err) {
      const errMessage = err instanceof Error ? err.message : 'Request failed'; alert(errMessage); } finally { setAttachmentUploading(false); e.target.value = ''; }
                      }} />
                    <label htmlFor="att-upload" className={`inline-block px-4 py-2 rounded-lg border text-sm cursor-pointer ${attachmentUploading ? 'opacity-50' : ''}`}>+ Upload</label>
                    {attachmentLibrary.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {attachmentLibrary.map((a) => (
                          <li key={a.id} className="flex justify-between text-sm">
                            <span className="truncate">{a.display_name || a.filename}</span>
                            <button onClick={async () => { try { await api.attachments.delete(a.id); setAttachmentLibrary(await api.attachments.list()); } catch (e) {
      const eMessage = e instanceof Error ? e.message : 'Request failed'; alert(eMessage); } }} className="ui-button ui-button--danger ui-button--sm">Remove</button>
                          </li>
                        ))}
                      </ul>
                    )}
                </div>
              </div>
              <div className="surface-card shadow-sm rounded-xl p-6">
                <h2 className="font-semibold text-deep-navy mb-4">Custom Email Formats</h2>
                <div className="flex gap-2 mb-4">
                  <input value={newFormatName} onChange={(e) => setNewFormatName(e.target.value)} placeholder="Name" className="flex-1 px-3 py-2 rounded-lg border" />
                  <input value={newFormatPattern} onChange={(e) => setNewFormatPattern(e.target.value)} placeholder="Pattern" className="flex-1 px-3 py-2 rounded-lg border" />
                  <button onClick={addFormat} disabled={!newFormatName.trim() || !newFormatPattern.trim()} className="ui-button ui-button--primary">{formatAdded ? 'Added!' : 'Add'}</button>
                </div>
                <ul className="space-y-2">
                  {customFormats.map((f) => (
                    <li key={f.id} className="flex justify-between py-2 border-b text-sm">
                      <span className="font-mono">{f.name}: {f.pattern}</span>
                      <button onClick={() => removeFormat(f.id)} className="ui-button ui-button--danger ui-button--sm">Remove</button>
                    </li>
                  ))}
                </ul>
              </div>
              <button onClick={saveSettings} className="ui-button ui-button--primary">{saved ? 'Saved!' : 'Save Settings'}</button>
            </>
          )}
        </div>
      )}

      {error && <p className="text-red-600 text-sm">{error}</p>}
    </div>
  );
}
