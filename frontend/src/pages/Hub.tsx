import { Link, useOutletContext } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { usableModules } from '../modules';

/**
 * The portal's front door: one card per app this member may use. It fetches
 * nothing; the cards come from the app manifests, so a new app appears here
 * as soon as its manifest carries a description.
 */
export default function Hub() {
  const { user } = useOutletContext<{ user: { name?: string; email: string; role?: string; apps?: string[] } }>();
  const firstName = (user.name || user.email.split('@')[0]).split(' ')[0];
  const apps = usableModules(user.apps).filter((module) => module.description);

  return (
    <div className="app-workspace max-w-5xl space-y-4">
      <PageHeader title={`Welcome, ${firstName}`} subtitle="YUCG Resources: the club's tools in one place." />
      <section aria-label="Your tools" className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {apps.map((module) => (
          <Link key={module.id} to={module.nav[0].to} className="surface-card block hover:border-[var(--accent)]">
            <h2 className="app-section-title mb-1">{module.title}</h2>
            <p className="text-sm text-slate-600">{module.description}</p>
          </Link>
        ))}
      </section>
      {user.role === 'admin' && (
        <p className="text-sm text-slate-600">
          Administrator: <Link to="/admin" className="font-semibold text-[var(--accent)] hover:underline">Admin</Link>{' '}
          manages members and app access.
        </p>
      )}
    </div>
  );
}
