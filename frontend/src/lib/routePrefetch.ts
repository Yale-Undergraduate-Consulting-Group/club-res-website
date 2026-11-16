import { matchPath } from 'react-router-dom';
import type { AppModule } from '../modules';

function ignore() {}

/** Fetches the code of the page at `pathname` (a hovered or focused link), so opening it does not wait. */
export function prefetchPath(modules: AppModule[], pathname: string) {
  for (const module of modules) {
    for (const route of module.routes) {
      const pattern = 'index' in route ? '/' : `/${route.path}`;
      if (route.preload && matchPath({ path: pattern, end: true }, pathname)) {
        route.preload().catch(ignore);
        return;
      }
    }
  }
}

/** Fetches every page the member may open, once the browser is idle; not on a data-saving connection. */
export function prefetchWhenIdle(modules: AppModule[]): () => void {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  if (connection?.saveData) return ignore;
  const run = () => {
    for (const module of modules) for (const route of module.routes) route.preload?.().catch(ignore);
  };
  if ('requestIdleCallback' in window) {
    const handle = window.requestIdleCallback(run, { timeout: 4000 });
    return () => window.cancelIdleCallback(handle);
  }
  const timer = setTimeout(run, 1500);
  return () => clearTimeout(timer);
}
