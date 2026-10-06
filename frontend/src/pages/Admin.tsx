import PageHeader from '../components/PageHeader';
import Invitations from '../components/Invitations';
import ContactMaintenance from '../components/admin/ContactMaintenance';
import { useEffect, useState, Fragment } from 'react';
import { useOutletContext } from 'react-router-dom';
import QRCode from 'qrcode';
import { api, type FirecrawlStatus } from '../api';
import { StatusBadge } from '../components/ui/Primitives';
import { useToast } from '../contexts/useToast';
import { AnimatedEventTypeChart, AnimatedResourceChart } from '../components/AnimatedOperationsCharts';
import AppTabMenu from '../components/AppTabMenu';
import { useUrlTab } from '../lib/useUrlTab';

export default function Admin() {
  const { user } = useOutletContext<{ user: { id?: number; email: string; role?: string } }>();
  const toast = useToast();
  const [activeTab, setActiveTab] = useUrlTab<'users' | 'projects' | 'audit' | 'apikeys' | '2fa' | 'catalog' | 'operations'>(
    ['users', 'projects', 'audit', 'apikeys', '2fa', 'catalog', 'operations'],
    'users',
  );
  const [catalog, setCatalog] = useState<Awaited<ReturnType<typeof api.admin.catalog>> | null>(null);
  const [users, setUsers] = useState<Awaited<ReturnType<typeof api.admin.users.list>>>([]);
  const [projects, setProjects] = useState<Awaited<ReturnType<typeof api.admin.projects.list>>>([]);
  const [projectForm, setProjectForm] = useState({ name: '', semester: '', description: '', client_name: '', discussable: false });
  const [selectedProject, setSelectedProject] = useState<Awaited<ReturnType<typeof api.admin.projects.list>>[number] | null>(null);
  const [projectAssignments, setProjectAssignments] = useState<Awaited<ReturnType<typeof api.admin.projects.assignments>>>([]);
  const [assignUserModal, setAssignUserModal] = useState<{ projectId: number; projectName: string } | null>(null);
  const [assignRole, setAssignRole] = useState('');
  const [editProjectForm, setEditProjectForm] = useState({ name: '', semester: '', description: '', client_name: '', discussable: false });
  const [editProjectSaving, setEditProjectSaving] = useState(false);
  const [auditLog, setAuditLog] = useState<Awaited<ReturnType<typeof api.admin.auditLog>>>([]);
  const [apiKeys, setApiKeys] = useState<Awaited<ReturnType<typeof api.admin.apiKeys.list>>>([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyResult, setNewKeyResult] = useState('');
  const [twoFactorCode, setTwoFactorCode] = useState('');
  const [twoFactorSetup, setTwoFactorSetup] = useState<{ provisioning_uri: string; secret: string } | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [qrExpiresAt, setQrExpiresAt] = useState<number | null>(null);
  const [twoFactorStatus, setTwoFactorStatus] = useState<'enabled' | 'pending' | 'not_setup' | null>(null);
  const [error, setError] = useState('');
  const [opsDays, setOpsDays] = useState(30);
  const [opsGroupBy, setOpsGroupBy] = useState<'hour' | 'day_of_week'>('hour');
  const [opsAggregates, setOpsAggregates] = useState<{ by_event_type: { event_type: string; count: number }[]; by_resource_type: { resource_type: string; count: number }[]; days: number } | null>(null);
  const [opsHeatmap, setOpsHeatmap] = useState<{
    group_by: string;
    grid: Record<string, Record<string, number>>;
    rows: Record<string, unknown>[];
    matrix_2d?: { row_labels: string[]; col_labels: string[]; values: number[][] };
  } | null>(null);
  const [opsEvents, setOpsEvents] = useState<Awaited<ReturnType<typeof api.admin.operations.events>>>([]);
  const [firecrawlStatus, setFirecrawlStatus] = useState<FirecrawlStatus | null>(null);
  const [firecrawlTesting, setFirecrawlTesting] = useState(false);
  const [firecrawlError, setFirecrawlError] = useState('');

  const opsMatrix2d = opsHeatmap?.matrix_2d;

  useEffect(() => {
    if (user?.role !== 'admin') return;
    if (activeTab === 'users') api.admin.users.list().then(setUsers).catch((e: Error) => setError(e.message));
    if (activeTab === 'projects') api.admin.projects.list().then(setProjects).catch((e: Error) => setError(e.message));
    if (activeTab === 'audit') api.admin.auditLog().then(setAuditLog).catch(() => setAuditLog([]));
    if (activeTab === 'apikeys') api.admin.apiKeys.list().then(setApiKeys).catch(() => setApiKeys([]));
    if (activeTab === '2fa') api.admin.twoFactor.status().then((s) => setTwoFactorStatus(s.status)).catch(() => setTwoFactorStatus('not_setup'));
    if (activeTab === 'catalog') api.admin.catalog().then(setCatalog).catch(() => setCatalog({ bucket: null, objects: [], prefixes: [] }));
    if (activeTab === 'operations') {
      api.admin.operations.aggregates(opsDays).then(setOpsAggregates).catch(() => setOpsAggregates({ by_event_type: [], by_resource_type: [], days: 30 }));
      api.admin.operations.heatmap({ days: opsDays, group_by: opsGroupBy }).then(setOpsHeatmap).catch(() => setOpsHeatmap(null));
      api.admin.operations.events({ limit: 200, days: opsDays }).then(setOpsEvents).catch(() => setOpsEvents([]));
    }
  }, [user?.role, activeTab, opsDays, opsGroupBy]);

  useEffect(() => {
    if (selectedProject?.id) {
      api.admin.projects.assignments(selectedProject.id).then(setProjectAssignments).catch(() => setProjectAssignments([]));
    }
  }, [selectedProject?.id]);

  const selectProject = (p: NonNullable<typeof selectedProject>) => {
    setSelectedProject(p);
    setEditProjectForm({
      name: p.name || '',
      semester: p.semester || '',
      description: p.description || '',
      client_name: p.client_name || '',
      discussable: Boolean(p.discussable),
    });
  };

  const handleSaveProjectTagging = async () => {
    if (!selectedProject?.id) return;
    setEditProjectSaving(true);
    setError('');
    try {
      const updated = await api.admin.projects.update(selectedProject.id, {
        name: editProjectForm.name.trim() || undefined,
        semester: editProjectForm.semester.trim() || undefined,
        description: editProjectForm.description.trim() || undefined,
        client_name: editProjectForm.client_name.trim() || undefined,
        discussable: editProjectForm.discussable,
      });
      setSelectedProject(updated);
      api.admin.projects.list().then(setProjects).catch(() => {});
      toast.addToast('Project updated.', 'success');
    } catch (e) {
      const msg = (e as Error)?.message || 'Failed';
      setError(msg);
      toast.addToast(msg, 'error');
    } finally {
      setEditProjectSaving(false);
    }
  };

  const handleCreateKey = async () => {
    if (!newKeyName.trim()) return;
    setError('');
    setNewKeyResult('');
    try {
      const res = await api.admin.apiKeys.create(newKeyName.trim());
      setNewKeyName('');
      setNewKeyResult(res.key || '');
      api.admin.apiKeys.list().then(setApiKeys).catch(() => {});
      toast.addToast('API key created. Copy it now—it won\'t be shown again.', 'success');
    } catch (e) {
      const msg = (e as Error)?.message || 'Failed';
      setError(msg);
      toast.addToast(msg, 'error');
    }
  };

  const QR_EXPIRY_SECONDS = 60;

  const handle2FASetup = async () => {
    setError('');
    setTwoFactorSetup(null);
    setQrDataUrl(null);
    setQrExpiresAt(null);
    try {
      const res = await api.admin.twoFactor.setup();
      setTwoFactorSetup({ provisioning_uri: res.provisioning_uri, secret: res.secret });
      const dataUrl = await QRCode.toDataURL(res.provisioning_uri, { width: 256, margin: 2 });
      setQrDataUrl(dataUrl);
      setQrExpiresAt(Date.now() + QR_EXPIRY_SECONDS * 1000);
      setTwoFactorStatus('pending');
    } catch (e) {
      const msg = (e as Error)?.message || 'Failed';
      setError(msg);
      toast.addToast(msg, 'error');
    }
  };

  const [qrCountdown, setQrCountdown] = useState(0);
  useEffect(() => {
    if (!qrExpiresAt) return;
    const tick = () => {
      const left = Math.ceil((qrExpiresAt - Date.now()) / 1000);
      if (left <= 0) {
        setQrDataUrl(null);
        setQrExpiresAt(null);
        setQrCountdown(0);
        toast.addToast('QR code expired. Click Regenerate to get a new one.', 'error', 8000);
        return;
      }
      setQrCountdown(left);
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [qrExpiresAt, toast]);

  const handle2FAVerify = async () => {
    if (!twoFactorCode.trim()) return;
    setError('');
    try {
      await api.admin.twoFactor.verify(twoFactorCode.trim());
      setTwoFactorCode('');
      setTwoFactorSetup(null);
      setQrDataUrl(null);
      setTwoFactorStatus('enabled');
      toast.addToast('2FA enabled successfully.', 'success');
    } catch (e) {
      const msg = (e as Error)?.message || 'Failed';
      setError(msg);
      toast.addToast(msg, 'error');
    }
  };

  const handle2FADisable = async () => {
    if (!twoFactorCode.trim()) return;
    setError('');
    try {
      await api.admin.twoFactor.disable(twoFactorCode.trim());
      setTwoFactorCode('');
      setTwoFactorStatus('not_setup');
      toast.addToast('2FA disabled.', 'success');
    } catch (e) {
      const msg = (e as Error)?.message || 'Failed';
      setError(msg);
      toast.addToast(msg, 'error');
    }
  };

  const handle2FAReset = async () => {
    if (!confirm('Clear 2FA and any pending setup? Use this if you\'re stuck (e.g. lost phone before verifying).')) return;
    setError('');
    try {
      await api.admin.twoFactor.reset();
      setTwoFactorCode('');
      setTwoFactorSetup(null);
      setQrDataUrl(null);
      setTwoFactorStatus('not_setup');
      toast.addToast('2FA reset. You can set up again.', 'success');
    } catch (e) {
      toast.addToast((e as Error)?.message || 'Failed', 'error');
    }
  };

  if (user?.role !== 'admin') {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-2xl font-bold text-deep-navy mb-6">Admin Access Required</h1>
        <p className="text-slate-600">You need admin privileges to access this page.</p>
      </div>
    );
  }

  return (
    <div className="app-workspace max-w-5xl">
      <PageHeader title="Admin" subtitle="Manage members, project access, and club settings." actions={
        <button
          onClick={() => api.admin.exportAllZip().then(() => toast.addToast('Export all (ZIP) downloaded.', 'success')).catch((e) => toast.addToast((e as Error).message, 'error'))}
          className="ui-button ui-button--secondary"
        >
          Export all (ZIP)
        </button>
      } />

      <AppTabMenu
        tabs={[{ id: 'users', label: 'Members' }, { id: 'projects', label: 'Projects' }, { id: 'audit', label: 'Activity' }, { id: 'apikeys', label: 'Settings' }]}
        active={['apikeys', '2fa', 'catalog', 'operations'].includes(activeTab) ? 'apikeys' : activeTab}
        onChange={(id) => setActiveTab(id as typeof activeTab)}
        label="Admin sections"
      />
      {['apikeys', '2fa', 'catalog', 'operations'].includes(activeTab) && <AppTabMenu
        tabs={[{ id: 'apikeys', label: 'API keys' }, { id: '2fa', label: 'Account security' }, { id: 'catalog', label: 'Storage' }, { id: 'operations', label: 'Operations' }]}
        active={activeTab}
        onChange={(id) => setActiveTab(id as typeof activeTab)}
        label="Administrative settings"
      />}


      {error && <p className="text-red-600 text-sm mb-4">{error}</p>}

      {activeTab === 'users' && (
        <div className="space-y-6">
          <Invitations />
          <div className="surface-card rounded-xl p-6">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
              <h2 className="font-semibold text-deep-navy">Members</h2>
              <button
                type="button"
                onClick={async () => {
                  try {
                    await api.admin.users.exportExcel();
                    toast.addToast('Export downloaded.', 'success');
                  } catch (e) {
                    toast.addToast((e as Error)?.message || 'Export failed', 'error');
                  }
                }}
                className="ui-button ui-button--primary"
              >
                Export to Excel
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-pale-sky">
                    <th className="text-left py-2">Email</th>
                    <th className="text-left py-2">Name</th>
                    <th className="text-left py-2">Role</th>
                    <th className="text-left py-2">Status</th>
                    <th className="text-left py-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id} className="border-b border-pale-sky/50">
                      <td className="py-2">{u.email}</td>
                      <td className="py-2">{u.name || '—'}</td>
                      <td className="py-2">
                        <select
                          aria-label={`Role for ${u.email}`}
                          value={u.role || 'standard'}
                          onChange={async (e) => {
                            try {
                              await api.admin.users.updateRole(u.id, e.target.value);
                              api.admin.users.list().then(setUsers).catch(() => {});
                            } catch (err) {
                              alert((err as Error)?.message);
                            }
                          }}
                          disabled={u.id === user?.id}
                          className="px-2 py-1 rounded border border-pale-sky"
                        >
                          <option value="admin">Admin</option>
                          <option value="standard">Standard</option>
                        </select>
                      </td>
                      <td className="py-2">{u.is_active ? 'Active' : 'Inactive'}</td>
                      <td className="py-2">
                        {u.id !== user?.id && (
                          <button
                            onClick={async () => {
                              try {
                                await api.admin.users.updateStatus(u.id, !u.is_active);
                                api.admin.users.list().then(setUsers).catch(() => {});
                              } catch (err) {
                                alert((err as Error)?.message);
                              }
                            }}
                            className="ui-button ui-button--danger ui-button--sm"
                          >
                            {u.is_active ? 'Deactivate' : 'Activate'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'projects' && (
        <div className="space-y-6">
          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Create Project</h2>
            <p className="text-sm text-slate-600 mb-4">Add semester + client projects (e.g. Spring 2026 - Project Lego). Assign team members to each project.</p>
            <div className="flex flex-wrap gap-2 mb-2">
              <input
                value={projectForm.name}
                onChange={(e) => setProjectForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="Project name (e.g. Project Lego)"
                className="px-3 py-2 rounded-lg border border-pale-sky"
              />
              <input
                value={projectForm.semester}
                onChange={(e) => setProjectForm((p) => ({ ...p, semester: e.target.value }))}
                placeholder="Semester (e.g. Spring 2026)"
                className="px-3 py-2 rounded-lg border border-pale-sky"
              />
              <input
                value={projectForm.description}
                onChange={(e) => setProjectForm((p) => ({ ...p, description: e.target.value }))}
                placeholder="Description (optional)"
                className="px-3 py-2 rounded-lg border border-pale-sky flex-1 min-w-[200px]"
              />
            </div>
            <div className="flex flex-wrap items-end gap-2 mb-4">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Real client name (only if this can be named)</label>
                <input
                  value={projectForm.client_name}
                  onChange={(e) => setProjectForm((p) => ({ ...p, client_name: e.target.value }))}
                  placeholder="e.g. Google — leave blank if under NDA"
                  className="px-3 py-2 rounded-lg border border-pale-sky min-w-[220px]"
                />
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-700 px-3 py-2">
                <input
                  type="checkbox"
                  checked={projectForm.discussable}
                  onChange={(e) => setProjectForm((p) => ({ ...p, discussable: e.target.checked }))}
                />
                Can be named in outreach (not under NDA)
              </label>
              <button
                onClick={async () => {
                  if (!projectForm.name.trim()) return;
                  setError('');
                  try {
                    await api.admin.projects.create({
                      name: projectForm.name.trim(),
                      semester: projectForm.semester.trim() || undefined,
                      description: projectForm.description.trim() || undefined,
                      client_name: projectForm.client_name.trim() || undefined,
                      discussable: projectForm.discussable,
                    });
                    setProjectForm({ name: '', semester: '', description: '', client_name: '', discussable: false });
                    api.admin.projects.list().then(setProjects).catch(() => {});
                  } catch (e) {
                    setError((e as Error)?.message || 'Failed');
                  }
                }}
                className="ui-button ui-button--primary"
              >
                Add Project
              </button>
            </div>
          </div>
          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Projects & Assignments</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-slate-600 mb-2">Select a project to manage assignments:</p>
                <ul className="space-y-1 max-h-64 overflow-y-auto">
                  {projects.map((p) => (
                    <li key={p.id}>
                      <button
                        onClick={() => selectProject(p)}
                        className={`w-full flex items-center justify-between gap-2 text-left px-3 py-2 rounded-lg text-sm ${
                          selectedProject?.id === p.id ? 'bg-pale-sky/50 font-medium' : 'hover:bg-pale-sky/20'
                        }`}
                      >
                        <span className="truncate">{p.semester ? `${p.semester} — ` : ''}{p.name}</span>
                        <StatusBadge tone={p.discussable ? 'success' : 'neutral'}>
                          {p.discussable ? 'Discussable' : 'Under NDA'}
                        </StatusBadge>
                      </button>
                    </li>
                  ))}
                  {projects.length === 0 && <p className="text-slate-500 text-sm py-2">No projects yet.</p>}
                </ul>
              </div>
              <div>
                {selectedProject ? (
                  <>
                    <p className="text-sm font-medium text-slate-700 mb-2">
                      Assigned to {selectedProject.semester ? `${selectedProject.semester} — ` : ''}{selectedProject.name}:
                    </p>
                    <div className="mb-4 p-3 rounded-lg border border-pale-sky bg-pale-sky/10 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-slate-700">Citation tagging</span>
                        <StatusBadge tone={selectedProject.discussable ? 'success' : 'neutral'}>
                          {selectedProject.discussable ? 'Discussable' : 'Under NDA — not shown in suggestions'}
                        </StatusBadge>
                      </div>
                      <input
                        value={editProjectForm.client_name}
                        onChange={(e) => setEditProjectForm((p) => ({ ...p, client_name: e.target.value }))}
                        placeholder="Real client name (leave blank if under NDA)"
                        className="w-full px-3 py-2 rounded-lg border border-pale-sky text-sm"
                      />
                      <label className="flex items-center gap-2 text-sm text-slate-700">
                        <input
                          type="checkbox"
                          checked={editProjectForm.discussable}
                          onChange={(e) => setEditProjectForm((p) => ({ ...p, discussable: e.target.checked }))}
                        />
                        Can be named in outreach (not under NDA)
                      </label>
                      <button
                        onClick={handleSaveProjectTagging}
                        disabled={editProjectSaving}
                        className="ui-button ui-button--secondary ui-button--sm"
                      >
                        {editProjectSaving ? 'Saving…' : 'Save tagging'}
                      </button>
                    </div>
                    <ul className="space-y-2 mb-4">
                      {projectAssignments.map((a) => (
                        <li key={a.id} className="flex justify-between items-center py-2 border-b border-pale-sky/50 text-sm">
                          <span>{a.name || a.email}</span>
                          <span className="flex items-center gap-2">
                            <span className="text-slate-500">{a.role_in_project || '—'}</span>
                            <button
                              onClick={async () => {
                                try {
                                  await api.admin.projects.unassignUser(a.id, selectedProject.id);
                                  setProjectAssignments(await api.admin.projects.assignments(selectedProject.id));
                                } catch (e) {
                                  alert((e as Error)?.message);
                                }
                              }}
                              className="ui-button ui-button--danger ui-button--sm"
                            >
                              Remove
                            </button>
                          </span>
                        </li>
                      ))}
                      {projectAssignments.length === 0 && <p className="text-slate-500 text-sm">No one assigned.</p>}
                    </ul>
                    <button
                      onClick={() => setAssignUserModal({ projectId: selectedProject.id, projectName: selectedProject.semester ? `${selectedProject.semester} — ${selectedProject.name}` : selectedProject.name })}
                      className="ui-button ui-button--secondary ui-button--sm"
                    >
                      + Assign user
                    </button>
                  </>
                ) : (
                  <p className="text-slate-500 text-sm">Select a project.</p>
                )}
              </div>
            </div>
          </div>
          {assignUserModal && (
            <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setAssignUserModal(null)}>
              <div className="surface-card rounded-xl p-6 max-w-md w-full mx-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
                <h3 className="font-semibold text-deep-navy mb-4">Assign to {assignUserModal.projectName}</h3>
                <input
                  value={assignRole}
                  onChange={(e) => setAssignRole(e.target.value)}
                  placeholder="Role (e.g. Lead, Analyst)"
                  className="w-full px-3 py-2 rounded-lg border border-pale-sky mb-4"
                />
                <div className="max-h-48 overflow-y-auto space-y-1 mb-4">
                  {users.filter((u) => !projectAssignments.some((a) => a.id === u.id)).map((u) => (
                    <button
                      key={u.id}
                      onClick={async () => {
                        try {
                          await api.admin.projects.assignUser(u.id, { project_id: assignUserModal.projectId, role_in_project: assignRole.trim() || undefined });
                          setProjectAssignments(await api.admin.projects.assignments(assignUserModal.projectId));
                          setAssignUserModal(null);
                          setAssignRole('');
                        } catch (e) {
                          alert((e as Error)?.message);
                        }
                      }}
                      className="w-full text-left px-3 py-2 rounded hover:bg-pale-sky/20 text-sm"
                    >
                      {u.name || u.email}
                    </button>
                  ))}
                  {users.filter((u) => !projectAssignments.some((a) => a.id === u.id)).length === 0 && (
                    <p className="text-slate-500 text-sm">All users already assigned.</p>
                  )}
                </div>
                <button onClick={() => setAssignUserModal(null)} className="ui-button ui-button--ghost">Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'audit' && (
        <div className="surface-card rounded-xl p-6">
          <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
            <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)]">Audit Log</h2>
            <button
              onClick={() => api.admin.exportAuditLogExcel().then(() => toast.addToast('Audit log export downloaded.', 'success')).catch((e) => toast.addToast((e as Error).message, 'error'))}
              className="ui-button ui-button--primary"
            >
              Export to Excel
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto space-y-2">
            {auditLog.map((a) => (
              <div key={a.id} className="text-sm py-2 border-b border-pale-sky last:border-0">
                <span className="font-medium">{a.action}</span>
                {a.resource_type && <span className="text-slate-500"> • {a.resource_type}</span>}
                {a.details && <span className="text-slate-600"> — {a.details}</span>}
                <span className="text-xs text-slate-400 ml-2">
                  {a.user_email || 'System'} • {new Date(a.created_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'apikeys' && (
        <div className="space-y-6">
          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Create API Key</h2>
            <p className="text-sm text-slate-600 mb-4">For integrations (SharePoint, Zapier, etc.). Store securely.</p>
            <div className="flex gap-2 mb-4">
              <input
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                placeholder="Key name (e.g. SharePoint)"
                className="flex-1 px-3 py-2 rounded-lg border border-pale-sky"
              />
              <button onClick={handleCreateKey} className="ui-button ui-button--primary">
                Create
              </button>
            </div>
            {newKeyResult && (
              <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
                <p className="text-sm font-medium text-amber-800">Copy this key now. It won&apos;t be shown again:</p>
                <code className="block mt-2 p-2 bg-white rounded text-sm break-all">{newKeyResult}</code>
              </div>
            )}
          </div>
          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy mb-4">Your API Keys</h2>
            <ul className="space-y-2">
              {apiKeys.map((k) => (
                <li key={k.id} className="flex justify-between items-center py-2 border-b border-pale-sky last:border-0">
                  <span className="font-mono text-sm">{k.key_prefix}</span>
                  <span className="text-slate-500 text-sm">{k.name}</span>
                  <button
                    onClick={async () => {
                      if (confirm('Revoke this key?')) {
                        try {
                          await api.admin.apiKeys.revoke(k.id);
                          api.admin.apiKeys.list().then(setApiKeys).catch(() => {});
                        } catch (err) {
                          alert((err as Error)?.message);
                        }
                      }
                    }}
                    className="ui-button ui-button--danger ui-button--sm"
                  >
                    Revoke
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {activeTab === '2fa' && (
        <div className="surface-card rounded-xl p-6">
          <h2 className="font-semibold text-deep-navy mb-4">Two-Factor Authentication</h2>
          <p className="text-sm text-slate-600 mb-4">When enabled, Google sign-in asks for this code before a session cookie is issued.</p>
          {twoFactorStatus === 'enabled' && (
            <p className="text-sm text-green-600 font-medium mb-4">✓ 2FA is enabled for your account.</p>
          )}
          <div className="space-y-4">
            {(twoFactorStatus === 'not_setup' || twoFactorStatus === 'pending' || twoFactorStatus === null) && (
              <div className="flex gap-2 flex-wrap">
                <button onClick={handle2FASetup} className="ui-button ui-button--primary btn-press">
                  {twoFactorStatus === 'pending' && !qrDataUrl ? 'Regenerate QR Code' : twoFactorStatus === 'pending' ? 'Regenerate QR Code' : 'Setup 2FA'}
                </button>
              </div>
            )}
            {qrDataUrl && twoFactorSetup && (
              <div className="p-4 bg-slate-50 rounded-lg border border-pale-sky">
                <p className="text-sm font-medium text-slate-700 mb-3">Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.):</p>
                <img src={qrDataUrl} alt="2FA QR Code" className="w-64 h-64 mx-auto block rounded-lg border border-slate-200" />
                {qrExpiresAt != null && qrCountdown > 0 && (
                  <p className="text-xs text-amber-600 mt-2">
                    Expires in {qrCountdown}s. Regenerate above if it expires.
                  </p>
                )}
                <p className="text-xs text-slate-500 mt-3">Or enter this secret manually: <code className="bg-white px-1 rounded">{twoFactorSetup.secret}</code></p>
                <p className="text-sm text-slate-600 mt-2">Then enter the 6-digit code below to verify.</p>
              </div>
            )}
            <div className="flex gap-2 flex-wrap items-center">
              <input
                value={twoFactorCode}
                onChange={(e) => setTwoFactorCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="6-digit code"
                className="w-32 px-3 py-2 rounded-lg border border-pale-sky"
                maxLength={6}
              />
              {(twoFactorStatus === 'pending' || twoFactorStatus === null) && (
                <button onClick={handle2FAVerify} className="ui-button ui-button--primary">
                  Verify
                </button>
              )}
              {twoFactorStatus === 'enabled' && (
                <button onClick={handle2FADisable} className="ui-button ui-button--danger">
                  Disable 2FA
                </button>
              )}
            </div>
            <div className="pt-4 border-t border-pale-sky">
              <button onClick={handle2FAReset} className="ui-button ui-button--ghost">
                Reset 2FA (If Stuck, e.g. Lost Phone Before Verifying)
              </button>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'catalog' && (
        <div className="surface-card rounded-xl p-6 space-y-4">
          <h2 className="font-semibold text-deep-navy">Object catalog</h2>
          <p className="text-sm text-slate-600">
            Bucket: {catalog?.bucket || 'not set (local files only)'}
          </p>
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left bg-slate-50">
                <th className="px-3 py-2">Kind</th>
                <th className="px-3 py-2">Key</th>
                <th className="px-3 py-2">Size</th>
                <th className="px-3 py-2">Source</th>
              </tr>
            </thead>
            <tbody>
              {(catalog?.objects || []).map((o) => (
                <tr key={o.id} className="border-t border-pale-sky/60">
                  <td className="px-3 py-2">{o.kind}</td>
                  <td className="px-3 py-2 font-mono text-xs">{o.s3_key}</td>
                  <td className="px-3 py-2 tabular-nums">{o.byte_size ?? '—'}</td>
                  <td className="px-3 py-2">{o.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(catalog?.objects || []).length === 0 && (
            <p className="text-sm text-slate-500">No stored_objects rows yet.</p>
          )}
        </div>
      )}

      {activeTab === 'operations' && (
        <div className="space-y-8 max-w-6xl">
          <ContactMaintenance />
          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)] mb-4">Operations Intelligence</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
              Ask questions about club activity and the reference material below.
            </p>
            <div className="flex flex-wrap gap-4 items-center mb-6">
              <label className="flex items-center gap-2">
                <span className="text-sm text-slate-600">Days</span>
                <select value={opsDays} onChange={(e) => setOpsDays(Number(e.target.value))} className="px-2 py-1.5 rounded-lg border border-pale-sky bg-white dark:bg-slate-700 dark:border-slate-600">
                  {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2">
                <span className="text-sm text-slate-600">Heatmap by</span>
                <select value={opsGroupBy} onChange={(e) => setOpsGroupBy(e.target.value as 'hour' | 'day_of_week')} className="px-2 py-1.5 rounded-lg border border-pale-sky bg-white dark:bg-slate-700 dark:border-slate-600">
                  <option value="hour">Hour Of Day</option>
                  <option value="day_of_week">Day Of Week</option>
                </select>
              </label>
              <button
                onClick={() => api.admin.operations.exportInsightsExcel(opsDays).then(() => toast.addToast('Export downloaded.', 'success')).catch((e) => toast.addToast((e as Error).message, 'error'))}
                className="ui-button ui-button--primary"
              >
                Export to Excel
              </button>
              <button
                onClick={() => api.admin.operations.exportChartsZip(opsDays).then(() => toast.addToast('Charts ZIP downloaded.', 'success')).catch((e) => toast.addToast((e as Error).message, 'error'))}
                className="ui-button ui-button--primary"
              >
                Download charts (ZIP)
              </button>
              <button
                onClick={() => api.admin.operations.exportFullZip(opsDays).then(() => toast.addToast('Full export (ZIP with cache) downloaded.', 'success')).catch((e) => toast.addToast((e as Error).message, 'error'))}
                className="ui-button ui-button--primary"
              >
                Download full export (ZIP + cache)
              </button>
            </div>

            <h3 className="text-sm font-semibold text-deep-navy dark:text-[var(--text-primary)] mt-6 mb-2">Usage heatmap (what’s used and when) — horizontal: time × event type</h3>
            {opsMatrix2d && opsMatrix2d.row_labels.length > 0 && opsMatrix2d.col_labels.length > 0 && Array.isArray(opsMatrix2d.values) ? (
              <div className="overflow-x-auto -mx-2">
                <div
                  className="inline-grid gap-0.5 mb-2 min-w-full"
                  style={{
                    gridTemplateRows: `auto repeat(${opsMatrix2d.row_labels.length}, minmax(20px, 1fr))`,
                    gridTemplateColumns: `120px repeat(${opsMatrix2d.col_labels.length}, minmax(28px, 1fr))`,
                  }}
                >
                  <div className="row-start-1 col-start-1 rounded-tl bg-pale-sky/40 p-1.5 text-xs font-medium text-slate-600 dark:text-slate-400" />
                  {opsMatrix2d.col_labels.map((label, c) => (
                    <div key={c} className="row-start-1 p-1 text-[10px] font-medium text-slate-600 dark:text-slate-400 truncate text-center" style={{ gridColumn: c + 2 }} title={String(label)}>
                      {String(label).length > 8 ? String(label).slice(0, 7) + '…' : String(label)}
                    </div>
                  ))}
                  {opsMatrix2d.row_labels.map((rowLabel, r) => {
                    const rowValues = opsMatrix2d.values[r];
                    if (!Array.isArray(rowValues)) return null;
                    const max = Math.max(1, ...(opsMatrix2d.values.flat() ?? [0]));
                    return (
                      <Fragment key={r}>
                        <div className="py-1 pr-2 text-xs text-slate-600 dark:text-slate-400 text-right" style={{ gridRow: r + 2, gridColumn: 1 }}>
                          {opsGroupBy === 'hour' ? `${rowLabel}:00` : ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][parseInt(String(rowLabel), 10)] ?? rowLabel}
                        </div>
                        {rowValues.map((val, c) => {
                          const intensity = val ? val / max : 0;
                          const colLabel = opsMatrix2d.col_labels[c] ?? '';
                          return (
                            <div
                              key={`${r}-${c}`}
                              className="min-h-[20px] rounded border border-pale-sky/30"
                              style={{ gridRow: r + 2, gridColumn: c + 2, backgroundColor: `rgba(26, 47, 90, ${0.12 + intensity * 0.88})` }}
                              title={`${colLabel}: ${val}`}
                            />
                          );
                        })}
                      </Fragment>
                    );
                  })}
                </div>
              </div>
            ) : opsHeatmap && Object.keys(opsHeatmap.grid || {}).length > 0 ? (
              <div className="overflow-x-auto">
                <div className="inline-grid gap-1 mb-4" style={{ gridTemplateColumns: `repeat(${Math.min(24, Object.keys(opsHeatmap.grid).length + 1)}, minmax(0, 1fr))` }}>
                  {opsGroupBy === 'hour' && Array.from({ length: 24 }, (_, i) => i).map((h) => {
                    const cell = opsHeatmap.grid[h] || {};
                    const total = Object.values(cell).reduce((a, b) => a + b, 0);
                    const max = Math.max(1, ...Object.values(opsHeatmap.grid).flatMap((c) => Object.values(c)));
                    const intensity = total ? (total / max) : 0;
                    return (
                      <div key={h} title={`${h}:00 - ${total} events`} className="flex flex-col items-center">
                        <div className="w-full rounded min-h-[24px] border border-pale-sky/50" style={{ backgroundColor: `rgba(26, 47, 90, ${0.15 + intensity * 0.85})` }} />
                        <span className="text-[10px] text-slate-500 mt-0.5">{h}</span>
                      </div>
                    );
                  })}
                  {opsGroupBy === 'day_of_week' && ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d, i) => {
                    const cell = opsHeatmap.grid[i] || {};
                    const total = Object.values(cell).reduce((a, b) => a + b, 0);
                    const max = Math.max(1, ...Object.values(opsHeatmap.grid).flatMap((c) => Object.values(c)));
                    const intensity = total ? (total / max) : 0;
                    return (
                      <div key={d} title={`${d} - ${total} events`} className="flex flex-col items-center">
                        <div className="w-full rounded min-h-[32px] border border-pale-sky/50" style={{ backgroundColor: `rgba(26, 47, 90, ${0.15 + intensity * 0.85})` }} />
                        <span className="text-xs text-slate-500 mt-1">{d}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500">No usage data yet. Use the site (create campaigns, generate emails, scrape) to build the heatmap.</p>
            )}


            <h3 className="text-sm font-semibold text-deep-navy dark:text-[var(--text-primary)] mt-6 mb-2">Event counts by type (animated)</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-4">
              <AnimatedEventTypeChart aggregates={opsAggregates} />
              <AnimatedResourceChart aggregates={opsAggregates} />
            </div>
            {opsAggregates && (opsAggregates.by_event_type?.length > 0 || opsAggregates.by_resource_type?.length > 0) ? (
              <div className="flex flex-wrap gap-6 text-sm">
                <div>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">By event type (list)</p>
                  <ul className="space-y-1">
                    {(opsAggregates.by_event_type || []).slice(0, 12).map((r) => (
                      <li key={r.event_type} className="flex justify-between gap-4"><span>{r.event_type}</span><span className="font-medium">{r.count}</span></li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">By resource (list)</p>
                  <ul className="space-y-1">
                    {(opsAggregates.by_resource_type || []).filter((r) => r.resource_type).slice(0, 12).map((r) => (
                      <li key={r.resource_type} className="flex justify-between gap-4"><span>{r.resource_type}</span><span className="font-medium">{r.count}</span></li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500 dark:text-slate-400">No aggregates yet.</p>
            )}

            <h3 className="text-sm font-semibold text-deep-navy dark:text-[var(--text-primary)] mt-6 mb-2">Recent events (raw data)</h3>
            <div className="max-h-48 overflow-y-auto border border-pale-sky rounded-lg">
              <table className="w-full text-sm">
                <thead className="bg-pale-sky/30 sticky top-0">
                  <tr><th className="text-left p-2">Time</th><th className="text-left p-2">User</th><th className="text-left p-2">Event</th><th className="text-left p-2">Resource</th></tr>
                </thead>
                <tbody>
                  {opsEvents.slice(0, 100).map((e) => (
                    <tr key={e.id} className="border-t border-pale-sky/50">
                      <td className="p-2 text-slate-600">{e.created_at ? new Date(e.created_at).toLocaleString() : ''}</td>
                      <td className="p-2">{e.user_id ?? '—'}</td>
                      <td className="p-2 font-medium">{e.event_type}</td>
                      <td className="p-2 text-slate-600">{e.resource_type || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {opsEvents.length === 0 && <p className="text-sm text-slate-500 mt-2">No events in this period.</p>}
          </div>

          <div className="surface-card rounded-xl p-6">
            <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)] mb-2">Assistant knowledge</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">The shared Assistant uses the same private document register as Projects. Access is checked for every retrieval, and Amazon Bedrock receives only the short source excerpts needed for an answer.</p>
          </div>

          <div className="surface-card rounded-xl p-6">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <h2 className="font-semibold text-deep-navy dark:text-[var(--text-primary)]">Discovery search (TinyFish / Firecrawl)</h2>
              <button
                type="button"
                className="ui-button ui-button--secondary ui-button--sm"
                disabled={firecrawlTesting}
                onClick={async () => {
                  setFirecrawlTesting(true);
                  setFirecrawlError('');
                  try {
                    setFirecrawlStatus(await api.admin.operations.firecrawlStatus());
                  } catch (e) {
                    setFirecrawlError(e instanceof Error ? e.message : 'Web search self-test failed');
                  } finally {
                    setFirecrawlTesting(false);
                  }
                }}
              >
                {firecrawlTesting ? 'Testing…' : 'Test web search now'}
              </button>
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
              Runs the exact fetch and search calls the discovery pipeline makes, live, against
              TinyFish and the self-hosted Firecrawl instance independently, plus what the
              pipeline actually gets right now (TinyFish first, then Firecrawl). Use this instead
              of guessing why a company search found nothing.
            </p>
            {firecrawlError && <p className="text-sm text-red-700 mb-3">{firecrawlError}</p>}
            {firecrawlStatus && (
              <div className="space-y-4 text-sm">
                {(['tinyfish', 'firecrawl'] as const).map((backend) => {
                  const status = firecrawlStatus[backend];
                  const label = backend === 'tinyfish' ? 'TinyFish' : 'Firecrawl';
                  return (
                    <div key={backend}>
                      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">{label}</h3>
                      {!status.configured ? (
                        <p className="text-xs text-slate-500 rounded-lg border border-pale-sky/80 px-4 py-3">
                          Not configured{backend === 'tinyfish' ? ' (MONID_API_KEY is not set)' : ' (FIRECRAWL_URL is not set)'}.
                        </p>
                      ) : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          {(['fetch', 'search'] as const).map((probe) => {
                            const result = status[probe];
                            if (!result) return null;
                            return (
                              <div key={probe} className={`rounded-lg border px-4 py-3 ${result.ok ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
                                <div className="font-medium text-deep-navy mb-1 capitalize">{probe}: {result.ok ? 'Working' : 'Failing'}</div>
                                <p className="text-xs text-slate-600">
                                  {result.duration_s != null && `${result.duration_s}s`}
                                  {result.content_chars != null && ` · ${result.content_chars} chars fetched`}
                                  {result.result_count != null && ` · ${result.result_count} results for a universal test query`}
                                </p>
                                {(result.error || result.note) && (
                                  <p className="text-xs text-red-700 mt-1">{result.error || result.note}</p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">What the pipeline gets right now</h3>
                  <p className="text-xs text-slate-500 mb-2">{firecrawlStatus.effective.note}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className={`rounded-lg border px-4 py-3 ${firecrawlStatus.effective.fetch.ok ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
                      <div className="font-medium text-deep-navy">Fetch: {firecrawlStatus.effective.fetch.ok ? 'Working' : 'Failing'}</div>
                    </div>
                    <div className={`rounded-lg border px-4 py-3 ${firecrawlStatus.effective.search.ok ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
                      <div className="font-medium text-deep-navy">Search: {firecrawlStatus.effective.search.ok ? 'Working' : 'Failing'}</div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
