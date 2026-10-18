import { useRef, type KeyboardEvent } from 'react';

type Item = { id: string; label: string };

type AppSubnavProps = {
  items: Item[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
  label?: string;
};

export default function AppSubnav({ items, active, onChange, className = '', label = 'View' }: AppSubnavProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
    onChange(items[next].id);
    refs.current[next]?.focus();
  };

  return (
    <div className={`app-subnav ${className}`.trim()} role="tablist" aria-label={label}>
      {items.map((item, index) => (
        <button
          key={item.id}
          ref={(node) => { refs.current[index] = node; }}
          type="button"
          role="tab"
          tabIndex={active === item.id ? 0 : -1}
          aria-selected={active === item.id}
          className={`app-subnav-item ${active === item.id ? 'app-subnav-item--active' : ''}`}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
