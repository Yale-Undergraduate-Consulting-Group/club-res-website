import { Link, useOutletContext } from 'react-router-dom';
import { usableModules } from '../modules';

/**
 * The portal's front door: one icon per app this member may use, centred.
 * It fetches nothing; every app manifest except core's gets a tile.
 */
export default function Hub() {
  const { user } = useOutletContext<{ user: { name?: string; email: string; role?: string; apps?: string[] } }>();
  const firstName = (user.name || user.email.split('@')[0]).split(' ')[0];
  const apps = usableModules(user.apps).filter((module) => module.id !== 'core');

  return (
    <div className="app-workspace flex hub">
      <header className="hub-header">
        <img src="/yucg-logo.png" alt="" className="hub-logo" decoding="async" />
        <h1 className="hub-title">Welcome, {firstName}</h1>
      </header>
      <section aria-label="Your tools" className="hub-tools">
        {apps.map((module) => (
          <Link key={module.id} to={module.nav[0].to} className="hub-tile">
            <span className="hub-tile__icon">{module.icon ?? <span className="hub-tile__initial">{module.title[0]}</span>}</span>
            <span className="hub-tile__name">{module.title}</span>
          </Link>
        ))}
      </section>
      {user.role === 'admin' && (
        <p className="hub-admin">
          <Link to="/admin">Admin</Link>
        </p>
      )}
    </div>
  );
}
