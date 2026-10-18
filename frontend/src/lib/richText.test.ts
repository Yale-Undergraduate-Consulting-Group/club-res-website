import { beforeEach, describe, expect, it, vi } from 'vitest';
import { insertSafeTransfer, safeImageUrl, safeStyle, sanitizeRichText } from './richText';

const PIXEL = 'data:image/png;base64,iVBORw0KGgo=';

/** Parse output the way the page will, so serialisation details cannot hide markup. */
function reparse(html: string): { tags: string[]; handlers: string[] } {
  const template = document.createElement('template');
  template.innerHTML = html;
  const elements = [...template.content.querySelectorAll('*')];
  return {
    tags: elements.map((element) => element.tagName.toLowerCase()),
    handlers: elements.flatMap((element) => element.getAttributeNames().filter((name) => name.startsWith('on'))),
  };
}

describe('sanitizeRichText', () => {
  it('keeps the formatting the composer and mail clients produce', () => {
    const html = '<h2>Title</h2><p><b>bold</b> <strong>strong</strong> <i>i</i> <em>em</em> <u>u</u> <s>s</s></p>'
      + '<ul><li>one</li></ul><ol><li>two</li></ol><blockquote>quote</blockquote><pre><code>x</code></pre>'
      + '<table><tbody><tr><td colspan="2" align="center">cell</td></tr></tbody></table>'
      + '<p><font size="3" face="Arial" color="#1a2f5a">font</font><br></p>';
    expect(sanitizeRichText(html)).toBe(html);
  });

  it('removes scripts and executable or embedding elements', () => {
    const output = sanitizeRichText(
      '<p>Hi</p><script>alert(1)</script><iframe src="https://evil.example"></iframe>'
      + '<object data="https://evil.example/x.swf"></object><embed src="https://evil.example/e">'
      + '<form action="https://evil.example"><input name="q"></form><style>p{color:red}</style>'
      + '<svg><script>alert(2)</script></svg><math><mi>x</mi></math>',
    );
    expect(output).not.toMatch(/<(?:script|iframe|object|embed|form|input|style|svg|math)\b/i);
    expect(output).not.toContain('alert');
    expect(output.startsWith('<p>Hi</p>')).toBe(true);
  });

  it('strips every event-handler attribute', () => {
    const output = sanitizeRichText(
      `<p onclick="alert(1)">a</p><img src="https://example.com/a.png" onerror="alert(2)">`
      + '<a href="https://example.com" onmouseover="alert(3)">b</a>'
      + '<table><tbody><tr><td onfocus="alert(4)">c</td></tr></tbody></table>',
    );
    expect(reparse(output).handlers).toEqual([]);
    expect(output).toContain('<img src="https://example.com/a.png">');
    expect(output).toContain('<a href="https://example.com">b</a>');
    expect(output).toContain('<td>c</td>');
  });

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' javascript:alert(1)',
    '&#106;avascript:alert(1)',
    'java\tscript:alert(1)',
    'vbscript:msgbox(1)',
    'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
    '/relative/path',
    '//evil.example/path',
    'ftp://example.com/file',
  ])('drops unsafe or non-absolute link %s', (href) => {
    expect(sanitizeRichText(`<a href="${href}">x</a>`)).toBe('<a>x</a>');
  });

  it.each(['https://example.com/a?b=c', 'http://example.com', 'mailto:member@example.com'])('keeps link %s', (href) => {
    expect(sanitizeRichText(`<a href="${href}">x</a>`)).toBe(`<a href="${href.replace('&', '&amp;')}">x</a>`);
  });

  it('allows URLs only on the element that can use them', () => {
    expect(sanitizeRichText('<span href="https://example.com">x</span>')).toBe('<span>x</span>');
    expect(sanitizeRichText('<p src="https://example.com/a.png">x</p>')).toBe('<p>x</p>');
    expect(sanitizeRichText('<a src="https://example.com/a.png">x</a>')).toBe('<a>x</a>');
  });

  it.each([
    ['http://example.com/a.png', false],
    ['https://example.com/a.png', true],
    [PIXEL, true],
    ['data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=', false],
    ['data:text/html;base64,PHA+PC9wPg==', false],
    ['javascript:alert(1)', false],
    ['/tracker.png', false],
  ])('image source %s kept: %s', (src, kept) => {
    expect(sanitizeRichText(`<img src="${src}">`)).toBe(kept ? `<img src="${src}">` : '<img>');
  });

  it('filters inline style one declaration at a time', () => {
    expect(sanitizeRichText(
      '<p style="color: red; position: fixed; background-image: url(https://evil.example/t.png); font-weight: bold">x</p>',
    )).toBe('<p style="color: red; font-weight: bold">x</p>');
    expect(sanitizeRichText('<p style="position: absolute; top: 0">x</p>')).toBe('<p>x</p>');
    expect(sanitizeRichText('<p style="color: expression(alert(1))">x</p>')).toBe('<p>x</p>');
  });

  it('validates presentational attribute values', () => {
    expect(sanitizeRichText('<table><tbody><tr><td width="120" height="abc" rowspan="2;x">c</td></tr></tbody></table>'))
      .toBe('<table><tbody><tr><td width="120">c</td></tr></tbody></table>');
    expect(sanitizeRichText('<img src="https://example.com/a.png" width="100%" height="40">')).toBe('<img src="https://example.com/a.png" height="40">');
    expect(sanitizeRichText('<font size="9" color="red;background:url(x)" face="<x>">f</font>')).toBe('<font>f</font>');
    expect(sanitizeRichText('<span size="3" face="Arial" color="red">x</span>')).toBe('<span color="red">x</span>');
    expect(sanitizeRichText('<p align="center">a</p><p align="expression">b</p>')).toBe('<p align="center">a</p><p>b</p>');
  });

  it('drops identifiers, data, and ARIA attributes that can clobber or mislead', () => {
    expect(sanitizeRichText('<p id="x" class="y" name="z" data-x="1" aria-label="2" title="t">a</p>')).toBe('<p title="t">a</p>');
  });

  it('neutralises markup smuggled inside attributes', () => {
    const output = sanitizeRichText('<p title="</p><img src=x onerror=alert(1)>">x</p><noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>');
    expect(reparse(output)).toEqual({ tags: ['p'], handlers: [] });
  });
});

describe('safeStyle', () => {
  it('keeps only listed properties with harmless values', () => {
    expect(safeStyle('COLOR: #fff;; text-align: center; behavior: url(x.htc); color:; margin-left: 2em')).toBe('color: #fff; text-align: center; margin-left: 2em');
  });

  it.each(['url(x)', 'URL ( x )', 'expression(alert(1))', 'javascript:alert(1)', '@import x', 'red<script>', 'red}', 'red\\0'])(
    'rejects value %s', (value) => expect(safeStyle(`color: ${value}`)).toBe(''),
  );
});

describe('safeImageUrl', () => {
  it('accepts https and inline raster images only', () => {
    expect(safeImageUrl('HTTPS://example.com/a.png')).toBe('HTTPS://example.com/a.png');
    expect(safeImageUrl(PIXEL)).toBe(PIXEL);
    expect(safeImageUrl('data:image/png;base64,abc"onerror="x')).toBe('');
    expect(safeImageUrl('http://example.com/a.png')).toBe('');
  });
});

describe('insertSafeTransfer', () => {
  // jsdom has no execCommand; the browser's is the sink this function guards.
  const execCommand = vi.fn(() => true);
  beforeEach(() => {
    execCommand.mockClear();
    document.execCommand = execCommand;
  });

  it('sanitizes pasted HTML before it reaches the editable node', () => {
    const html = '<p onclick="alert(1)">hi</p><script>alert(2)</script>';
    insertSafeTransfer({ getData: (type: string) => (type === 'text/html' ? html : 'hi') } as DataTransfer);
    expect(execCommand).toHaveBeenCalledExactlyOnceWith('insertHTML', false, '<p>hi</p>');
  });

  it('inserts plain text as text, never as HTML', () => {
    const text = '<img src=x onerror=alert(1)>';
    insertSafeTransfer({ getData: (type: string) => (type === 'text/plain' ? text : '') } as DataTransfer);
    expect(execCommand).toHaveBeenCalledExactlyOnceWith('insertText', false, text);
  });
});
