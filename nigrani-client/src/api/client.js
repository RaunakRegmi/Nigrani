import axios from 'axios';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1';

export const TOKEN_KEY = 'nigrani_token';
export const ROLE_KEY = 'nigrani_role';
export const USER_KEY = 'nigrani_user';

// Fired when the API rejects our token; App listens and returns to the login screen
export const AUTH_EXPIRED_EVENT = 'nigrani:auth-expired';

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (response) => response,
  (error) => {
    // A 401 from /token is just a wrong password, not an expired session
    const isLogin = error.config?.url?.endsWith('/token');
    if (error.response?.status === 401 && !isLogin) {
      clearSession();
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    }
    return Promise.reject(error);
  },
);

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(USER_KEY);
}

function tokenExpired(token) {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload.exp === 'number' && payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

/** The stored session, or null if missing or expired. */
export function getSession() {
  const token = localStorage.getItem(TOKEN_KEY);
  const role = localStorage.getItem(ROLE_KEY);
  if (!token || !role || tokenExpired(token)) {
    clearSession();
    return null;
  }
  return { token, role, username: localStorage.getItem(USER_KEY) ?? role };
}

/** OAuth2 password flow: /token expects form-encoded credentials, not JSON. */
export async function login(username, password) {
  const form = new URLSearchParams({ username, password });
  const { data } = await client.post('/token', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(ROLE_KEY, data.role);
  localStorage.setItem(USER_KEY, username);
  return { token: data.access_token, role: data.role, username };
}

/** A human-readable message for any API failure. Never throws. */
export function describeError(error) {
  if (!error?.response) {
    if (error?.code === 'ECONNABORTED') return 'The Nigrani API did not respond in time.';
    return `Cannot reach the Nigrani API at ${API_BASE_URL}. Is the FastAPI server running?`;
  }
  const { status, data } = error.response;
  if (status === 422 && Array.isArray(data?.detail)) {
    const fields = data.detail.map((d) => d.loc?.at(-1)).filter(Boolean).join(', ');
    return `Invalid input: ${fields || 'check the form'}.`;
  }
  if (status === 403) return 'Your role is not allowed to perform this action.';
  if (status === 503) return 'The feature store is unavailable. Try again shortly.';
  return typeof data?.detail === 'string' ? data.detail : `Request failed (${status}).`;
}

export default client;
