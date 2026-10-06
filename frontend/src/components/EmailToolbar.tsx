import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * The formatting a member expects because Gmail has it: font, size, weight,
 * colour, alignment, lists, indent, quote, link, and a way back to plain.
 *
 * Two rules make it safe and portable. Commands run with styleWithCSS, so the
 * editor emits spans with inline CSS rather than <font> tags — that is what
 * mail clients render and what the sanitizer's declaration allowlist accepts.
 * And the buttons report state from the live selection, so the bar says what
 * the caret is actually in rather than what was last pressed.
 */

type Props = {
  /** The contentEditable the commands apply to. */
  editorRef: React.RefObject<HTMLDivElement | null>;
  /** Called with the editor's HTML after every command. */
  onChange: (html: string) => void;
};

const FONTS: { label: string; value: string }[] = [
  { label: 'Sans serif', value: "Lato, Helvetica, Arial, sans-serif" },
  { label: 'Serif', value: "Georgia, 'Times New Roman', serif" },
  { label: 'Fixed width', value: "'SFMono-Regular', Menlo, Consolas, monospace" },
  { label: 'Wide', value: "Verdana, Geneva, sans-serif" },
  { label: 'Narrow', value: "'Arial Narrow', Arial, sans-serif" },
];

const SIZES: { label: string; value: string }[] = [
  { label: 'Small', value: '12px' },
  { label: 'Normal', value: '14px' },
  { label: 'Large', value: '18px' },
  { label: 'Huge', value: '24px' },
];

/** execCommand names whose on/off state the bar mirrors. */
const TOGGLES = ['bold', 'italic', 'underline', 'strikeThrough',
  'insertUnorderedList', 'insertOrderedList', 'justifyLeft', 'justifyCenter', 'justifyRight'] as const;

type Toggle = (typeof TOGGLES)[number];


/** Character offsets of a range inside the editor's text.
 *
 * Styling wraps text in new elements, which discards a stored Range and
 * collapses the caret — after one click the selection was gone and the next
 * format applied to nothing. Offsets survive the rewrite because styling
 * never changes the text itself. */
function offsetsOf(editor: HTMLElement, range: Range): { start: number; end: number } | null {
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  let seen = 0;
  let start: number | null = null;
  let end: number | null = null;
  let node = walker.nextNode();
  while (node) {
    if (node === range.startContainer) start = seen + range.startOffset;
    if (node === range.endContainer) end = seen + range.endOffset;
    seen += (node.textContent || '').length;
    node = walker.nextNode();
  }
  if (start == null || end == null || start === end) return null;
  return { start, end };
}

function rangeAt(editor: HTMLElement, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let seen = 0;
  let anchored = false;
  let node = walker.nextNode();
  while (node) {
    const length = (node.textContent || '').length;
    if (!anchored && seen + length >= start) {
      range.setStart(node, Math.max(0, start - seen));
      anchored = true;
    }
    if (anchored && seen + length >= end) {
      range.setEnd(node, Math.max(0, end - seen));
      return range;
    }
    seen += length;
    node = walker.nextNode();
  }
  return anchored ? range : null;
}
export default function EmailToolbar({ editorRef, onChange }: Props) {
  const [active, setActive] = useState<Record<string, boolean>>({});
  const [font, setFont] = useState(FONTS[0].value);
  const [size, setSize] = useState('14px');
  const colorRef = useRef<HTMLInputElement>(null);
  const highlightRef = useRef<HTMLInputElement>(null);

  // A menu that takes focus, and styling that rewraps the text, both destroy
  // the selection. What is remembered is the span of characters, not a Range
  // whose nodes a command may replace.
  const savedSpan = useRef<{ start: number; end: number } | null>(null);

  const selectionInEditor = useCallback(() => {
    const editor = editorRef.current;
    const selection = document.getSelection();
    return Boolean(editor && selection?.anchorNode && editor.contains(selection.anchorNode));
  }, [editorRef]);

  /** Remember the live selection while it is still the editor's. */
  const saveSelection = useCallback(() => {
    const editor = editorRef.current;
    const selection = document.getSelection();
    if (!editor || !selectionInEditor() || !selection?.rangeCount) return;
    savedSpan.current = offsetsOf(editor, selection.getRangeAt(0)) ?? savedSpan.current;
  }, [editorRef, selectionInEditor]);

  const readState = useCallback(() => {
    if (!selectionInEditor()) return;
    saveSelection();
    const next: Record<string, boolean> = {};
    for (const command of TOGGLES) {
      try { next[command] = document.queryCommandState(command); } catch { next[command] = false; }
    }
    setActive(next);
  }, [saveSelection, selectionInEditor]);

  useEffect(() => {
    document.addEventListener('selectionchange', readState);
    return () => document.removeEventListener('selectionchange', readState);
  }, [readState]);

  const run = useCallback((command: string, value?: string) => {
    const editor = editorRef.current;
    if (!editor) return;
    const span = savedSpan.current;
    editor.focus();
    const live = document.getSelection();
    const lost = !selectionInEditor() || (live?.isCollapsed ?? true);
    if (lost && span) {
      const restored = rangeAt(editor, span.start, span.end);
      if (restored) {
        live?.removeAllRanges();
        live?.addRange(restored);
      }
    }
    // Inline CSS rather than <font>: one representation for the editor, the
    // preview, the sanitizer and the sent message.
    try { document.execCommand('styleWithCSS', false, 'true'); } catch { /* not supported */ }
    document.execCommand(command, false, value);
    // Styling rewraps the text and drops the selection; put it back so the
    // next format lands on the same words, the way a mail client behaves.
    if (span) {
      const again = rangeAt(editor, span.start, span.end);
      if (again) {
        const selection = document.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(again);
      }
    }
    onChange(editor.innerHTML);
    readState();
  }, [editorRef, onChange, readState, selectionInEditor]);

  /** Menus and colour pickers take focus; capture the selection first. */
  const menuProps = {
    onMouseDown: saveSelection,
    onFocus: saveSelection,
    onKeyDown: saveSelection,
  };

  // A plain render helper, not a nested component: a component defined during
  // render is a new type on every keystroke and remounts its button.
  const toggle = (command: Toggle | string, label: string, title: string, className = '') => (
    <button
      key={command}
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active[command] || undefined}
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => run(command)}
      className={`email-toolbar__button ${active[command] ? 'is-active' : ''} ${className}`.trim()}
    >
      {label}
    </button>
  );

  return (
    <div className="email-toolbar" role="toolbar" aria-label="Message formatting">
      <select
        aria-label="Font"
        className="ui-input ui-input--sm email-toolbar__select"
        value={font}
        {...menuProps}
        onChange={(event) => { setFont(event.target.value); run('fontName', event.target.value); }}
      >
        {FONTS.map((option) => <option key={option.label} value={option.value}>{option.label}</option>)}
      </select>
      <select
        aria-label="Text size"
        className="ui-input ui-input--sm email-toolbar__select"
        value={size}
        {...menuProps}
        onChange={(event) => {
          setSize(event.target.value);
          // execCommand only speaks 1–7; the CSS size is applied to the
          // resulting span so the message carries a real px value.
          run('fontSize', '4');
          const editor = editorRef.current;
          if (!editor) return;
          editor.querySelectorAll('span[style*="font-size"], font[size="4"]').forEach((node) => {
            (node as HTMLElement).removeAttribute('size');
            (node as HTMLElement).style.fontSize = event.target.value;
          });
          onChange(editor.innerHTML);
        }}
      >
        {SIZES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>

      <span className="email-toolbar__divider" aria-hidden />
      {toggle('bold', 'B', 'Bold', 'font-bold')}
      {toggle('italic', 'I', 'Italic', 'italic')}
      {toggle('underline', 'U', 'Underline', 'underline')}
      {toggle('strikeThrough', 'S', 'Strikethrough', 'line-through')}

      <span className="email-toolbar__divider" aria-hidden />
      <label className="email-toolbar__color" title="Text colour">
        <span aria-hidden>A</span>
        <input
          ref={colorRef}
          type="color"
          defaultValue="#1a1a1a"
          aria-label="Text colour"
          onChange={(event) => run('foreColor', event.target.value)}
        />
      </label>
      <label className="email-toolbar__color email-toolbar__color--fill" title="Highlight">
        <span aria-hidden>▦</span>
        <input
          ref={highlightRef}
          type="color"
          defaultValue="#fff2a8"
          aria-label="Highlight colour"
          onChange={(event) => run('hiliteColor', event.target.value)}
        />
      </label>

      <span className="email-toolbar__divider" aria-hidden />
      {toggle('justifyLeft', '⬅', 'Align left')}
      {toggle('justifyCenter', '↔', 'Align centre')}
      {toggle('justifyRight', '➡', 'Align right')}

      <span className="email-toolbar__divider" aria-hidden />
      {toggle('insertUnorderedList', '•', 'Bulleted list')}
      {toggle('insertOrderedList', '1.', 'Numbered list')}
      {toggle('outdent', '⇤', 'Indent less')}
      {toggle('indent', '⇥', 'Indent more')}
      <button
        type="button"
        title="Quote"
        aria-label="Quote"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => run('formatBlock', 'blockquote')}
        className="email-toolbar__button"
      >
        ❝
      </button>

      <span className="email-toolbar__divider" aria-hidden />
      <button
        type="button"
        title="Insert link"
        aria-label="Insert link"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          const url = window.prompt('Link URL (https:// or mailto:)');
          if (!url) return;
          if (!/^(?:https?:\/\/|mailto:)/i.test(url.trim())) {
            window.alert('Links must start with https://, http:// or mailto:');
            return;
          }
          run('createLink', url.trim());
        }}
        className="email-toolbar__button"
      >
        Link
      </button>
      <button
        type="button"
        title="Remove formatting"
        aria-label="Remove formatting"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => { run('removeFormat'); run('unlink'); }}
        className="email-toolbar__button"
      >
        Clear
      </button>
    </div>
  );
}
