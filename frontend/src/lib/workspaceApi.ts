import { API_BASE } from '../api';
export async function workspaceRequest<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const token = localStorage.getItem('yucg_token');
  const response = await fetch(`${API_BASE}${path}`, { method, credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  if (!response.ok) {
    const body: { detail?: unknown } = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string' ? detail.message : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
