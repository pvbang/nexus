// ============================================================
// Nexus - WebSocket connection manager hook
// Auto-reconnect with exponential backoff, heartbeat detection,
// and tab-visibility awareness
// Batch 7: Bridge task_update events â†’ notificationCenterStore
// F5-Recovery: Route execution events â†’ processStore + executionStore
// ============================================================

import { useEffect, useRef, useCallback } from 'react'
import { createMonitorWebSocket, isTokenExpired, refreshAccessToken } from '../api'
import { useRealtimeStore } from '../stores/realtimeStore'
import { useNotificationCenterStore } from '../stores/notificationCenterStore'
import { useProcessStore } from '../stores/processStore'
import { useExecutionStore } from '../stores/executionStore'
import { useTabStore } from '../stores/tabStore'
import type {
  LiveAgentStatus,
  LiveTaskStatus,
  LiveSwarmStatus,
  SystemMetrics,
  LiveEvent,
} from '../stores/realtimeStore'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseWebSocketOptions {
  /** Whether the WebSocket connection is enabled (default: true) */
  enabled?: boolean
  /** Maximum number of reconnect attempts (default: 10) */
  reconnectAttempts?: number
  /** Base reconnect delay in ms â€” doubles each retry (default: 3000) */
  reconnectDelay?: number
  /** Disconnect and reconnect if no heartbeat within this ms (default: 45000) */
  heartbeatTimeout?: number
}

export interface UseWebSocketReturn {
  /** Whether the WebSocket is currently connected */
  connected: boolean
  /** Whether a reconnection attempt is in progress */
  reconnecting: boolean
  /** Manually disconnect the WebSocket */
  disconnect: () => void
  /** Manually trigger a reconnection */
  reconnect: () => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_OPTIONS: Required<UseWebSocketOptions> = {
  enabled: true,
  reconnectAttempts: 10,
  reconnectDelay: 3000,
  heartbeatTimeout: 120_000, // 120s — generous margin over server's 30s heartbeat interval
}

/** Maximum reconnect delay cap in ms */
const MAX_RECONNECT_DELAY = 30_000


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a human-readable message from a WebSocket event payload */
function buildEventMessage(type: string, data: Record<string, unknown>): string {
  switch (type) {
    case 'agent_status':
      return `Agent "${data.name ?? data.agent_id}" â†’ ${data.status}`
    case 'task_update':
      return `Task "${data.title ?? data.task_id}" â†’ ${data.status}`
    case 'swarm_event':
      return `Swarm "${data.name ?? data.swarm_id}" â†’ ${data.status}`
    case 'system_metric':
      return `System: CPU ${data.cpu}%, Memory ${data.memory}%`
    case 'heartbeat':
      return 'Heartbeat'
    case 'agent_thinking':
      return `${data.agent_name ?? 'Agent'} thinking`
    case 'tool_start':
      return `${data.agent_name ?? 'Agent'} â†’ ${data.tool}`
    case 'tool_done':
      return `${data.agent_name ?? 'Agent'} â† ${data.tool}`
    case 'agent_spawn':
      return `Spawned: ${data.agent_name} (${data.agent_role})`
    default:
      return `Event: ${type}`
  }
}


// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useWebSocket(options?: UseWebSocketOptions): UseWebSocketReturn {
  const opts = { ...DEFAULT_OPTIONS, ...options }

  // Store actions â€” stable references via getState()
  const wsRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const heartbeatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const manualDisconnectRef = useRef(false)
  const pausedRef = useRef(false)

  // Read reactive state from store
  const connected = useRealtimeStore((s) => s.connected)
  const reconnecting = useRealtimeStore((s) => s.reconnecting)

  // -----------------------------------------------------------------------
  // Heartbeat timeout â€” if no heartbeat for `heartbeatTimeout` ms, reconnect
  // -----------------------------------------------------------------------
  const resetHeartbeatTimer = useCallback(() => {
    if (heartbeatTimerRef.current) clearTimeout(heartbeatTimerRef.current)
    heartbeatTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return
      console.warn('[useWebSocket] Heartbeat timeout â€” reconnecting')
      closeSocket()
      scheduleReconnect()
    }, opts.heartbeatTimeout)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.heartbeatTimeout])

  // -----------------------------------------------------------------------
  // Dispatch incoming event to the realtime store + execution stores
  // -----------------------------------------------------------------------
  const dispatchEvent = useCallback((type: string, data: Record<string, unknown>) => {
    const store = useRealtimeStore.getState()

    // Batch 7: notification bridge helper
    const notifyStore = useNotificationCenterStore.getState()

    // All execution event routing is now handled by Chat WS (useResilientChat).
    // Monitor WS only updates tab processing indicators for task_started/completed/failed.
    if (type === 'task_started' || type === 'task_completed' || type === 'task_failed') {
      const eventSessionId = (data.session_id as string) || (data.conversation_id as string) || ''
      if (eventSessionId) {
        // Update tab processing indicator
        try {
          const tabState = useTabStore.getState()
          const tab = tabState.getTabBySessionId(eventSessionId)
          if (tab) {
            if (type === 'task_started') {
              tabState.updateTabProcessing(tab.id, true)
            } else {
              tabState.updateTabProcessing(tab.id, false)
              tabState.setTabUnread(tab.id, true)
              try {
                sessionStorage.setItem(`nexus_tab_needs_refresh_${eventSessionId}`, '1')
              } catch { /* sessionStorage full */ }
            }
          }
        } catch { /* tab store not ready */ }

        // Update executionStore active flag for background sessions
        const es = useExecutionStore.getState()
        if (type === 'task_started') {
          es.setActive(eventSessionId, true)
        } else {
          es.setActive(eventSessionId, false)
        }
      }
    }

    switch (type) {
      case 'agent_status':
        store.updateAgentStatus({
          agent_id: data.agent_id as string,
          name: (data.name as string) ?? '',
          status: (data.status as string) ?? 'unknown',
          role: (data.role as string) ?? '',
          last_activity: data.last_activity as string | undefined,
          timestamp: (data.timestamp as number) ?? Date.now(),
        } satisfies LiveAgentStatus)
        break

      case 'task_update': {
        const taskTitle = (data.title as string) ?? (data.task_id as string) ?? 'Unknown'
        const taskStatus = (data.status as string) ?? 'unknown'
        const taskId = (data.task_id as string) ?? ''

        store.updateTaskStatus({
          task_id: taskId,
          title: (data.title as string) ?? '',
          status: taskStatus,
          progress: data.progress as number | undefined,
          timestamp: (data.timestamp as number) ?? Date.now(),
        } satisfies LiveTaskStatus)

        // Bridge completed/failed to notification center
        if (taskStatus === 'completed') {
          notifyStore.addNotification({
            type: 'task_complete',
            title: `Task completed: ${taskTitle}`,
            description: `Task "${taskTitle}" finished successfully.`,
            link: `/tasks/${taskId}`,
          })
        } else if (taskStatus === 'failed') {
          notifyStore.addNotification({
            type: 'task_failed',
            title: `Task failed: ${taskTitle}`,
            description: `Task "${taskTitle}" encountered an error.`,
            link: `/tasks/${taskId}`,
          })
        }
        break
      }

      case 'swarm_event': {
        store.updateSwarmStatus({
          swarm_id: data.swarm_id as string,
          name: (data.name as string) ?? '',
          status: (data.status as string) ?? 'unknown',
          strategy: (data.strategy as string) ?? '',
          round: data.round as number | undefined,
          total_rounds: data.total_rounds as number | undefined,
          timestamp: (data.timestamp as number) ?? Date.now(),
        } satisfies LiveSwarmStatus)

        // Bridge swarm completion to notification center
        const swarmStatus = (data.status as string) ?? 'unknown'
        if (swarmStatus === 'completed' || swarmStatus === 'done') {
          notifyStore.addNotification({
            type: 'swarm_done',
            title: `Swarm completed: ${(data.name as string) || (data.swarm_id as string) || 'Unknown'}`,
            description: `Strategy: ${(data.strategy as string) || 'N/A'}`,
            link: `/swarms`,
          })
        }
        break
      }

      case 'system_metric':
        store.updateMetrics({
          cpu: (data.cpu as number) ?? 0,
          memory: (data.memory as number) ?? 0,
          active_tasks: (data.active_tasks as number) ?? 0,
          active_agents: (data.active_agents as number) ?? 0,
          active_swarms: (data.active_swarms as number) ?? 0,
          queue_size: (data.queue_size as number) ?? 0,
          cache_hit_rate: (data.cache_hit_rate as number) ?? 0,
          uptime_seconds: (data.uptime_seconds as number) ?? 0,
          timestamp: (data.timestamp as number) ?? Date.now(),
        } satisfies SystemMetrics)
        break

      case 'heartbeat':
        store.setHeartbeat((data.timestamp as number) ?? Date.now())
        break

      case 'pong':
        // Reset heartbeat timeout â€” server responded to our ping
        // No store update needed; resetHeartbeatTimer() in onmessage handles it
        break

      default:
        // Execution events are already handled above via routeExecutionEvent
        // Unknown non-execution events are still recorded below
        break
    }

    // All events (including heartbeat) are added to recentEvents
    const event: LiveEvent = {
      id: crypto.randomUUID(),
      type,
      message: buildEventMessage(type, data),
      timestamp: (data.timestamp as number) ?? Date.now(),
      data,
    }
    store.addEvent(event)
  }, [])

  // -----------------------------------------------------------------------
  // Socket lifecycle
  // -----------------------------------------------------------------------
  const closeSocket = useCallback(() => {
    if (heartbeatTimerRef.current) {
      clearTimeout(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onmessage = null
      wsRef.current.onerror = null
      wsRef.current.onclose = null
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close()
      }
      wsRef.current = null
    }
  }, [])

  const scheduleReconnect = useCallback(() => {
    if (manualDisconnectRef.current || !mountedRef.current) return
    if (attemptRef.current >= opts.reconnectAttempts) {
      console.warn('[useWebSocket] Max reconnect attempts reached')
      useRealtimeStore.getState().setReconnecting(false)
      return
    }

    useRealtimeStore.getState().setReconnecting(true)

    const delay = Math.min(
      opts.reconnectDelay * Math.pow(2, attemptRef.current),
      MAX_RECONNECT_DELAY,
    )
    console.info(`[useWebSocket] Reconnecting in ${delay}ms (attempt ${attemptRef.current + 1}/${opts.reconnectAttempts})`)

    reconnectTimerRef.current = setTimeout(() => {
      if (!mountedRef.current || manualDisconnectRef.current) return
      attemptRef.current += 1
      connect()
    }, delay)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.reconnectAttempts, opts.reconnectDelay])

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    closeSocket()
    manualDisconnectRef.current = false

    // Ensure token is fresh before opening Monitor WS.
    const openWs = () => {
      if (!mountedRef.current) return
      try {
        const ws = createMonitorWebSocket()
        wsRef.current = ws

      ws.onopen = () => {
        if (!mountedRef.current) return
        console.info('[useWebSocket] Connected to monitor WebSocket')
        attemptRef.current = 0
        const store = useRealtimeStore.getState()
        store.setConnected(true)
        store.setReconnecting(false)
        resetHeartbeatTimer()

        setTimeout(async () => {
          try {
            const tabState = useTabStore.getState()
            const activeTab = tabState.tabs.find((t: { isActive: boolean }) => t.isActive)
            if (activeTab?.sessionId) {
              const { API_URL } = await import('../lib/constants')
              // FIX: Include auth header to avoid 401 when AUTH_ENABLED=true
              const AUTH_TOKEN_KEY = 'nexus_auth_token'
              const token = localStorage.getItem(AUTH_TOKEN_KEY)
              const headers: HeadersInit = token ? { 'Authorization': `Bearer ${token}` } : {}
              const res = await fetch(`${API_URL}/api/v1/sessions/${activeTab.sessionId}/state`, { headers })
              if (res.ok) {
                const state = await res.json()
                const ps = useProcessStore.getState()
                // Always set the session so Monitor WS can route events
                if (!ps.currentSessionId && activeTab.sessionId) {
                  ps.setSession(activeTab.sessionId)
                }
                if (state.is_running && state.task_id) {
                  ps.startOrResumeProcess(state.task_id, activeTab.sessionId)
                  const es = useExecutionStore.getState()
                  es.setActive(activeTab.sessionId, true)
                }
              }
            }
          } catch {
            // Best-effort â€” Chat WS handles primary delivery
          }
        }, 500)
      }

      ws.onmessage = (event: MessageEvent) => {
        if (!mountedRef.current) return
        // Any message from server proves connection is alive — reset heartbeat
        resetHeartbeatTimer()
        try {
          const parsed = JSON.parse(event.data as string) as Record<string, unknown>
          // Support both formats:
          // 1. Transformed: {"type": "system_metric", "data": {"cpu": 45, ...}}
          // 2. Legacy flat: {"event_type": "system_metric", "cpu": 45, ...}
          const eventType = (parsed.type as string) || (parsed.event_type as string)
          if (eventType) {
            // Use nested data if present, otherwise extract flat payload
            const eventData = (parsed.data as Record<string, unknown>)
              || Object.fromEntries(
                Object.entries(parsed).filter(([k]) => k !== 'type' && k !== 'event_type'),
              )
            dispatchEvent(eventType, eventData)
          }
        } catch (err) {
          console.warn('[useWebSocket] Failed to parse message:', err)
        }
      }

      ws.onerror = (event: Event) => {
        console.error('[useWebSocket] WebSocket error:', event)
      }

      ws.onclose = () => {
        if (!mountedRef.current) return
        console.info('[useWebSocket] Connection closed')
        const store = useRealtimeStore.getState()
        store.setConnected(false)
        if (!manualDisconnectRef.current && !pausedRef.current) {
          scheduleReconnect()
        }
      }
      } catch (err) {
        console.error('[useWebSocket] Failed to create WebSocket:', err)
        scheduleReconnect()
      }
    }

    // If token is expired, try refreshing before connecting
    if (isTokenExpired()) {
      refreshAccessToken().catch(() => {}).finally(openWs)
    } else {
      openWs()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [closeSocket, dispatchEvent, resetHeartbeatTimer, scheduleReconnect])

  // -----------------------------------------------------------------------
  // Visibility change â€” pause/resume
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!opts.enabled) return

    const handleVisibilityChange = () => {
      if (document.hidden) {
        // Keep connection alive so background task events still arrive.
        // Only pause heartbeat timer to avoid spurious reconnects.
        pausedRef.current = true
        if (heartbeatTimerRef.current) {
          clearTimeout(heartbeatTimerRef.current)
          heartbeatTimerRef.current = null
        }
      } else {
        // Tab visible â†’ resume heartbeat monitoring
        pausedRef.current = false
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          // WS is still connected â€” just resume heartbeat timer
          resetHeartbeatTimer()
        } else if (!manualDisconnectRef.current) {
          // WS was closed (server-side or network) â€” reconnect
          attemptRef.current = 0
          connect()
        }

        // Even though WS stays connected, metrics may have been missed during
        // periods of server load. Refetch for consistency.
        setTimeout(async () => {
          try {
            const { API_URL } = await import('../lib/constants')
            const res = await fetch(`${API_URL}/api/v1/system/stats`)
            if (res.ok) {
              const stats = await res.json()
              const store = useRealtimeStore.getState()
              if (stats.active_tasks !== undefined) {
                store.updateMetrics({
                  cpu: stats.cpu ?? 0,
                  memory: stats.memory ?? 0,
                  active_tasks: stats.active_tasks ?? 0,
                  active_agents: stats.active_agents ?? 0,
                  active_swarms: stats.active_swarms ?? 0,
                  queue_size: stats.queue_size ?? 0,
                  cache_hit_rate: stats.cache_hit_rate ?? 0,
                  uptime_seconds: stats.uptime_seconds ?? 0,
                  timestamp: Date.now(),
                })
              }
            }
          } catch {
          }
        }, 1000) // Delay 1s to let WS establish first
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [opts.enabled, closeSocket, connect, resetHeartbeatTimer])

  // -----------------------------------------------------------------------
  // Main effect â€” connect on mount, cleanup on unmount
  // -----------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true
    manualDisconnectRef.current = false

    if (opts.enabled && !document.hidden) {
      connect()
    }

    return () => {
      mountedRef.current = false
      closeSocket()
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      // Don't call reset() — other consumers may still need realtime data
      useRealtimeStore.getState().setConnected(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.enabled])

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------
  const disconnect = useCallback(() => {
    manualDisconnectRef.current = true
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    closeSocket()
    const store = useRealtimeStore.getState()
    store.setConnected(false)
    store.setReconnecting(false)
  }, [closeSocket])

  const reconnect = useCallback(() => {
    manualDisconnectRef.current = false
    attemptRef.current = 0
    connect()
  }, [connect])

  return { connected, reconnecting, disconnect, reconnect }
}
