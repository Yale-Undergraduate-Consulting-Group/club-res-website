import DOMPurify from 'dompurify';

/**
 * The alumni portal's posts and event descriptions: exactly the tags and
 * attributes the server keeps (backend sanitize_rich_text). Everything the
 * server sends, and everything the editor produces, passes through here.
 *
 * A separate DOMPurify instance, so richText.ts's global hooks (which allow
 * images and inline styles for email) never apply to portal content.
 */
const purifier = DOMPurify(window);

const TAGS = ['p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'blockquote', 'pre', 'code', 'h2', 'h3', 'ul', 'ol', 'li', 'a'];
const ATTRS = ['href', 'title'];
const LINK = /^(?:https?:\/\/|mailto:)/i;

function safeHref(value: string): boolean {
  if (value !== value.trim() || /\s/.test(value)) return false;
  if (/^mailto:/i.test(value)) return value.slice(value.indexOf(':') + 1).length > 0;
  try {
    const url = new URL(value);
    return (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.hostname);
  } catch {
    return false;
  }
}

purifier.addHook('uponSanitizeAttribute', (node, data) => {
  if (data.attrName === 'href') data.keepAttr = node.nodeName === 'A' && safeHref(data.attrValue);
});

purifier.addHook('afterSanitizeAttributes', (node) => {
  if (node.nodeName !== 'A') return;
  if (node.hasAttribute('href')) {
    node.setAttribute('rel', 'noopener noreferrer');
    node.setAttribute('target', '_blank');
  } else {
    node.removeAttribute('rel');
    node.removeAttribute('target');
  }
});

export function sanitizePortalHtml(value: string): string {
  return purifier.sanitize(value, {
    ALLOWED_TAGS: TAGS,
    ALLOWED_ATTR: ATTRS,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    SANITIZE_NAMED_PROPS: true,
    ALLOWED_URI_REGEXP: LINK,
  });
}

/** Plain text of sanitised HTML, for short excerpts. */
export function portalExcerpt(value: string, max = 220): string {
  const template = document.createElement('template');
  template.innerHTML = sanitizePortalHtml(value);
  const text = (template.content.textContent || '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}
