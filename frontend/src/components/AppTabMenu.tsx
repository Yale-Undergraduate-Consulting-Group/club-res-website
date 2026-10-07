import { useRef, type KeyboardEvent } from 'react';

type Tab = { id: string; label: string };

type AppTabMenuProps = {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
  label?: string;
};

export default function AppTabMenu({ tabs, active, onChange, className = '', label = 'Page sections' }: AppTabMenuProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    onChange(tabs[next].id);
    refs.current[next]?.focus();
  };

  return (
    <div className={`app-tab-menu ${className}`.trim()} role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          ref={(node) => { refs.current[index] = node; }}
          type="button"
          role="tab"
          tabIndex={active === tab.id ? 0 : -1}
          aria-selected={active === tab.id}
          className={`app-tab-menu-item ${active === tab.id ? 'app-tab-menu-item--active' : ''}`}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
