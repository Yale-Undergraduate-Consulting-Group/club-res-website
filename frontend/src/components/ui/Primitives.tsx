import { forwardRef, useEffect, useRef, useId, type ButtonHTMLAttributes, type ReactNode } from 'react';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
  /** Square button for a glyph or icon with no visible text; needs an aria-label. */
  iconOnly?: boolean;
};

/**
 * The one action button. Height, padding, radius and weight come from the size
 * alone (md = --control-height, sm = --control-height-sm); the variant only
 * changes colour, so a button never changes size because of how it looks.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', iconOnly = false, className = '', type = 'button', ...props },
  ref,
) {
  const classes = `ui-button ui-button--${variant} ui-button--${size}${iconOnly ? ' ui-button--icon' : ''} ${className}`.trim();
  return <button ref={ref} type={type} className={classes} {...props} />;
});

type ChipProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'role'> & {
  /** Current state of a toggle or segmented choice; omit for a plain pill action. */
  selected?: boolean;
  /** Use "radio" for one-of-many groups (the group needs role="radiogroup"). */
  role?: 'radio';
};

/**
 * The one pill for toggles, filters and segmented choices. Selected and
 * unselected share every dimension (1px border, same padding and font); only
 * colour changes with state.
 */
export const Chip = forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { selected, role, className = '', type = 'button', ...props },
  ref,
) {
  const state = role === 'radio' ? { 'aria-checked': Boolean(selected) } : selected === undefined ? {} : { 'aria-pressed': selected };
  return <button ref={ref} type={type} role={role} className={`ui-chip ${className}`.trim()} {...state} {...props} />;
});

export function Notice({ children, tone = 'info', className = '' }: { children: ReactNode; tone?: 'info' | 'success' | 'warning' | 'danger'; className?: string }) {
  return <div className={`ui-notice ui-notice--${tone} ${className}`.trim()} role={tone === 'danger' ? 'alert' : 'status'}>{children}</div>;
}

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger' }) {
  return <span className={`ui-status ui-status--${tone}`}>{children}</span>;
}

export function EmptyState({ title, body, action, className = '' }: { title: string; body?: string; action?: ReactNode; className?: string }) {
  return (
    <div className={`ui-empty ${className}`.trim()}>
      <h3>{title}</h3>
      {body && <p>{body}</p>}
      {action && <div className="ui-empty__action">{action}</div>}
    </div>
  );
}

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

export function ConfirmDialog({ open, title, body, confirmLabel, danger = false, busy = false, onConfirm, onClose }: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const titleId = useId();
  // The page re-renders while the dialog is open (data arriving, inline
  // onClose); reading these through a ref keeps focus where the member put it
  // instead of resetting it on every render.
  const latest = useRef({ busy, onClose });
  useEffect(() => { latest.current = { busy, onClose }; }, [busy, onClose]);
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !latest.current.busy) latest.current.onClose();
      if (event.key === 'Tab') {
        const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || []);
        const first = controls[0]; const last = controls.at(-1);
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => { window.removeEventListener('keydown', onKeyDown); previousFocus?.focus(); };
  }, [open]);
  if (!open) return null;

  return (
    <div className="ui-dialog-root" role="presentation">
      <button className="ui-dialog-backdrop" aria-label="Close dialog" onClick={onClose} disabled={busy} />
      <section ref={dialogRef} className="ui-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId}>{title}</h2>
        <div className="ui-dialog__body">{body}</div>
        <div className="ui-dialog__actions">
          <Button ref={cancelRef} variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</Button>
        </div>
      </section>
    </div>
  );
}
