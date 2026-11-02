// ============================================================
// Nexus - useTaskPolling Hook
//
// ARCHITECTURE: DB-first polling, no SSE/WS dependency.
//
// Pure polling pattern for chat task events:
// - Poll GET /tasks/{taskId}/events?after_seq=N every 2s
// - Append new events (never replace) for smooth UI
// - Stop polling when task_completed/task_failed detected
// - Pause when tab hidden, resume + immediate fetch on focus
// - React keys via event.sequence for stable identity
// ============================================================

import { useState, useEffect, useRef, useCallback } from 'react'
import { API_URL } from '../lib/constants'

const AUTH_TOKEN_KEY = 'nexus_auth_token'

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    return { 'Authorization': `Bearer ${token}` }
  }
  return {}
}

/** Single task event from the DB */
export interface TaskEvent {
  id: string
  task_id: string
  session_id: string
  event_type: string
  data: Record<string, unknown>
  created_at: string | null
  sequence: number
}

/**
 * Compute the genuinely-new events to append.
 *
 * Dedup key is the PAIR (task_id, sequence), not sequence alone: sequence is
 * per-task and restarts at 0 for each new task, so two tasks in the same
 * session can share a sequence number. Single-sequence dedup would wrongly
 * drop a different task's event. Also dedups duplicates within `incoming`.
 */
export function dedupBySeqAndTask(prev: TaskEvent[], incoming: TaskEvent[]): TaskEvent[] {
  const keyOf = (e: TaskEvent) => `${e.task_id}:${e.sequence}`
  const seen = new Set(prev.map(keyOf))
  const result: TaskEvent[] = []
  for (const evt of incoming) {
    const key = keyOf(evt)
    if (seen.has(key)) continue
    seen.add(key)
    result.push(evt)
  }
  return result
}

/** Terminal event types that signal task completion */
const TERMINAL_EVENT_TYPES = new Set([
  'task_completed',
  'task_failed',
  'task_cancelled',
])

/**
 * If no new event arrives for this long, the task may have died silently
 * (worker SIGKILL/OOM/lost connection) WITHOUT writing a terminal event —
 * in which case pure event-polling would loop forever and the UI would stay
 * stuck "processing". After this much silence we re-verify against /state and,
 * if the backend reports the task is no longer running, synthesize a terminal
 * event so the UI completes and loads the final result from the DB.
 */
const STALE_SILENCE_MS = 30_000

export interface UseTaskPollingOptions {
  /** Task ID to poll events for (null = inactive) */
  taskId: string | null
  /** Session ID for the conversation */
  sessionId: string
  /** Poll interval in ms (default: 2000) */
  intervalMs?: number
  /** Called when new events arrive */
  onEvents?: (events: TaskEvent[]) => void
  /** Called when task reaches terminal state */
  onComplete?: (finalEvent: TaskEvent) => void
  /** Called on poll error */
  onError?: (error: string) => void
}

export interface UseTaskPollingReturn {
  /** All events received so far (ordered by sequence) */
  events: TaskEvent[]
  /** Whether polling is actively running */
  isPolling: boolean
  /** Whether the task has completed */
  isComplete: boolean
  /** Last error message (if any) */
  error: string | null
  /** Manually stop polling */
  stop: () => void
  /** Force an immediate poll */
  pollNow: () => void
}

/**
 * ARCHITECTURE: DB-first polling, no SSE/WS dependency.
 *
 * Hook that polls task events from the DB at a fixed interval.
 * Events are appended incrementally using after_seq for efficiency.
 * Automatically stops when a terminal event is detected.
 */
export function useTaskPolling(options: UseTaskPollingOptions): UseTaskPollingReturn {
  const {
    taskId,
    sessionId,
    intervalMs = 2000,
    onEvents,
    onComplete,
    onError,
  } = options

  const [events, setEvents] = useState<TaskEvent[]>([])
  const [isPolling, setIsPolling] = useState(false)
  const [isComplete, setIsComplete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Refs for stable callback identity + mutable state in intervals
  const lastSeqRef = useRef(0)
  const isPollingRef = useRef(false)
  const isFetchingRef = useRef(false)
  // Wall-clock of the last time any new event was received — drives stale detection.
  const lastEventAtRef = useRef(0)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const mountedRef = useRef(true)
  const taskIdRef = useRef(taskId)
  const onEventsRef = useRef(onEvents)
  const onCompleteRef = useRef(onComplete)
  const onErrorRef = useRef(onError)

  // Keep refs in sync
  onEventsRef.current = onEvents
  onCompleteRef.current = onComplete
  onErrorRef.current = onError
  taskIdRef.current = taskId

  // ── Core fetch function ──────────────────────────────────────
  const fetchEvents = useCallback(async () => {
    const currentTaskId = taskIdRef.current
    if (!currentTaskId || !mountedRef.current || isFetchingRef.current) return

    isFetchingRef.current = true
    try {
      const url = `${API_URL}/api/v1/tasks/${currentTaskId}/events?session_id=${sessionId}&after_seq=${lastSeqRef.current}&limit=500`
      const res = await fetch(url, { headers: getAuthHeaders() })

      if (!res.ok) {
        if (res.status === 404) {
          // Task not found — might not be persisted yet, retry
          return
        }
        throw new Error(`Poll failed: ${res.status}`)
      }

      const data = await res.json()
      const newEvents: TaskEvent[] = Array.isArray(data) ? data : (data.events || [])

      if (newEvents.length === 0) {
        // No new events. If the task has been silent for too long, it may have died
        // without emitting a terminal event (SIGKILL/OOM/lost worker). Verify against
        // the authoritative session state; if it is no longer running, synthesize a
        // terminal event so the UI completes instead of polling forever.
        const silentFor = Date.now() - lastEventAtRef.current
        if (lastEventAtRef.current > 0 && silentFor >= STALE_SILENCE_MS) {
          try {
            const stateUrl = `${API_URL}/api/v1/sessions/${sessionId}/state`
            const stateRes = await fetch(stateUrl, { headers: getAuthHeaders() })
            if (stateRes.ok && mountedRef.current) {
              const state = await stateRes.json()
              // Only complete on a genuinely terminal state. is_running can read
              // false transiently while the backend resolves liveness, but if
              // task_status still says the task is working (executing/planning/
              // decomposing) we must NOT synthesize completion — that was the bug
              // where the UI declared "completed" while the backend kept running.
              const stillWorking = ['executing', 'planning', 'decomposing', 'running'].includes(state?.task_status)
              if (state && state.is_running === false && !stillWorking) {
                const isFailure = ['failed', 'cancelled', 'crashed'].includes(state.task_status)
                const synthetic: TaskEvent = {
                  id: `synthetic-terminal-${currentTaskId}`,
                  task_id: currentTaskId,
                  session_id: sessionId,
                  event_type: isFailure ? 'task_failed' : 'task_completed',
                  data: { synthetic: true, task_status: state.task_status },
                  created_at: null,
                  sequence: lastSeqRef.current,
                }
                setIsComplete(true)
                setIsPolling(false)
                isPollingRef.current = false
                if (intervalRef.current) {
                  clearInterval(intervalRef.current)
                  intervalRef.current = null
                }
                onCompleteRef.current?.(synthetic)
              }
            }
          } catch { /* network blip — retry next interval */ }
        }
        return
      }
      if (!mountedRef.current) return

      // New events arrived — reset the silence timer.
      lastEventAtRef.current = Date.now()

      // Update last sequence
      for (const evt of newEvents) {
        if (evt.sequence > lastSeqRef.current) {
          lastSeqRef.current = evt.sequence
        }
      }

      // Append new events (deduplicate by (task_id, sequence))
      setEvents(prev => {
        const genuinelyNew = dedupBySeqAndTask(prev, newEvents)
        if (genuinelyNew.length === 0) return prev
        return [...prev, ...genuinelyNew]
      })

      // Notify callback
      onEventsRef.current?.(newEvents)

      // Check for terminal events
      const terminalEvent = newEvents.find(e => TERMINAL_EVENT_TYPES.has(e.event_type))
      if (terminalEvent) {
        setIsComplete(true)
        setIsPolling(false)
        isPollingRef.current = false
        if (intervalRef.current) {
          clearInterval(intervalRef.current)
          intervalRef.current = null
        }
        onCompleteRef.current?.(terminalEvent)
      }
    } catch (err) {
      if (!mountedRef.current) return
      const msg = err instanceof Error ? err.message : 'Unknown error'
      setError(msg)
      onErrorRef.current?.(msg)
    } finally {
      isFetchingRef.current = false
    }
  }, [sessionId])

  // ── Start/stop polling based on taskId ────────────────────────
  useEffect(() => {
    if (!taskId) {
      // No task — clean up
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      setIsPolling(false)
      isPollingRef.current = false
      return
    }

    // New task — reset state and start polling
    lastSeqRef.current = 0
    // Seed the silence timer from task start so a task that dies before emitting
    // ANY event is still eligible for stale detection (guard requires > 0).
    lastEventAtRef.current = Date.now()
    setEvents([])
    setIsComplete(false)
    setError(null)
    setIsPolling(true)
    isPollingRef.current = true

    // Immediate first fetch
    fetchEvents()

    // Start interval
    intervalRef.current = setInterval(() => {
      // Pause when tab is hidden
      if (document.hidden) return
      if (!isPollingRef.current) return
      fetchEvents()
    }, intervalMs)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      isPollingRef.current = false
    }
  }, [taskId, intervalMs, fetchEvents])

  // ── Visibility change: immediate fetch on tab focus ───────────
  useEffect(() => {
    const handleVisibility = () => {
      if (!document.hidden && isPollingRef.current && taskIdRef.current) {
        // Tab became visible — fetch immediately
        fetchEvents()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [fetchEvents])

  // ── Cleanup on unmount ────────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [])

  // ── Public controls ───────────────────────────────────────────
  const stop = useCallback(() => {
    setIsPolling(false)
    isPollingRef.current = false
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  const pollNow = useCallback(() => {
    fetchEvents()
  }, [fetchEvents])

  return {
    events,
    isPolling,
    isComplete,
    error,
    stop,
    pollNow,
  }
}
