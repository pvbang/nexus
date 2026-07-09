// ============================================================
// MekongAI Nexus - Auth Store (Zustand)
// Manages authentication state, token storage, and logout.
// Supports refresh tokens and cross-tab sync via storage events.
// ============================================================

import { create } from 'zustand'

const AUTH_TOKEN_KEY = 'nexus_auth_token'
const REFRESH_TOKEN_KEY = 'nexus_refresh_token'
const USER_ID_KEY = 'nexus_user_id'

interface AuthState {
  /** Whether user has a valid token in localStorage */
  isAuthenticated: boolean
  /** Current user ID (from token or localStorage) */
  userId: string | null
  /** Check localStorage for existing token on app boot */
  initialize: () => void
  /** Clear token and mark as unauthenticated */
  logout: () => void
  /** Set token after successful login (legacy — backward compat) */
  setToken: (token: string, userId: string) => void
  /** Set both access + refresh tokens after login or refresh */
  setTokens: (accessToken: string, refreshToken: string | null, userId: string) => void
}

/** Schedule proactive token refresh 5 minutes before access token expiry. */
function scheduleTokenRefresh(accessToken: string) {
  try {
    const payload = JSON.parse(atob(accessToken.split('.')[1]))
    const expiresAt = payload.exp * 1000
    const refreshAt = expiresAt - 5 * 60 * 1000 // 5 min before expire
    const delay = refreshAt - Date.now()

    if (delay > 0) {
      // Clear previous timer if exists
      const existing = (window as any).__tokenRefreshTimer
      if (existing) clearTimeout(existing)

      ;(window as any).__tokenRefreshTimer = setTimeout(async () => {
        try {
          const { refreshAccessToken } = await import('../api')
          await refreshAccessToken()
        } catch {
          // Silent fail — will retry on next API call
        }
      }, delay)
    }
  } catch {
    /* ignore decode errors */
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  isAuthenticated: !!localStorage.getItem(AUTH_TOKEN_KEY),
  userId: localStorage.getItem(USER_ID_KEY),

  initialize: () => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    set({
      isAuthenticated: !!token,
      userId: localStorage.getItem(USER_ID_KEY),
    })
    // Schedule refresh if token exists
    if (token) scheduleTokenRefresh(token)
  },

  logout: () => {
    localStorage.removeItem(AUTH_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(USER_ID_KEY)
    // Clear proactive refresh timer
    const existing = (window as any).__tokenRefreshTimer
    if (existing) clearTimeout(existing)
    set({ isAuthenticated: false, userId: null })
  },

  setToken: (token: string, userId: string) => {
    localStorage.setItem(AUTH_TOKEN_KEY, token)
    localStorage.setItem(USER_ID_KEY, userId)
    set({ isAuthenticated: true, userId })
    scheduleTokenRefresh(token)
  },

  setTokens: (accessToken: string, refreshToken: string | null, userId: string) => {
    localStorage.setItem(AUTH_TOKEN_KEY, accessToken)
    localStorage.setItem(USER_ID_KEY, userId)
    if (refreshToken) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
    }
    set({ isAuthenticated: true, userId })
    scheduleTokenRefresh(accessToken)
  },
}))

// ── Cross-tab sync via storage events ────────────────────────
// When another tab logs out or refreshes the token, this tab picks up the change.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === AUTH_TOKEN_KEY) {
      if (!event.newValue) {
        // Logout from another tab
        useAuthStore.getState().logout()
      } else if (event.newValue !== event.oldValue) {
        // Token refreshed from another tab — reinitialize
        useAuthStore.getState().initialize()
      }
    }
  })
}
