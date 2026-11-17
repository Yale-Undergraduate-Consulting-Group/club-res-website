import { Link, useOutletContext } from 'react-router-dom';
import { usableModules } from '../modules';
import { memberFirstName } from '../lib/memberName';

/**
 * The portal's front door, the whole page with no bar: one icon per app this
 * member may use, centred. It fetches nothing; every app manifest except
 * core's gets a tile. Opening one brings in that app's own bar.
 */
export default function Hub() {
  const { user, onLogout, alumni } = useOutletContext<{
    user: { name?: string; email: string; role?: string; apps?: string[] };
    onLogout: () => void;
    /** An alumni session is valid too (components/shell/AppShell.tsx). */
    alumni: boolean;
  }>();
  const apps = usableModules(user.apps).filter((module) => module.id !== 'core');

  return (
    <div className="app-workspace flex hub">
      <header className="hub-header">
        <img src="/yucg-logo.png" alt="YUCG" className="hub-logo" decoding="async" />
        <h1 className="hub-title">Welcome, {memberFirstName(user)}</h1>
      </header>
      <section aria-label="Your tools" className="hub-tools">
        {apps.map((module) => (
          <Link key={module.id} to={module.nav[0].to} className="hub-tile">
            <span className="hub-tile__icon">{module.icon ?? <span className="hub-tile__initial">{module.title[0]}</span>}</span>
            <span className="hub-tile__name">{module.title}</span>
          </Link>
        ))}
      </section>
      <nav className="hub-footer" aria-label="Account">
        <Link to="/profile">Profile</Link>
        {user.role === 'admin' && <Link to="/admin">Admin</Link>}
        {alumni && <Link to="/alumni">Switch to alumni</Link>}
        <button type="button" onClick={onLogout}>Log out</button>
      </nav>
    </div>
  );
}
