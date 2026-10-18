import { createContext, useContext } from 'react';
export type Toast = {
  id: string;
  message: string;
  type: 'error' | 'success' | 'info';
  duration: number;
  createdAt: number;
};

type ToastContextValue = {
  toasts: Toast[];
  addToast: (message: string, type?: 'error' | 'success' | 'info', duration?: number) => void;
  removeToast: (id: string) => void;
};
export const ToastContext = createContext<ToastContextValue | null>(null);
export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) return { addToast: () => {}, toasts: [], removeToast: () => {} };
  return ctx;
}
