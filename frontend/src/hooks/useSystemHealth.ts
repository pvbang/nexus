// ============================================================
// Nexus - System health polling hook
// Manages systemOnline, isOffline, isInitializing states
// Boot initialization + 30s periodic health check
// ============================================================

import { useState, useEffect } from 'react'
import { apiGet } from '@/api'
import { useSessionStore } from '@/stores/sessionStore'
import { usePreferenceStore } from '@/stores/preferenceStore'
import { registerBuiltinPlugins } from '@/plugins'

export interface SystemHealthState {
  systemOnline: boolean
  isOffline: boolean
  isInitializing: boolean
  retryConnection: () => void
}

export function useSystemHealth(): SystemHealthState {
  const [systemOnline, setSystemOnline] = useState(false)
  const [isOffline, setIsOffline] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)
  const fetchSessions = useSessionStore((s) => s.fetchSessions)

  // Boot: check system status + load sessions + apply theme + register plugins
  useEffect(() => {
    const init = async () => {
      try {
        // Apply dark/light theme on mount (sync — must be before render)
        const { applyTheme } = usePreferenceStore.getState()
        if (applyTheme) applyTheme()
        // Register built-in demo plugins
        registerBuiltinPlugins()
        // Check system status
        await apiGet('/system/info')
          .then(() => { setSystemOnline(true); setIsOffline(false) })
          .catch(() => { setSystemOnline(false); setIsOffline(true) })
        // Load sessions
        await fetchSessions()
      } catch {
        // Non-critical — app still boots
      } finally {
        setIsInitializing(false)
      }
    }
    init()
  }, [fetchSessions])

  // MISS-09: Periodic connection check + browser online/offline events
  useEffect(() => {
    const checkOnline = () => {
      apiGet('/system/info')
        .then(() => { setSystemOnline(true); setIsOffline(false) })
        .catch(() => { setSystemOnline(false); setIsOffline(true) })
    }

    const interval = setInterval(checkOnline, 30000) // Every 30s

    const onOnline = () => checkOnline()
    const onOffline = () => setIsOffline(true)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)

    return () => {
      clearInterval(interval)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [])

  const retryConnection = () => {
    apiGet('/system/info')
      .then(() => { setSystemOnline(true); setIsOffline(false) })
      .catch(() => { /* still offline */ })
  }

  return { systemOnline, isOffline, isInitializing, retryConnection }
}
