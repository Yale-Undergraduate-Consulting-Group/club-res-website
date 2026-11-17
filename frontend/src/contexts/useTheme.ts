import { createContext, useContext } from 'react';

/** The member's choice; 'system' follows the operating system's light or dark setting live. */
export type Theme = 'light' | 'dark' | 'system';

type ThemeContextValue = {
  theme: Theme;
  /** Light → Dark → System → Light. */
  cycleTheme: () => void;
};
export const ThemeContext = createContext<ThemeContextValue | null>(null);
export function useTheme() {
  const ctx = useContext(ThemeContext);
  return ctx || { theme: 'light' as Theme, cycleTheme: () => {} };
}
