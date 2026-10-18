import { ThemeContext , type Theme } from './useTheme';
/**
 * Theme (light/dark) - persists to localStorage, applies class to document
 */
import { useCallback, useEffect, useState } from 'react';
import { applyStoredPreferences } from '../lib/userPreferences';

const STORAGE_KEY = 'yucg_theme';

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      const s = localStorage.getItem(STORAGE_KEY);
      if (s === 'dark' || s === 'light') return s;
    } catch { /* Storage may be unavailable; keep the in-memory preference. */ }
    return 'light';
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch { /* Storage may be unavailable; keep the in-memory preference. */ }
    // Re-derive --btn-primary-* for light vs dark (accent-based)
    applyStoredPreferences();
  }, [theme]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggleDark = useCallback(() => setThemeState((p) => (p === 'dark' ? 'light' : 'dark')), []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleDark }}>
      {children}
    </ThemeContext.Provider>
  );
}
