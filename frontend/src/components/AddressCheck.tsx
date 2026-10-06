import { useEffect, useState } from 'react';
import { fetchApi } from '../api';

type Evidence = { valid: boolean; status?: string; mx_valid?: boolean | null; mailbox_exists?: boolean | null };

export default function AddressCheck({ email }: { email: string }) {
  const [result, setResult] = useState<{ email: string; evidence?: Evidence; failed?: boolean } | null>(null);
  useEffect(() => {
    if (!email.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchApi<Evidence>(`/api/outreach/verify-email?email=${encodeURIComponent(email.trim())}`, { signal: controller.signal })
        .then(evidence => setResult({ email, evidence }))
        .catch(error => {
          if (!(error instanceof DOMException && error.name === 'AbortError')) setResult({ email, failed: true });
        });
    }, 600);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [email]);
  if (!email.trim()) return null;
  const current = result?.email === email ? result : null;
  let label = 'Checking address…';
  if (current?.failed) label = 'Address check unavailable. Try again later.';
  else if (current?.evidence) {
    const evidence = current.evidence;
    if (!evidence.valid || evidence.status === 'invalid') label = 'Address needs review: invalid format or domain accepts no mail.';
    else if (evidence.mx_valid) label = 'Domain accepts mail · individual mailbox unconfirmed';
    else label = 'Address check inconclusive · individual mailbox unconfirmed';
  }
  return <p className="studio-address-check" role="status">{label}<span>Checked without sending an email.</span></p>;
}
