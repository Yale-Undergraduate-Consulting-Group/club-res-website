/**
 * Whether the page being opened is still on its way: its code (a route the
 * router is still loading) or the first data it asked for. The app shell
 * shows a slim bar while this is true, over the page already on screen, so a
 * menu change never goes blank. Requests a page makes later (polling, a
 * button) do not count: only those started just after the page appeared.
 */

/** Requests started this soon after a page appears are that page's first load. */
const FIRST_LOAD_MS = 400;

let navigating = false;
let firstLoadUntil = 0;
let firstLoads = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function isBusy(): boolean {
  return navigating || firstLoads > 0;
}

/** The router has been asked for another page that is not on screen yet. */
export function navigationStarted() {
  if (navigating) return;
  navigating = true;
  emit();
}

/** The new page is on screen; its first requests follow. */
export function pageShown() {
  firstLoadUntil = performance.now() + FIRST_LOAD_MS;
  if (!navigating) return;
  navigating = false;
  emit();
}

/** Counts a request toward the page's first load when it starts in that window. */
export function trackRequest<T>(request: Promise<T>): Promise<T> {
  if (performance.now() > firstLoadUntil) return request;
  firstLoads += 1;
  emit();
  const settle = () => {
    firstLoads -= 1;
    emit();
  };
  request.then(settle, settle);
  return request;
}
