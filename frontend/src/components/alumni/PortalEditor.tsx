import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../ui/Primitives';
import { httpsLinkProblem } from '../../lib/alumniPortal';
import { sanitizePortalHtml } from '../../lib/portalHtml';

type Props = {
  /** Accessible name of the text box. */
  label: string;
  /** The text to start from. The editor owns its content afterwards: remount (key) to load another. */
  initialHtml: string;
  /** Called with sanitised HTML after every change. */
  onChange: (html: string) => void;
};

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] || c);

const TOOLS = ['Bold', 'Italic', 'Heading', 'Bulleted list', 'Numbered list', 'Quote', 'Link', 'Clear formatting'] as const;
type Tool = (typeof TOOLS)[number];

/**
 * A small rich-text editor for posts and event descriptions: bold, italic,
 * heading, lists, quote, link. It writes only what the server keeps (see
 * lib/portalHtml), and what leaves the component has been through that sanitiser.
 */
export default function PortalEditor({ label, initialHtml, onChange }: Props) {
  const labelId = useId();
  const editor = useRef<HTMLDivElement>(null);
  const [linkError, setLinkError] = useState('');

  useEffect(() => {
    const element = editor.current;
    if (!element) return;
    const safe = sanitizePortalHtml(initialHtml);
    element.innerHTML = safe;
    onChange(safe);
    // New lines make paragraphs, not divs.
    document.execCommand('defaultParagraphSeparator', false, 'p');
    // Only the first value: the editor owns its content afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const emit = () => {
    const element = editor.current;
    if (!element) return;
    onChange(element.textContent?.trim() || element.querySelector('li, br') ? sanitizePortalHtml(element.innerHTML) : '');
  };

  const run = (command: string, value?: string) => {
    editor.current?.focus();
    document.execCommand(command, false, value);
    emit();
  };

  /** Headings and quotes switch back to a paragraph when pressed again. */
  const toggleBlock = (tag: 'h2' | 'blockquote') => {
    editor.current?.focus();
    const current = document.queryCommandValue('formatBlock').toLowerCase().replace(/[<>]/g, '');
    run('formatBlock', current === tag ? 'p' : tag);
  };

  const addLink = () => {
    const element = editor.current;
    if (!element) return;
    const selection = window.getSelection();
    const saved = selection && selection.rangeCount && element.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null;
    const url = window.prompt('Link address (must start with https://)', 'https://')?.trim();
    if (!url || url === 'https://') return;
    const problem = httpsLinkProblem(url, 'The link');
    if (problem) { setLinkError(problem); return; }
    setLinkError('');
    element.focus();
    if (saved) { selection?.removeAllRanges(); selection?.addRange(saved); }
    if (selection && !selection.isCollapsed) document.execCommand('createLink', false, url);
    else document.execCommand('insertHTML', false, `<a href="${escapeHtml(url)}">${escapeHtml(url)}</a>`);
    emit();
  };

  const clearFormatting = () => {
    editor.current?.focus();
    document.execCommand('removeFormat');
    document.execCommand('unlink');
    document.execCommand('formatBlock', false, 'p');
    emit();
  };

  const apply = (tool: Tool) => {
    switch (tool) {
      case 'Bold': return run('bold');
      case 'Italic': return run('italic');
      case 'Heading': return toggleBlock('h2');
      case 'Bulleted list': return run('insertUnorderedList');
      case 'Numbered list': return run('insertOrderedList');
      case 'Quote': return toggleBlock('blockquote');
      case 'Link': return addLink();
      case 'Clear formatting': return clearFormatting();
    }
  };

  return (
    <div className="min-w-0 space-y-2">
      <span id={labelId} className="block text-sm font-medium text-deep-navy">{label}</span>
      <div className="flex flex-wrap gap-1" role="toolbar" aria-label={`${label} formatting`}>
        {TOOLS.map((tool) => (
          <Button key={tool} size="sm" variant="secondary"
            // Keep the selection in the text while a button is pressed.
            onMouseDown={(event) => event.preventDefault()} onClick={() => apply(tool)}>
            {tool}
          </Button>
        ))}
      </div>
      {linkError && <p className="text-sm text-[var(--error)]" role="alert">{linkError}</p>}
      <div
        ref={editor}
        className="portal-editor portal-html"
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        aria-labelledby={labelId}
        onInput={emit}
        onPaste={(event) => {
          event.preventDefault();
          const html = event.clipboardData.getData('text/html');
          if (html) document.execCommand('insertHTML', false, sanitizePortalHtml(html));
          else document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
          emit();
        }}
        onDrop={(event) => event.preventDefault()}
      />
    </div>
  );
}
