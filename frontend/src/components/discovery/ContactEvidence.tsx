import type { ContactEvidence, EvidenceSource } from '../../api';
import { evidenceLabel, evidenceTime, publicSourceUrl } from '../../lib/contactEvidence';

export function EvidenceSources({ sources }: { sources: EvidenceSource[] }) {
  if (!sources.length) return <p>No dated public sources saved. Do not treat an inferred address as identity evidence.</p>;
  return <ul className="research-sources">{sources.map((source, index) => {
    const url = publicSourceUrl(source.url);
    return <li key={source.id ?? index}>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">Source {index + 1}: {new URL(url).hostname}</a> : <span>Source {index + 1}: link unavailable</span>}
      <p>Observed {evidenceTime(source.observed_at)}</p>
      {source.excerpt ? <blockquote>{source.excerpt}</blockquote> : <p>No supporting excerpt recorded.</p>}
    </li>;
  })}</ul>;
}

export default function EvidenceDetails({ evidence }: { evidence: ContactEvidence }) {
  return <div className="research-evidence">
    <dl className="research-dimensions">
      {([['Person identity', evidence.identity], ['Employment', evidence.employment], ['Address origin', evidence.address_origin], ['Mailbox assessment', evidence.mailbox], ['Project fit', evidence.project_fit]] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{evidenceLabel(value)}</dd></div>)}
    </dl>
    <p>A mail-domain route does not confirm this individual mailbox. An inferred address does not prove identity. Provider confidence is an assessment at one point in time, not a delivery guarantee.</p>
    <p>Checked {evidenceTime(evidence.checked_at)} · Expires {evidenceTime(evidence.expires_at)}</p>
    <p><strong>Assessment method:</strong> {evidence.method ? evidence.method.replaceAll('_', ' ') : 'Not recorded'}</p>
    <p><strong>Assessment reason:</strong> {evidence.reason || 'No reason recorded.'}</p>
    <h3>Conflicts and uncertainty</h3>
    {evidence.conflicts.length ? <ul>{evidence.conflicts.map((conflict, index) => <li key={index}>{conflict.replaceAll('_', ' ')}</li>)}</ul> : <p>No conflicts recorded. This does not establish that all facts are current.</p>}
    <h3>Saved sources</h3>
    <EvidenceSources sources={evidence.sources} />
  </div>;
}
