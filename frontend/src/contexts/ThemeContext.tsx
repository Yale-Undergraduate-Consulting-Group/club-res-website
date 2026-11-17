import { ThemeContext, type Theme } from './useTheme';
/**
 * Theme: the member's choice (light, dark or system) persists to localStorage;
 * the resolved light or dark look is the `dark` class on <html>. System
 * follows prefers-color-scheme live, without a reload.
 */
import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { applyStoredPreferences } from '../lib/userPreferences';

const STORAGE_KEY = 'yucg_theme';
const NEXT: Record<Theme, Theme> = { light: 'dark', dark: 'system', system: 'light' };
const DARK_QUERY = '(prefers-color-scheme: dark)';

function storedTheme(): Theme {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s === 'dark' || s === 'light' || s === 'system') return s;
  } catch { /* Storage may be unavailable; keep the in-memory preference. */ }
  return 'light';
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(DARK_QUERY).matches);

  useEffect(() => {
    const query = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  const dark = theme === 'dark' || (theme === 'system' && systemDark);

  // Before paint, so a page never flashes the other theme.
  useLayoutEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    // Re-derive --btn-primary-* for light vs dark (accent-based)
    applyStoredPreferences();
  }, [dark]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch { /* Storage may be unavailable; keep the in-memory preference. */ }
  }, [theme]);

  const cycleTheme = useCallback(() => setTheme((current) => NEXT[current]), []);

  return (
    <ThemeContext.Provider value={{ theme, cycleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
