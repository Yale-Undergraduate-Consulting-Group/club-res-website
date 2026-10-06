import { createContext, useContext } from 'react';
export type Theme = 'light' | 'dark';

type ThemeContextValue = {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleDark: () => void;
};
export const ThemeContext = createContext<ThemeContextValue | null>(null);
export function useTheme() {
  const ctx = useContext(ThemeContext);
  return ctx || { theme: 'light' as Theme, setTheme: () => {}, toggleDark: () => {} };
}
