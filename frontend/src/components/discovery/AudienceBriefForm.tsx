import { useState } from 'react';
import type { AudienceSpec, Project } from '../../api';
import { Button } from '../ui/Primitives';

const specFields: Array<{ key: keyof Pick<AudienceSpec, 'industries' | 'companies' | 'geography' | 'size' | 'roles' | 'seniority' | 'exclusions'>; label: string; hint: string }> = [
  { key: 'industries', label: 'Industries', hint: 'For example: logistics, regional banking' },
  { key: 'companies', label: 'Named companies', hint: 'Specific companies already in scope' },
  { key: 'geography', label: 'Geography', hint: 'For example: Connecticut, New England' },
  { key: 'size', label: 'Organization size', hint: 'For example: 50–500 employees, Series A' },
  { key: 'roles', label: 'Functions and role families', hint: 'For example: operations, supply chain lead' },
  { key: 'seniority', label: 'Acceptable seniority', hint: 'For example: director, VP' },
  { key: 'exclusions', label: 'Exclusions', hint: 'For example: support desks, recruiters, agencies' },
];

const emptySpec: AudienceSpec = { industries: [], companies: [], geography: [], size: [], roles: [], seniority: [], people_per_company: 25, exclusions: [], reason: '' };

export default function AudienceBriefForm({
  projects, brief, busy, onSave,
}: {
  projects: Project[];
  brief: { id?: number; project_id?: number | null; name: string; spec: AudienceSpec } | null;
  busy: boolean;
  onSave: (brief: { id?: number; project_id: number | null; name: string; spec: AudienceSpec }) => void;
}) {
  const [name, setName] = useState(brief?.name || '');
  const [projectId, setProjectId] = useState<number | null>(brief?.project_id ?? null);
  const [spec, setSpec] = useState<AudienceSpec>(brief?.spec || emptySpec);

  const setList = (key: keyof Pick<AudienceSpec, 'industries' | 'companies' | 'geography' | 'size' | 'roles' | 'seniority' | 'exclusions'>, value: string) => {
    setSpec((current) => ({ ...current, [key]: value.split(',').map((entry) => entry.trim()).filter(Boolean) }));
  };

  return <form
    className="research-brief-form"
    onSubmit={(event) => { event.preventDefault(); onSave({ id: brief?.id, project_id: projectId, name: name.trim() || 'Audience brief', spec }); }}
  >
    <label htmlFor="research-brief-name">Brief name</label>
    <input id="research-brief-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="For example: Spring consulting audience" required />
    <label htmlFor="research-brief-project">Project scope</label>
    <select id="research-brief-project" value={projectId ?? ''} onChange={(event) => setProjectId(event.target.value ? Number(event.target.value) : null)}>
      <option value="">No project (club-wide)</option>
      {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
    </select>
    {specFields.map(({ key, label, hint }) => <div key={key} className="research-brief-field">
      <label htmlFor={`spec-${key}`}>{label}</label>
      <input id={`spec-${key}`} defaultValue={spec[key].join(', ')} onBlur={(event) => setList(key, event.target.value)} placeholder={hint} />
      <p>Comma-separated. Leave blank to skip.</p>
    </div>)}
    <div className="research-brief-field">
      <label htmlFor="spec-people">People per company</label>
      <input id="spec-people" type="number" min={1} max={100} value={spec.people_per_company} onChange={(event) => setSpec((current) => ({ ...current, people_per_company: Math.min(100, Math.max(1, Number(event.target.value) || 25)) }))} />
      <p>How many people to collect per company. Use 25–50 for a working list, up to 100 when you need volume.</p>
    </div>
    <div className="research-brief-field">
      <label htmlFor="spec-reason">Reason the club can contact this audience</label>
      <textarea id="spec-reason" rows={3} defaultValue={spec.reason} onBlur={(event) => setSpec((current) => ({ ...current, reason: event.target.value }))} placeholder="For example: alumni-led program supporting Connecticut manufacturers" />
      <p>The system records this reason; it does not infer hidden filters.</p>
    </div>
    <Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save brief'}</Button>
  </form>;
}
