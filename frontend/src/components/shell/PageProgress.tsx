import { useSyncExternalStore } from 'react';
import { isBusy, subscribe } from '../../lib/pageProgress';

/**
 * A slim bar along the top of the window while the next page's code or first
 * data is on its way. The page already on screen stays put underneath it; the
 * bar takes no room, so nothing moves when it comes and goes.
 */
export default function PageProgress() {
  const busy = useSyncExternalStore(subscribe, isBusy);
  return busy ? <div className="app-page-progress" role="progressbar" aria-label="Loading page" /> : null;
}
