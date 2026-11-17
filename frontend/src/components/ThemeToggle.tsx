import type { ReactNode } from 'react';
import { useTheme, type Theme } from '../contexts/useTheme';
import { Button } from './ui/Primitives';

/** What each choice is called, what pressing it switches to, and its icon (kit stroke style, like AppIcons). */
const MODES: Record<Theme, { name: string; next: string; icon: ReactNode }> = {
  light: {
    name: 'Light',
    next: 'Switch to dark theme',
    icon: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" /></>,
  },
  dark: {
    name: 'Dark',
    next: 'Switch to system theme',
    icon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  },
  system: {
    name: 'System',
    next: 'Switch to light theme',
    icon: <><rect x="3" y="4.5" width="18" height="12" /><path d="M9 20h6M12 16.5V20" /></>,
  },
};

/** The top bar's theme button: the same square as the help button beside it; it cycles Light, Dark, System. */
export default function ThemeToggle() {
  const { theme, cycleTheme } = useTheme();
  const mode = MODES[theme];
  const label = `Theme: ${mode.name}${theme === 'system' ? ' (follows your device)' : ''}. ${mode.next}`;
  return (
    <Button iconOnly size="sm" variant="secondary" onClick={cycleTheme} aria-label={label} title={label}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6"
           strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        {mode.icon}
      </svg>
    </Button>
  );
}
