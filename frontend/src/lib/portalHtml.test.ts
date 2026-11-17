import { describe, expect, it } from 'vitest';
import { portalExcerpt, sanitizePortalHtml } from './portalHtml';

/** Parse output the way the page will, so serialisation details cannot hide markup. */
function reparse(html: string) {
  const template = document.createElement('template');
  template.innerHTML = html;
  const elements = [...template.content.querySelectorAll('*')];
  return {
    tags: elements.map((element) => element.tagName.toLowerCase()),
    attrs: elements.flatMap((element) => element.getAttributeNames()),
    links: [...template.content.querySelectorAll('a')],
  };
}

describe('sanitizePortalHtml', () => {
  it('keeps the formatting the editor and server produce', () => {
    const html = '<h2>Title</h2><h3>Sub</h3><p><strong>bold</strong> <b>b</b> <em>em</em> <i>i</i> <u>u</u> <s>s</s><br>line</p>'
      + '<ul><li>one</li></ul><ol><li>two</li></ol><blockquote>quote</blockquote><pre><code>x</code></pre>';
    const { tags } = reparse(sanitizePortalHtml(html));
    for (const tag of ['h2', 'h3', 'p', 'strong', 'b', 'em', 'i', 'u', 's', 'br', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code']) {
      expect(tags).toContain(tag);
    }
    expect(sanitizePortalHtml('<p>Hello</p>')).toBe('<p>Hello</p>');
  });

  it('removes script tags with their content', () => {
    const out = sanitizePortalHtml('<p>Hi</p><script>alert(1)</script>');
    expect(out).not.toContain('script');
    expect(out).not.toContain('alert');
  });

  it('removes event-handler attributes and everything outside the allowed tags and attributes', () => {
    const out = sanitizePortalHtml(
      '<p onclick="x()" style="color:red" class="c" id="i">a</p><img src=x onerror="alert(1)"><svg onload="x()"></svg>'
      + '<iframe src="https://example.com"></iframe><div>kept text</div><a href="https://example.com" onclick="x()" title="t" data-x="1">l</a>',
    );
    const { tags, attrs } = reparse(out);
    expect(tags).not.toContain('img');
    expect(tags).not.toContain('svg');
    expect(tags).not.toContain('iframe');
    expect(tags).not.toContain('div');
    expect(attrs.filter((name) => name.startsWith('on'))).toEqual([]);
    expect(attrs.every((name) => ['href', 'title', 'rel', 'target'].includes(name))).toBe(true);
    expect(out).toContain('kept text');
  });

  it.each([
    ['plain', 'javascript:alert(1)'],
    ['mixed case', 'JaVaScRiPt:alert(1)'],
    ['entity-obfuscated', '&#106;avascript:alert(1)'],
    ['hex entity', '&#x6A;avascript:alert(1)'],
    ['whitespace-obfuscated', 'java\tscript:alert(1)'],
    ['newline-obfuscated', 'java&#10;script:alert(1)'],
    ['leading space', ' javascript:alert(1)'],
    ['data url', 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='],
    ['relative', '/admin'],
    ['protocol-relative', '//evil.example/x'],
  ])('strips a %s link', (_name, href) => {
    const { links } = reparse(sanitizePortalHtml(`<a href="${href}">click</a>`));
    expect(links).toHaveLength(1);
    expect(links[0].hasAttribute('href')).toBe(false);
    expect(links[0].textContent).toBe('click');
  });

  it('keeps http(s) and mailto links and makes them open safely', () => {
    const { links } = reparse(sanitizePortalHtml(
      '<a href="https://yucg.example/a">a</a><a href="HTTP://yucg.example/b">b</a><a href="mailto:board@yucg.example">c</a>',
    ));
    expect(links).toHaveLength(3);
    for (const link of links) {
      expect(link.getAttribute('href')).toBeTruthy();
      expect(link.getAttribute('rel')).toBe('noopener noreferrer');
      expect(link.getAttribute('target')).toBe('_blank');
    }
  });

  it('does not let a link carry its own target or rel', () => {
    const { links } = reparse(sanitizePortalHtml('<a href="https://x.example" target="_self" rel="opener">x</a>'));
    expect(links[0].getAttribute('target')).toBe('_blank');
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer');
  });
});

describe('portalExcerpt', () => {
  it('is plain text, shortened', () => {
    expect(portalExcerpt('<p>One <strong>two</strong></p><script>x()</script>')).toBe('One two');
    expect(portalExcerpt(`<p>${'word '.repeat(100)}</p>`, 20).endsWith('…')).toBe(true);
  });
});
