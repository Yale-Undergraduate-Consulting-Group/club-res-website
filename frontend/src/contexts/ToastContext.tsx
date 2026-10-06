import { ToastContext, type Toast } from './useToast';
/**
 * Toast notifications - bottom-right, progress bar, auto-dismiss
 */
import { useCallback, useEffect, useState } from 'react';

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (message: string, type: 'error' | 'success' | 'info' = 'error', duration = 5000) => {
      const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const createdAt = Date.now();
      setToasts((prev) => [...prev, { id, message, type, duration, createdAt }]);
      setTimeout(() => removeToast(id), duration);
    },
    [removeToast]
  );

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  );
}

function ToastContainer({ toasts, removeToast }: { toasts: Toast[]; removeToast: (id: string) => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (toasts.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 50);
    return () => clearInterval(id);
  }, [toasts.length]);
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      <div className="flex flex-col gap-2 pointer-events-auto">
        {toasts.map((t) => {
          const elapsed = now - t.createdAt;
          const progress = Math.max(0, 100 - (elapsed / t.duration) * 100);
          return (
            <div
              key={t.id}
              className={`app-toast p-4 pr-10 relative overflow-hidden ${
                t.type === 'error'
                  ? 'bg-red-50 border-red-200 text-red-800'
                  : t.type === 'success'
                  ? 'bg-green-50 border-green-200 text-green-800'
                  : 'bg-slate-50 border-slate-200 text-slate-800'
              }`}
            >
              <p className="text-sm font-medium">{t.message}</p>
              <div
                className="absolute bottom-0 left-0 h-1 bg-current opacity-20 transition-none"
                style={{ width: `${progress}%` }}
              />
              <button
                onClick={() => removeToast(t.id)}
                className="absolute top-2 right-2 text-slate-400 hover:text-slate-600 p-1 text-lg leading-none"
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
