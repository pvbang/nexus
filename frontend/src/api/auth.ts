// ============================================================
// JWT Auth Logic - Token management, refresh, cross-tab sync
// ============================================================

import { AUTH_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_ID_KEY } from './constants'
import { API_URL } from '../lib/constants'
import { useAuthStore } from '../stores/authStore'

/**
 * Check if the stored JWT token is expired by decoding the `exp` claim.
 * Returns true if token is missing, malformed, or past expiration.
 * Adds a 30-second buffer to avoid edge-case races near expiry.
 */
export function isTokenExpired(): boolean {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (!token) return true
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    if (!payload.exp) return false // No expiry claim = never expires
    const nowSec = Math.floor(Date.now() / 1000)
    return nowSec >= (payload.exp - 30) // 30s buffer
  } catch {
    return true // Malformed token = treat as expired
  }
}

export function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

// ── Token Refresh ─────────────────────────────────────────────
// Module-level promise prevents concurrent refresh calls across
// multiple 401 responses racing each other.
let _refreshPromise: Promise<string> | null = null

/**
 * Exchange the stored refresh token for a new access + refresh token pair.
 * Returns the new access token. Deduplicates concurrent calls.
 *
 * Throws if no refresh token exists or the refresh request fails
 * (caller should catch and logout).
 */
export async function refreshAccessToken(): Promise<string> {
  if (_refreshPromise) return _refreshPromise

  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
  if (!refreshToken) {
    throw new Error('No refresh token')
  }

  _refreshPromise = fetch(`${API_URL}/api/v1/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
    .then(async (res) => {
      if (!res.ok) throw new Error('Refresh failed')
      const data = await res.json()
      // Update stored tokens via authStore
      useAuthStore
        .getState()
        .setTokens(data.access_token, data.refresh_token, data.user_id)
      return data.access_token as string
    })
    .finally(() => {
      _refreshPromise = null
    })

  return _refreshPromise
}

export function getUserId(): string {
  // Try to extract from JWT token
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      if (payload.sub) return payload.sub
      if (payload.user_id) return payload.user_id
    } catch {
      // Invalid token format — fall through
    }
  }
  // Fallback: persistent anonymous ID per browser session
  let userId = localStorage.getItem(USER_ID_KEY)
  if (!userId) {
    userId = `user_${crypto.randomUUID()}`
    localStorage.setItem(USER_ID_KEY, userId)
  }
  return userId
}
