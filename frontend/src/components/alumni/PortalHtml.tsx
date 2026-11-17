import { useMemo } from 'react';
import { sanitizePortalHtml } from '../../lib/portalHtml';

/** Post and event text. Sanitised here, whatever the server sent. */
export default function PortalHtml({ html, className = '' }: { html: string; className?: string }) {
  const safe = useMemo(() => sanitizePortalHtml(html), [html]);
  if (!safe) return null;
  return <div className={`portal-html ${className}`.trim()} dangerouslySetInnerHTML={{ __html: safe }} />;
}
