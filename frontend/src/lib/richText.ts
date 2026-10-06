import DOMPurify from 'dompurify';

/** Explicit display contract for member/imported/generated email HTML. No SVG,
 * forms, embedded documents, event handlers, or relative/protocol-relative URLs.
 *
 * Inline style is allowed but not trusted: the composer writes colour, font,
 * size and alignment as CSS (that is what every mail client understands and
 * what Gmail itself pastes), so the declarations are filtered one by one
 * rather than the whole attribute being kept or dropped. */
const tags = ['p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 's', 'strike', 'blockquote', 'pre', 'code', 'h2', 'h3', 'ul', 'ol', 'li', 'a', 'img', 'font', 'table', 'tbody', 'thead', 'tr', 'td', 'th'];
const attrs = ['href', 'src', 'alt', 'title', 'width', 'height', 'color', 'face', 'size', 'colspan', 'rowspan', 'style', 'align'];

/** Declarations a mail client renders and an attacker cannot ride. Anything
 * that can fetch (url()), position, or execute is absent by construction. */
const STYLE_PROPERTIES = new Set([
  'color', 'background-color', 'font-family', 'font-size', 'font-weight',
  'font-style', 'text-decoration', 'text-decoration-line', 'text-align',
  'line-height', 'margin-left', 'padding-left', 'list-style-type',
]);
const UNSAFE_STYLE_VALUE = /url\s*\(|expression|javascript:|@import|[<>{}\\]/i;

export function safeStyle(value: string): string {
  return value
    .split(';')
    .map((declaration) => declaration.trim())
    .filter(Boolean)
    .map((declaration) => {
      const separator = declaration.indexOf(':');
      if (separator < 0) return '';
      const property = declaration.slice(0, separator).trim().toLowerCase();
      const propertyValue = declaration.slice(separator + 1).trim();
      if (!STYLE_PROPERTIES.has(property) || !propertyValue || UNSAFE_STYLE_VALUE.test(propertyValue)) return '';
      return `${property}: ${propertyValue}`;
    })
    .filter(Boolean)
    .join('; ');
}

export function safeImageUrl(value: string): string {
  if (/^https:\/\//i.test(value) || /^data:image\/(?:png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(value)) return value;
  return '';
}

DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
  if (data.attrName === 'href') data.keepAttr = node.nodeName === 'A' && /^(?:https?:\/\/|mailto:)/i.test(data.attrValue);
  if (data.attrName === 'src') data.keepAttr = node.nodeName === 'IMG' && Boolean(safeImageUrl(data.attrValue));
  if (['width', 'height', 'colspan', 'rowspan'].includes(data.attrName)) data.keepAttr = /^\d{1,4}$/.test(data.attrValue);
  if (data.attrName === 'size') data.keepAttr = node.nodeName === 'FONT' && /^[1-7]$/.test(data.attrValue);
  if (data.attrName === 'face') data.keepAttr = node.nodeName === 'FONT' && /^[\w\s,'"-]{1,80}$/.test(data.attrValue);
  if (data.attrName === 'align') data.keepAttr = /^(?:left|center|right|justify)$/i.test(data.attrValue);
  if (data.attrName === 'color') data.keepAttr = /^(?:#[0-9a-f]{3,8}|[a-z]{1,20})$/i.test(data.attrValue);
  if (data.attrName === 'style') {
    const filtered = safeStyle(data.attrValue);
    data.attrValue = filtered;
    data.keepAttr = Boolean(filtered);
  }
});

export function sanitizeRichText(value: string): string {
  return DOMPurify.sanitize(value, {
    ALLOWED_TAGS: tags,
    ALLOWED_ATTR: attrs,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    SANITIZE_NAMED_PROPS: true,
    // URI hooks above further restrict each element and attribute.
    ALLOWED_URI_REGEXP: /^(?:https?:\/\/|mailto:|data:image\/(?:png|jpeg|gif|webp);base64,)/i,
    // DOMPurify applies the URI pattern to every attribute outside its URI-safe
    // list, which silently dropped these. The hook above validates their values.
    ADD_URI_SAFE_ATTR: ['width', 'height', 'colspan', 'rowspan', 'size', 'face', 'align', 'color'],
  });
}

/** Browser paste/drop must be sanitized before it enters a live editable node. */
export function insertSafeTransfer(transfer: DataTransfer): void {
  const html = transfer.getData('text/html');
  if (html) document.execCommand('insertHTML', false, sanitizeRichText(html));
  else document.execCommand('insertText', false, transfer.getData('text/plain'));
}
