// Access to the Lilith web console API.
//
// Without LILITH_AUTH_TOKEN the server only accepts local requests. With a
// token, open the console as http://127.0.0.1:12356/#token=<token>: the
// fragment never reaches the server, and the token is kept in sessionStorage.

const TOKEN_KEY = 'lilith_auth_token';

export class UnauthorizedError extends Error {}

export function captureTokenFromUrl(): void {
  const match = window.location.hash.match(/token=([^&]+)/);
  if (!match) return;
  setToken(decodeURIComponent(match[1]));
  history.replaceState(null, '', window.location.pathname + window.location.search);
}

export function getToken(): string | null {
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token.trim());
}

export async function apiGet<T>(path: string): Promise<T> {
  const token = getToken();
  const response = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401) {
    throw new UnauthorizedError('Missing or invalid token');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function openSocket(path: string): WebSocket {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const endpoint = `${scheme}//${window.location.host}${path}`;
  const token = getToken();
  return token ? new WebSocket(endpoint, ['lilith-auth', token]) : new WebSocket(endpoint);
}
