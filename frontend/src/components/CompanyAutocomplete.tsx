import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { api } from '../api';

export type CompanyOption = {
  name: string;
  domain?: string;
  source: 'pipeline' | 'register';
  contactCount?: number;
  sector?: string;
  hint?: string;
};

function norm(value: string): string {
  return value.trim().toLowerCase();
}

export default function CompanyAutocomplete({
  id,
  label,
  value,
  onChange,
  onSelect,
  onSubmitText,
  placeholder = 'Company name',
  disabled = false,
}: {
  id?: string;
  label?: string;
  value: string;
  onChange: (name: string, option?: CompanyOption) => void;
  onSelect?: (option: CompanyOption) => void;
  /** Enter with no row chosen, or a paste: the text itself, for the caller
   *  to resolve. Without this Enter would take the top row, which for "HBO"
   *  was a UK production subsidiary nobody asked for. */
  onSubmitText?: (text: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const autoId = useId();
  const inputId = id || autoId;
  const listId = `${inputId}-list`;
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<CompanyOption[]>([]);
  const [registerOptions, setRegisterOptions] = useState<CompanyOption[]>([]);
  // -1: no row chosen yet. A row is only taken once the member points at it.
  const [highlight, setHighlight] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.contacts.companiesSummary().then((summary) => {
      if (cancelled) return;
      const pipeline = (Array.isArray(summary) ? summary : []).map((row) => ({
        name: row.company,
        domain: row.company_domain || undefined,
        source: 'pipeline' as const,
        contactCount: row.contact_count,
      }));
      setOptions(pipeline.sort((a, b) => a.name.localeCompare(b.name)));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // The club's own companies live in memory, but the public register holds
  // 100k+ and cannot. Query it as the member types so a company nobody has
  // worked yet is reachable from this field instead of only from the register
  // tab. Debounced, and the in-flight request is aborted on the next keystroke.
  useEffect(() => {
    const q = value.trim();
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (q.length < 3) {
        setRegisterOptions([]);
        return;
      }
      api.yucgoutreach.register({ q, limit: 8 }, controller.signal)
        .then((res) => {
          setRegisterOptions((res.items || []).map((row) => ({
            name: row.company_name,
            domain: row.company_domain || undefined,
            source: 'register' as const,
            sector: row.sector_label || undefined,
            hint: [row.region, row.officer_count ? `${row.officer_count} officers on file` : null]
              .filter(Boolean).join(' · ') || undefined,
          })));
        })
        .catch(() => { /* abort or offline: the local lists still answer */ });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [value]);

  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, []);

  const filtered = useMemo(() => {
    const q = norm(value);
    if (!q) return options.slice(0, 12);
    const local = options.filter((option) =>
      norm(option.name).includes(q) || (option.domain && norm(option.domain).includes(q))
    );
    // Companies the club already works come first and are never displaced by
    // the public register; register rows only fill the remaining slots, minus
    // any the club already has.
    const known = new Set(local.map((option) => norm(option.name)));
    const fromRegister = registerOptions.filter((option) => !known.has(norm(option.name)));
    return [...local, ...fromRegister].slice(0, 12);
  }, [options, registerOptions, value]);

  const pick = (option: CompanyOption) => {
    onChange(option.name, option);
    onSelect?.(option);
    setOpen(false);
  };

  return (
    <div ref={wrapRef} className="relative">
      {label && <label htmlFor={inputId} className="block text-xs font-medium text-slate-600 mb-1">{label}</label>}
      <input
        id={inputId}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && filtered[highlight] ? `${listId}-${highlight}` : undefined}
        disabled={disabled}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onFocus={() => setOpen(true)}
        onPaste={(event) => {
          // A pasted LinkedIn page, or a pasted list of companies, needs no
          // further typing: hand it over whole.
          const text = event.clipboardData.getData('text').trim();
          if (onSubmitText && (/linkedin\.com\/(company|showcase)\//i.test(text) || /[\n\r]/.test(text))) {
            event.preventDefault();
            setOpen(false);
            onSubmitText(text);
          }
        }}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
          setHighlight(-1);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
            setHighlight((current) => Math.min(filtered.length - 1, current + 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHighlight((current) => Math.max(-1, current - 1));
          } else if (event.key === 'Enter' && open && filtered[highlight]) {
            event.preventDefault();
            pick(filtered[highlight]);
          } else if (event.key === 'Enter' && value.trim() && onSubmitText) {
            event.preventDefault();
            setOpen(false);
            onSubmitText(value.trim());
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
        className="ui-input"
      />
      {open && filtered.length > 0 && (
        <ul id={listId} role="listbox" className="company-autocomplete-list absolute z-30 mt-1 w-full max-h-64 overflow-auto rounded-xl border border-pale-sky bg-white shadow-lg">
          {filtered.map((option, index) => (
            <li
              id={`${listId}-${index}`}
              key={`${option.source}-${option.name}-${option.domain || index}`}
              role="option"
              aria-selected={index === highlight}
              className={`px-3 py-2 cursor-pointer text-sm ${index === highlight ? 'bg-pale-sky/50' : 'hover:bg-pale-sky/30'}`}
              onMouseEnter={() => setHighlight(index)}
              onMouseDown={(event) => { event.preventDefault(); pick(option); }}
            >
              <div className="font-medium text-deep-navy">{option.name}</div>
              <div className="text-xs text-slate-500">
                {option.domain || option.sector
                  || (option.source === 'pipeline' ? 'In pipeline' : 'Public register')}
                {option.source === 'register' && option.hint ? ` · ${option.hint}` : ''}
                {option.contactCount != null ? ` · ${option.contactCount} saved` : ''}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
