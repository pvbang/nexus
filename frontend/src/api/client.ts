// ============================================================
// Base HTTP Client - Generic helpers with timeout, retry, error handling
// ============================================================

import { DEFAULT_TIMEOUT_MS, REFRESH_TOKEN_KEY } from './constants'
import { getAuthHeaders, isTokenExpired, refreshAccessToken } from './auth'
import { API_URL } from '../lib/constants'
import { useAuthStore } from '../stores/authStore'
import { useNotificationStore } from '../stores/notificationStore'

function _getNotify() { return useNotificationStore.getState() }

export const devLog = (...args: unknown[]): void => {
  if (import.meta.env.DEV) console.log(...args)
}
export const devWarn = (...args: unknown[]): void => {
  if (import.meta.env.DEV) console.warn(...args)
}
export const devError = (...args: unknown[]): void => {
  if (import.meta.env.DEV) console.error(...args)
}

// L-03 FIX: Lazy-load RateLimitIndicator once (cached promise)
let _rateLimitUpdaterPromise: Promise<(limit: number, remaining: number) => void> | null = null
function _getRateLimitUpdater(): Promise<(limit: number, remaining: number) => void> {
  if (!_rateLimitUpdaterPromise) {
    _rateLimitUpdaterPromise = import('../components/ui/RateLimitIndicator').then(m => m.updateRateLimit)
  }
  return _rateLimitUpdaterPromise
}

/**
 * Wrapper around `fetch()` that enforces a timeout via `AbortController`.
 * If the request takes longer than `timeoutMs` it is aborted and a
 * descriptive error is thrown.
 *
 * NOTE: Do NOT use for SSE streams (`streamChat`) or file uploads
 * (`uploadAttachments`) — those need long-lived connections.
 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const existingSignal = options.signal

  // If the caller already supplies a signal, respect it as well
  if (existingSignal) {
    existingSignal.addEventListener('abort', () => controller.abort(existingSignal.reason))
  }

  const timer = setTimeout(() => controller.abort(new DOMException('Request timeout', 'TimeoutError')), timeoutMs)

  try {
    const res = await fetch(url, { ...options, signal: controller.signal })
    return res
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // Distinguish caller-abort from timeout-abort
      if (existingSignal?.aborted) throw err
      throw new Error(`Request timed out after ${timeoutMs}ms: ${url}`)
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

/** Parse error detail from a non-ok response */
async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const errorBody = await res.json()
    return errorBody.detail || errorBody.message || errorBody.error || JSON.stringify(errorBody)
  } catch {
    return `API error: ${res.status} ${res.statusText}`
  }
}

// Handles 401, 429, 5xx and network errors with user-facing toasts.
async function handleResponseError(res: Response): Promise<never> {
  const notify = _getNotify()

  if (res.status === 401) {
    // FIX-CASCADE-LOGOUT: Only logout if token is genuinely expired.
    // Prevents a single transient 401 from wiping a valid token and
    // cascading failures across all concurrent/subsequent requests.
    if (isTokenExpired()) {
      useAuthStore.getState().logout()
      notify.add('error', 'Session expired', 'Please log in again.', 8000)
    }
    throw new Error('Unauthorized: session expired')
  }

  if (res.status === 429) {
    const retryAfter = res.headers.get('Retry-After')
    const seconds = retryAfter ? parseInt(retryAfter, 10) : 30
    notify.add('warning', 'Rate limited', `Too many requests. Try again in ${seconds}s.`, 6000)
    throw new Error(`Rate limited (429). Retry after ${seconds}s`)
  }

  if (res.status >= 500) {
    notify.add('error', 'Server error', `The server returned ${res.status}. Please try again later.`, 6000)
  }

  // Parse detailed error for the thrown Error
  const detail = await parseErrorDetail(res)
  throw new Error(detail)
}

/** Wrapper that calls fetchWithTimeout and intercepts common HTTP errors + network failures.
 *  On 401: attempts token refresh before retrying. Falls back to logout if refresh fails. */
export async function apiFetch(url: string, options: RequestInit = {}, _isRetry = false): Promise<Response> {
  let res: Response
  try {
    res = await fetchWithTimeout(url, options)
  } catch (err: unknown) {
    // Network error (offline, DNS, CORS, timeout)
    const notify = _getNotify()
    if (err instanceof Error && err.message.includes('timed out')) {
      notify.add('error', 'Request timeout', 'The server took too long to respond.', 5000)
    } else {
      notify.add('error', 'Connection lost', 'Cannot reach the server. Check your network.', 5000)
    }
    throw err
  }

  // Extract rate limit headers and update UI indicator
  // L-03 FIX: Lazy-load once instead of dynamic import on every response
  const rlLimit = res.headers.get('x-ratelimit-limit')
  const rlRemaining = res.headers.get('x-ratelimit-remaining')
  if (rlLimit && rlRemaining) {
    _getRateLimitUpdater().then(fn => fn(parseInt(rlLimit, 10), parseInt(rlRemaining, 10)))
  }

  // On 401: try to refresh token before giving up.
  // If refresh succeeds, retry the original request with the new token.
  // If refresh fails (no refresh token, server rejects), then logout.
  if (res.status === 401 && !_isRetry) {
    const hasRefreshToken = !!localStorage.getItem(REFRESH_TOKEN_KEY)
    if (hasRefreshToken) {
      devLog('[apiFetch] Got 401 — attempting token refresh')
      try {
        await refreshAccessToken()
        // Retry original request with fresh token
        const freshHeaders = { ...options.headers, ...getAuthHeaders() }
        return apiFetch(url, { ...options, headers: freshHeaders }, true)
      } catch {
        devLog('[apiFetch] Token refresh failed — logging out')
        // Fall through to handleResponseError which will logout
      }
    }
  }

  if (!res.ok) {
    await handleResponseError(res)
  }
  return res
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await apiFetch(`${API_URL}/api/v1${path}`, {
    headers: { ...getAuthHeaders() },
  })
  return res.json()
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${API_URL}/api/v1${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${API_URL}/api/v1${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await apiFetch(`${API_URL}/api/v1${path}`, {
    method: 'DELETE',
    headers: { ...getAuthHeaders() },
  })
  return res.json()
}
