import type { ReactNode } from 'react';


type PageHeaderProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
};

export default function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <header className="app-page-header">
      <div>
        <h1 className="text-2xl font-bold text-deep-navy dark:text-[var(--text-primary)]">{title}</h1>
        {subtitle && <p className="mt-2 text-sm text-[var(--text-secondary)]">{subtitle}</p>}
      </div>
      {actions ? <div className="app-page-header-actions">{actions}</div> : null}
    </header>
  );
}
