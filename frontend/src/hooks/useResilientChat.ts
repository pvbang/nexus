// ============================================================
// MekongAI Nexus - Session Resume & Resilient Chat WebSocket
//
//
// ============================================================

import { useEffect, useRef, useCallback, useState } from 'react'
import { API_URL, WS_URL } from '../lib/constants'
import { setWsActiveSession, removeWsActiveSession, setWsConnectingSession, removeWsConnectingSession } from '../stores/executionStore'
import { useExecutionStore } from '../stores/executionStore'
import { isTokenExpired, refreshAccessToken } from '../api'

function getTabId(): string {
  let tabId = sessionStorage.getItem('nexus_tab_id')
  if (!tabId) {
    tabId = `tab-${crypto.randomUUID().slice(0, 8)}`
    sessionStorage.setItem('nexus_tab_id', tabId)
  }
  return tabId
}

function getWsUserId(): string {
  const AUTH_TOKEN_KEY = 'nexus_auth_token'
  const USER_ID_KEY = 'nexus_user_id'
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      if (payload.sub) return payload.sub
      if (payload.user_id) return payload.user_id
    } catch { /* invalid token */ }
  }
  let userId = localStorage.getItem(USER_ID_KEY)
  if (!userId) {
    userId = `user_${crypto.randomUUID()}`
    localStorage.setItem(USER_ID_KEY, userId)
  }
  return userId
}

// ─── Types ───────────────────────────────────────────────────

export interface SessionState {
  session_id: string
  is_running: boolean
  task_id: string | null
  task_status: string | null
  started_at: string | null
  last_activity: string | null
  last_activity_desc: string | null
  current_tool: string | null
  last_event_sequence: number
  message_count: number
  error?: string | null
}

export interface TaskEvent {
  id: string
  task_id: string
  event_type: string
  data: Record<string, unknown>
  sequence: number
  created_at: string
}

export interface ChatWsMessage {
  type: string
  [key: string]: unknown
}

export interface UseResilientChatOptions {
  /** Session/conversation ID */
  sessionId: string | null
  /** Called for each event (live or replayed) */
  onEvent?: (event: ChatWsMessage) => void
  /** Called when session state is loaded (on mount/reconnect) */
  onSessionState?: (state: SessionState) => void
  /** Called when connection status changes */
  onConnectionChange?: (connected: boolean) => void
  /** Max reconnect attempts (default: Infinity) */
  maxReconnectAttempts?: number
  /** User ID for messages */
  userId?: string
}

export interface UseResilientChatReturn {
  /** Whether WS is connected */
  connected: boolean
  /** Whether a task is running for this session */
  taskRunning: boolean
  /** Current task activity description */
  taskActivity: string | null
  /** Send a chat message */
  sendMessage: (message: string, streaming?: boolean) => void
  /** Cancel the running task */
  cancelTask: () => void
  /** Force reconnect */
  reconnect: () => void
}

// ─── Constants ───────────────────────────────────────────────

const LAST_SEQ_KEY_PREFIX = 'nexus_last_seq_'
const MAX_RECONNECT_DELAY = 30_000
const BASE_RECONNECT_DELAY = 1_000

// ─── Hook ────────────────────────────────────────────────────

export function useResilientChat(options: UseResilientChatOptions): UseResilientChatReturn {
  const {
    sessionId,
    onEvent,
    onSessionState,
    onConnectionChange,
    maxReconnectAttempts = Infinity,
    userId = 'default_user',
  } = options

  const [connected, setConnected] = useState(false)
  const [taskRunning, setTaskRunning] = useState(false)
  const [taskActivity, setTaskActivity] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const lastSeqRef = useRef<number>(0)
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const sessionIdRef = useRef(sessionId)
  const currentTaskIdRef = useRef<string>('')
  const backgroundCheckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent
  const onSessionStateRef = useRef(onSessionState)
  onSessionStateRef.current = onSessionState
  const onConnectionChangeRef = useRef(onConnectionChange)
  onConnectionChangeRef.current = onConnectionChange

  // Keep sessionId ref in sync
  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  // ─── Helpers ───────────────────────────────────────────────

  const getStoredSeq = useCallback((): number => {
    if (!sessionId) return 0
    const stored = localStorage.getItem(`${LAST_SEQ_KEY_PREFIX}${sessionId}`)
    return stored ? parseInt(stored, 10) : 0
  }, [sessionId])

  const storeSeq = useCallback((seq: number) => {
    if (!sessionId) return
    // HIGH-01 FIX: Sequences are per-task and restart at 0 for new tasks.
    // If incoming seq is much lower than lastSeqRef (gap > 10), it indicates
    // a new task started and we missed the task_started event that normally
    // resets lastSeqRef. Auto-reset to accept the new task's events.
    if (seq > lastSeqRef.current) {
      lastSeqRef.current = seq
    } else if (lastSeqRef.current - seq > 10) {
      // New task detected via sequence gap — reset tracker
      lastSeqRef.current = seq
    }
    // For seq < lastSeq with gap <= 10: still allow processing (out-of-order delivery)
    // Always persist to track progress correctly
    if (seq % 3 === 0 || seq === 0 || seq === lastSeqRef.current) {
      localStorage.setItem(`${LAST_SEQ_KEY_PREFIX}${sessionId}`, String(lastSeqRef.current))
    }
  }, [sessionId])

  // ─── Session State Fetch ───────────────────────────────────

  /** Build auth headers for API calls (SEC-SESSION-01 compliance) */
  const getAuthHeaders = useCallback((): HeadersInit => {
    const AUTH_TOKEN_KEY = 'nexus_auth_token'
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    if (token) {
      return { 'Authorization': `Bearer ${token}` }
    }
    return {}
  }, [])

  const fetchSessionState = useCallback(async (): Promise<SessionState | null> => {
    if (!sessionId) return null
    try {
      const res = await fetch(`${API_URL}/api/v1/sessions/${sessionId}/state`, {
        headers: getAuthHeaders(),
      })
      if (!res.ok) return null
      const state: SessionState = await res.json()
      onSessionStateRef.current?.(state)
      setTaskRunning(state.is_running)
      setTaskActivity(state.last_activity_desc)
      return state
    } catch (err) {
      console.warn('[useResilientChat] Failed to fetch session state:', err)
      return null
    }
  }, [sessionId, getAuthHeaders])

  // ─── Event Catch-up ────────────────────────────────────────

  const catchUpEvents = useCallback(async (afterSeq: number) => {
    if (!sessionId) return
    try {
      const res = await fetch(
        `${API_URL}/api/v1/sessions/${sessionId}/events?after_seq=${afterSeq}&limit=200`,
        { headers: getAuthHeaders() }
      )
      if (!res.ok) return
      const data = await res.json()
      const events: TaskEvent[] = data.events || []

      if (events.length === 0) return

      // PERF: Defer event replay to after initial UI render to avoid blocking paint.
      // Uses requestIdleCallback (with setTimeout fallback) so caught-up events
      // are processed during idle time instead of competing with first render.
      const processEvents = () => {
        for (const event of events) {
          onEventRef.current?.({
            type: 'event',
            event_type: event.event_type,
            data: event.data,
            sequence: event.sequence,
            // Priority: data.timestamp (source time) > created_at (DB insert time)
            timestamp: (event.data as Record<string, unknown>)?.timestamp || event.created_at,
            replayed: true, // Flag so UI can handle differently if needed
          })
          storeSeq(event.sequence)
        }
        console.info(`[useResilientChat] Caught up ${events.length} events (seq ${afterSeq} → ${lastSeqRef.current})`)
      }

      if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(processEvents, { timeout: 2000 })
      } else {
        setTimeout(processEvents, 0)
      }
    } catch (err) {
      console.warn('[useResilientChat] Event catch-up failed:', err)
    }
  }, [sessionId, storeSeq, getAuthHeaders])

  // ─── WebSocket Connection ──────────────────────────────────

  const connectWs = useCallback(() => {
    if (!sessionId || !mountedRef.current) return

    // Clean up existing connection
    if (wsRef.current) {
      // Clean up active session BEFORE closing to prevent stale _wsActiveSessions
      if (sessionIdRef.current) {
        removeWsActiveSession(sessionIdRef.current)
      }
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }

    const AUTH_TOKEN_KEY = 'nexus_auth_token'

    // Ensure token is fresh before connecting WS.
    // If token is expired, try to refresh it before opening the connection.
    const ensureFreshTokenAndConnect = async () => {
      if (isTokenExpired()) {
        try {
          await refreshAccessToken()
        } catch {
          // Refresh failed — proceed with whatever token exists (or none)
        }
      }

      if (!mountedRef.current) return
      const token = localStorage.getItem(AUTH_TOKEN_KEY)

      try {
        const wsParams = new URLSearchParams({
          tab_id: getTabId(),
          user_id: (userId && userId !== 'default_user') ? userId : getWsUserId(),
        })

        // SEC-WS02: Pass JWT token for authenticated WebSocket connections.
        if (token) {
          wsParams.set('token', token)
        }

      // This tells Monitor WS to skip routing events for this session during the
      // narrow window between WS creation and onopen (when setWsActiveSession fires).
      setWsConnectingSession(sessionId)

      const ws = new WebSocket(`${WS_URL}/ws/chat/${sessionId}?${wsParams}`)
      wsRef.current = ws

      ws.onopen = async () => {
        if (!mountedRef.current) return
        console.info('[useResilientChat] Connected')
        reconnectAttemptRef.current = 0
        setConnected(true)
        onConnectionChangeRef.current?.(true)

        // Order matters: clear connecting THEN set active (no gap).
        if (sessionId) removeWsConnectingSession(sessionId)
        // Mark this session as having active WS delivery
        // so Monitor WS (useWebSocket) skips duplicate event routing.
        setWsActiveSession(sessionId)

        // On connect (including reconnect): resume session
        const state = await fetchSessionState()

        // Previous order (catchUp → subscribe) left a gap where events emitted
        // AFTER catchUp query but BEFORE subscribe were lost.
        // New order: subscribe immediately → then replay missed events.
        // Dedup in executionStore handles any overlap between live + replayed events.
        if (state?.is_running) {
          ws.send(JSON.stringify({ type: 'subscribe' }))
        }

        // Catch up any missed events (dedup handles overlap with live events)
        const storedSeq = getStoredSeq()
        const serverSeq = state?.last_event_sequence ?? 0
        if (serverSeq > storedSeq) {
          await catchUpEvents(storedSeq)
        }
      }

      ws.onmessage = (event: MessageEvent) => {
        if (!mountedRef.current) return
        if (sessionIdRef.current !== sessionId) return
        try {
          const msg: ChatWsMessage = JSON.parse(event.data as string)
          const msgType = msg.type as string

          // Server heartbeat to keep proxy connection alive — ignore silently
          if (msgType === 'heartbeat' || msgType === 'pong') {
            return
          }

          // Track sequence for catch-up
          if (msg.sequence && typeof msg.sequence === 'number') {
            storeSeq(msg.sequence as number)
          }


          // Server sends replayed events with type="event_replay" wrapper.
          // Unwrap to regular event format for uniform downstream handling.
          if (msgType === 'event_replay') {
            const unwrapped: ChatWsMessage = {
              type: 'event',
              event_type: msg.event_type as string,
              data: msg.data,
              sequence: msg.sequence,
              replayed: true,
            }
            if (unwrapped.sequence && typeof unwrapped.sequence === 'number') {
              storeSeq(unwrapped.sequence as number)
            }
            // Update task activity from replayed events
            const replayedType = msg.event_type as string
            if (replayedType === 'task_completed' || replayedType === 'task_failed') {
              setTaskRunning(false)
              setTaskActivity(null)
            }
            onEventRef.current?.(unwrapped)
            return
          }

          // {type: "session_event", event: "task_started", task_id: "..."}.
          // Auto-subscribe so this tab also receives real-time events.
          if (msgType === 'session_event') {
            const sessionEvent = msg.event as string
            if (sessionEvent === 'task_started' && wsRef.current?.readyState === WebSocket.OPEN) {
              const newTaskId = (msg.task_id as string) || ''
              // Only clear if it's actually a DIFFERENT task
              if (newTaskId && newTaskId !== currentTaskIdRef.current) {
                if (sessionIdRef.current) {
                  useExecutionStore.getState().clearSession(sessionIdRef.current)
                }
                currentTaskIdRef.current = newTaskId
                // Auto-subscribe to the new task's events
                wsRef.current.send(JSON.stringify({ type: 'subscribe' }))
                wsRef.current.send(JSON.stringify({ type: 'replay', since_sequence: 0 }))
                setTaskRunning(true)
                setTaskActivity('starting')
                // Reset lastSeqRef since we cleared the session
                lastSeqRef.current = 0
              }
            } else if (sessionEvent === 'interrupt') {
              // Another tab interrupted — update local state
              setTaskActivity('interrupted')
            }
            // Forward to consumer for any additional handling
            onEventRef.current?.(msg)
            return
          }

          // Update task status from events
          if (msgType === 'task_started' || msgType === 'task_resumed') {
            setTaskRunning(true)
            setTaskActivity(msgType === 'task_resumed' ? 'resuming' : 'starting')
            const newTaskId = (msg.task_id as string) || ''
            currentTaskIdRef.current = newTaskId
            // Sequences are PER-TASK (restart at 0). If lastSeqRef.current is 50
            // from a previous task, new events (seq 0,1,2...) would be skipped
            // by the storeSeq guard (seq > lastSeqRef.current). Reset here.
            lastSeqRef.current = 0
            if (sessionIdRef.current) {
              useExecutionStore.getState().clearSession(sessionIdRef.current)
            }
          } else if (msgType === 'stream_token') {
            setTaskActivity('streaming')
          } else if (msgType === 'event') {
            const eventType = msg.event_type as string
            if (eventType === 'task_completed' || eventType === 'task_failed' || eventType === 'task_cancelled') {
              // Guard: only accept terminal events for the CURRENT task.
              // Stale terminal events from previous tasks must not kill the current task's UI.
              const eventTaskId = (msg.task_id as string) || ((msg.data as Record<string, unknown>)?.task_id as string) || ''
              if (!eventTaskId || eventTaskId === currentTaskIdRef.current) {
                setTaskRunning(false)
                setTaskActivity(null)
              }
            } else {
              setTaskActivity(eventType)
            }
          } else if (msgType === 'stream_end' || msgType === 'complete' || msgType === 'done') {
            // Normalize final content from all completion event variants:
            // SSE sends {type:"done", content}, WS streaming sends {type:"stream_end", full_content},
            // WS non-streaming sends {type:"complete", message}.
            const finalContent = (msg.full_content || msg.content || msg.message || undefined) as string | undefined
            if (finalContent) {
              // Attach normalized field so consumers handle all 3 uniformly
              msg._finalContent = finalContent
            }

            // BUG-19 FIX: Distinguish terminal completion from background continuation.
            // stream_end / complete → task is DONE (WS received task_completed data)
            // done with status='background' → task continues in background (SSE timeout)
            // done with other status → task is DONE
            if (msgType === 'stream_end' || msgType === 'complete') {
              // WS terminal events — task completed on server
              setTaskRunning(false)
              setTaskActivity(null)
            } else if (msgType === 'done') {
              const doneStatus = msg.status as string | undefined
              if (doneStatus === 'background') {
                // SSE stream timed out but task still running on backend
                setTaskActivity('background')
                // Start background check — poll session_state after 60s
                if (backgroundCheckTimerRef.current) clearTimeout(backgroundCheckTimerRef.current)
                backgroundCheckTimerRef.current = setTimeout(async () => {
                  if (!mountedRef.current || !sessionIdRef.current) return
                  const state = await fetchSessionState()
                  if (state && !state.is_running) {
                    setTaskRunning(false)
                    setTaskActivity(null)
                  }
                }, 60_000)
              } else {
                // done with status != 'background' → task finished
                setTaskRunning(false)
                setTaskActivity(null)
              }
            }
          } else if (msgType === 'session_state') {
            // Server notifies us of running task on connect
            setTaskRunning(msg.status === 'running')
            setTaskActivity(msg.last_activity as string | null)
            // Reset here so new events from the running task are accepted.
            if (msg.status === 'running') {
              lastSeqRef.current = 0
              if (sessionId) {
                localStorage.setItem(`${LAST_SEQ_KEY_PREFIX}${sessionId}`, '0')
              }
            }
          } else if (msgType === 'status') {
            const msgStatus = msg.status as string
            if (msgStatus === 'cancelled' || msgStatus === 'no_task') {
              setTaskRunning(false)
              setTaskActivity(null)
            }
          } else if (msgType === 'error') {
            // Error with TASK_BUSY code means task is still running — don't change state.
            // Other errors don't affect task running state.
            // Just forward to consumer for display.
          }

          // Forward to consumer
          onEventRef.current?.(msg)
        } catch (err) {
          console.warn('[useResilientChat] Failed to parse message:', err)
        }
      }

      ws.onclose = (ev: CloseEvent) => {
        if (!mountedRef.current) return
        setConnected(false)
        onConnectionChangeRef.current?.(false)

        // Other sessions with active Chat WS in other tabs remain tracked.
        if (sessionId) removeWsActiveSession(sessionId)
        // Also clear connecting state for this session (if WS failed before onopen)
        if (sessionId) removeWsConnectingSession(sessionId)

        // Don't reconnect on auth rejection — prevents infinite loop
        if (ev.code === 4001 || ev.code === 4003) {
          console.warn('[useResilientChat] WS auth rejected, stopping reconnect')
          return
        }

        // Auto-reconnect with exponential backoff
        if (reconnectAttemptRef.current < maxReconnectAttempts) {
          const delay = Math.min(
            BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttemptRef.current),
            MAX_RECONNECT_DELAY
          )
          reconnectAttemptRef.current++
          console.info(`[useResilientChat] Reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current})`)
          reconnectTimerRef.current = setTimeout(() => {
            if (mountedRef.current) connectWs()
          }, delay)
        }
      }

      ws.onerror = () => {
        // Will trigger onclose which handles reconnect
      }
    } catch (err) {
      console.error('[useResilientChat] Failed to create WebSocket:', err)
    }
    }

    ensureFreshTokenAndConnect()
  }, [sessionId, userId, fetchSessionState, getStoredSeq, catchUpEvents, storeSeq, maxReconnectAttempts])

  // ─── Send Message ──────────────────────────────────────────

  const sendMessage = useCallback((message: string, streaming = true) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('[useResilientChat] Cannot send: WebSocket not connected')
      return
    }

    // Pre-send validation: reject messages exceeding character limit
    const MAX_MESSAGE_CHARS = 500_000
    if (message.length > MAX_MESSAGE_CHARS) {
      console.warn(`[useResilientChat] Message rejected: ${message.length} chars exceeds limit of ${MAX_MESSAGE_CHARS}`)
      onEventRef.current?.({
        type: 'error',
        error: 'message_too_long',
        message: `Tin nhắn vượt giới hạn ${MAX_MESSAGE_CHARS.toLocaleString()} ký tự cho phép.`,
      })
      return
    }

    wsRef.current.send(JSON.stringify({
      type: streaming ? 'chat_stream' : 'chat',
      message,
    }))
  }, [])

  // ─── Cancel Task ───────────────────────────────────────────

  const cancelTask = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'cancel' }))
  }, [])

  // ─── Force Reconnect ───────────────────────────────────────

  const reconnect = useCallback(() => {
    reconnectAttemptRef.current = 0
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    connectWs()
  }, [connectWs])

  // ─── Lifecycle ─────────────────────────────────────────────

  useEffect(() => {
    mountedRef.current = true

    if (sessionId) {
      // Load stored sequence
      lastSeqRef.current = getStoredSeq()
      // Connect
      connectWs()
    }

    return () => {
      mountedRef.current = false
      if (sessionId && lastSeqRef.current > 0) {
        localStorage.setItem(`${LAST_SEQ_KEY_PREFIX}${sessionId}`, String(lastSeqRef.current))
      }
      if (sessionId) removeWsActiveSession(sessionId)
      if (sessionId) removeWsConnectingSession(sessionId)
      if (backgroundCheckTimerRef.current) {
        clearTimeout(backgroundCheckTimerRef.current)
        backgroundCheckTimerRef.current = null
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.onclose = null // Prevent reconnect on unmount
        wsRef.current.close()
        wsRef.current = null
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // Visibility change: reconnect when tab becomes visible
  useEffect(() => {
    const handleVisibility = () => {
      if (!document.hidden && sessionId) {
        // WS in CLOSING/CLOSED state with non-null ref won't reconnect otherwise.
        const needsReconnect = !wsRef.current ||
          wsRef.current.readyState === WebSocket.CLOSED ||
          wsRef.current.readyState === WebSocket.CLOSING
        if (needsReconnect) {
          reconnect()
        }
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [sessionId, reconnect])

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (sessionId && lastSeqRef.current > 0) {
        localStorage.setItem(`${LAST_SEQ_KEY_PREFIX}${sessionId}`, String(lastSeqRef.current))
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [sessionId])

  return {
    connected,
    taskRunning,
    taskActivity,
    sendMessage,
    cancelTask,
    reconnect,
  }
}
