// ============================================================
// System/Health/Analytics + File Operations + WebSocket + Decisions
// ============================================================

import { apiGet, apiPost } from './client'
import { getAuthHeaders, refreshAccessToken } from './auth'
import { AUTH_TOKEN_KEY, REFRESH_TOKEN_KEY } from './constants'
import { API_URL, WS_URL } from '../lib/constants'
import { useAuthStore } from '../stores/authStore'
import { useNotificationStore } from '../stores/notificationStore'
import type { UploadResult, AnalyticsData } from './types'

function _getNotify() { return useNotificationStore.getState() }

// ============================================================
// Authenticated File Download
// ============================================================

/**
 * Download a file with proper Bearer token authentication.
 * Uses fetch + blob approach instead of window.open (which cannot send auth headers).
 */
export async function downloadWithAuth(url: string, filename?: string): Promise<void> {
  let res = await fetch(url, {
    headers: { ...getAuthHeaders() },
  })
  if (!res.ok) {
    if (res.status === 401) {
      // Try refresh token before logging out
      const hasRefreshToken = !!localStorage.getItem(REFRESH_TOKEN_KEY)
      if (hasRefreshToken) {
        try {
          await refreshAccessToken()
          res = await fetch(url, { headers: { ...getAuthHeaders() } })
          if (res.ok) {
            // Retry succeeded — fall through to blob download below
          } else if (res.status === 401) {
            useAuthStore.getState().logout()
            const notify = _getNotify()
            notify.add('error', 'Session expired', 'Please log in again.', 8000)
            return
          } else {
            const notify = _getNotify()
            notify.add('error', 'Download failed', `Server returned ${res.status}`, 5000)
            return
          }
        } catch {
          useAuthStore.getState().logout()
          const notify = _getNotify()
          notify.add('error', 'Session expired', 'Please log in again.', 8000)
          return
        }
      } else {
        useAuthStore.getState().logout()
        const notify = _getNotify()
        notify.add('error', 'Session expired', 'Please log in again.', 8000)
        return
      }
    }
    const notify = _getNotify()
    notify.add('error', 'Download failed', `Server returned ${res.status}`, 5000)
    return
  }
  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = blobUrl
  // Derive filename from Content-Disposition header or URL
  if (!filename) {
    const disposition = res.headers.get('Content-Disposition')
    if (disposition) {
      const match = disposition.match(/filename="?([^";\n]+)"?/)
      if (match) filename = match[1]
    }
    if (!filename) {
      filename = url.split('/').pop() || 'download'
    }
  }
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(blobUrl)
}

// ============================================================
// File Upload
// ============================================================

export async function uploadAttachments(
  files: File[],
  conversationId?: string,
): Promise<UploadResult> {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  // Stage uploads under the conversation so the backend can materialize them
  // into the task workspace (agent can re-open originals anytime).
  if (conversationId) {
    formData.append('conversation_id', conversationId)
  }
  let res = await fetch(`${API_URL}/api/v1/attachments/upload`, {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
  })
  if (res.status === 401) {
    // Try refresh token and retry once
    const hasRefreshToken = !!localStorage.getItem(REFRESH_TOKEN_KEY)
    if (hasRefreshToken) {
      try {
        await refreshAccessToken()
        res = await fetch(`${API_URL}/api/v1/attachments/upload`, {
          method: 'POST',
          headers: { ...getAuthHeaders() },
          body: formData,
        })
      } catch {
        throw new Error(`Upload error: 401 (token refresh failed)`)
      }
    }
  }
  if (!res.ok) throw new Error(`Upload error: ${res.status}`)
  return res.json()
}

// ============================================================
// WebSocket
// ============================================================

export function createMonitorWebSocket(): WebSocket {
  // SEC-WS01: Pass JWT token via query param for WebSocket auth
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  const url = token
    ? `${WS_URL}/ws/monitor?token=${encodeURIComponent(token)}`
    : `${WS_URL}/ws/monitor`
  return new WebSocket(url)
}

// ============================================================
// Interactive Decisions API
// ============================================================

/** Submit user answer to a pending decision */
export async function answerDecision(decisionId: string, value: string): Promise<{ status: string }> {
  return apiPost(`/decisions/${decisionId}/answer`, { value })
}

// ============================================================
// Analytics API
// ============================================================

/** Fetch analytics data for a given period */
export function getAnalytics(period?: string): Promise<AnalyticsData> {
  return apiGet<AnalyticsData>(`/analytics/?period=${period || '7d'}`)
}
