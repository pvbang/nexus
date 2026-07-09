// ============================================================
// MekongAI Nexus - useFullEvents Hook
// Lazy-fetches full event data from API for a given taskId.
// Caches results to avoid refetching. Used by MessageEventExplorer.
// ============================================================

import { useState, useCallback, useRef, useEffect } from 'react'
import { API_URL } from '../lib/constants'

const AUTH_TOKEN_KEY = 'nexus_auth_token'

/** Build auth headers for API calls */
function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    return { 'Authorization': `Bearer ${token}` }
  }
  return {}
}

export interface FullEvent {
  id: string
  task_id: string
  session_id: string
  event_type: string
  data: Record<string, unknown>
  created_at: string | null
  started_at: string | null
  sequence: number
}

interface UseFullEventsReturn {
  /** Full events from API (null = not yet fetched) */
  fullEvents: FullEvent[] | null
  /** Loading state */
  isLoading: boolean
  /** Error message if fetch failed */
  error: string | null
  /** Trigger fetch */
  fetchFullEvents: () => Promise<void>
  /** Whether full events have been loaded */
  isLoaded: boolean
}

/** Module-level cache: taskId → full events (survives component remounts) */
const _cache = new Map<string, FullEvent[]>()

/** Module-level max sequence tracker: taskId → highest sequence seen */
const _maxSeqByTask = new Map<string, number>()

/** Maximum number of cached conversations (LRU eviction) */
const _MAX_CACHE_SIZE = 20

function _evictOldestCache() {
  if (_cache.size > _MAX_CACHE_SIZE) {
    // Map preserves insertion order — delete first entry
    const firstKey = _cache.keys().next().value
    if (firstKey) {
      _cache.delete(firstKey)
      _maxSeqByTask.delete(firstKey)
    }
  }
}

/**
 * Hook to lazily fetch full execution events from API.
 * Returns cached data if already fetched for this taskId.
 *
 * FIX-P2.2: Added `isTaskRunning` parameter. When true, polls every 1s
 * for fresh events (bypasses cache). This ensures frontend always shows
 * latest events during active task execution without manual refresh.
 *
 * FIX-11: Race condition fix — during polling, only APPEND new events
 * (never replace with smaller set). Uses after_seq for incremental fetch
 * to reduce payload size and prevent stale-response overwrites.
 */
export function useFullEvents(
  taskId: string | undefined | null,
  sessionId: string,
  isTaskRunning: boolean = false,
): UseFullEventsReturn {
  const [fullEvents, setFullEvents] = useState<FullEvent[] | null>(() => {
    if (taskId && !isTaskRunning && _cache.has(taskId)) return _cache.get(taskId)!
    return null
  })
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchingRef = useRef(false)
  // FIX-11: Track highest sequence for incremental fetch
  const maxSeqRef = useRef<number>(_maxSeqByTask.get(taskId || '') ?? 0)
  // P2-02 FIX: Track consecutive empty polls for stale detection
  const emptyPollCountRef = useRef(0)
  const STALE_THRESHOLD = 5 // After 5 empty polls while running → full refetch

  const fetchFullEvents = useCallback(async () => {
    // Already loading — skip
    if (fetchingRef.current) return
    // Use cache ONLY when task is NOT running (completed tasks don't change)
    if (taskId && !isTaskRunning && _cache.has(taskId)) {
      setFullEvents(_cache.get(taskId)!)
      return
    }

    fetchingRef.current = true
    // Only show loading spinner on initial fetch, not polling updates
    const isInitialFetch = maxSeqRef.current === 0
    if (isInitialFetch) setIsLoading(true)
    setError(null)

    try {
      let url: string
      if (taskId) {
        // FIX-11: Incremental fetch — only get events after last known sequence
        const afterSeq = isTaskRunning ? maxSeqRef.current : 0
        url = `${API_URL}/api/v1/tasks/${taskId}/events?session_id=${sessionId}&after_seq=${afterSeq}&limit=500`
      } else {
        // Fallback: fetch session events
        url = `${API_URL}/api/v1/sessions/${sessionId}/events?after_seq=${maxSeqRef.current}&limit=500`
      }

      const res = await fetch(url, { headers: getAuthHeaders() })
      if (!res.ok) {
        throw new Error(`Failed to fetch events: ${res.status}`)
      }

      const data = await res.json()

      // API returns different formats depending on endpoint
      let newEvents: FullEvent[]
      if (Array.isArray(data)) {
        // /tasks/{taskId}/events returns array directly
        newEvents = data
      } else if (data.events && Array.isArray(data.events)) {
        // /sessions/{id}/events returns { events: [...] }
        newEvents = data.events.map((e: Record<string, unknown>, idx: number) => ({
          id: String(e.id || `evt-${idx}`),
          task_id: String(e.task_id || taskId || ''),
          session_id: String(e.session_id || sessionId),
          event_type: String(e.event_type || ''),
          data: (e.data as Record<string, unknown>) || {},
          created_at: e.created_at ? String(e.created_at) : null,
          started_at: null,
          sequence: Number(e.sequence || idx),
        }))
      } else {
        newEvents = []
      }

      // FIX-11: Merge strategy — append new events, never shrink
      setFullEvents(prev => {
        if (!prev || maxSeqRef.current === 0) {
          // Initial load — use full response
          const merged = newEvents
          // Track max sequence
          for (const evt of merged) {
            if (evt.sequence > maxSeqRef.current) maxSeqRef.current = evt.sequence
          }
          if (taskId) _maxSeqByTask.set(taskId, maxSeqRef.current)
          return merged
        }

        if (newEvents.length === 0) {
          // P2-02 FIX: Track empty polls for stale detection
          if (isTaskRunning) {
            emptyPollCountRef.current += 1
            // If too many empty polls while task is running, possible sequence gap
            // Reset after_seq to do a full refetch next time
            if (emptyPollCountRef.current >= STALE_THRESHOLD) {
              emptyPollCountRef.current = 0
              maxSeqRef.current = 0
              if (taskId) _maxSeqByTask.set(taskId, 0)
              // Don't change state — next poll will do full fetch
            }
          }
          // No new events — keep existing (never downgrade)
          return prev
        }
        // Reset empty counter on successful fetch
        emptyPollCountRef.current = 0

        // Incremental: append only genuinely new events (deduplicate by sequence)
        const existingSeqs = new Set(prev.map(e => e.sequence))
        const genuinelyNew = newEvents.filter(e => !existingSeqs.has(e.sequence))

        if (genuinelyNew.length === 0) return prev

        const merged = [...prev, ...genuinelyNew]
        // Update max sequence
        for (const evt of genuinelyNew) {
          if (evt.sequence > maxSeqRef.current) maxSeqRef.current = evt.sequence
        }
        if (taskId) _maxSeqByTask.set(taskId, maxSeqRef.current)
        return merged
      })

      // Cache result ONLY when task is completed (running tasks change constantly)
      if (taskId && !isTaskRunning && newEvents.length > 0) {
        // For completed tasks, cache the final merged result
        setFullEvents(current => {
          if (current && taskId) {
            _cache.set(taskId, current)
            _evictOldestCache()
          }
          return current
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setIsLoading(false)
      fetchingRef.current = false
    }
  }, [taskId, sessionId, isTaskRunning])

  // AUTO-FETCH: Automatically load full events on mount when taskId is available.
  useEffect(() => {
    if (taskId && !fullEvents && !fetchingRef.current) {
      // Reset sequence tracker for new task
      maxSeqRef.current = _maxSeqByTask.get(taskId) ?? 0
      fetchFullEvents()
    }
  }, [taskId, fetchFullEvents]) // eslint-disable-line react-hooks/exhaustive-deps

  // FIX-P2.2: LIVE POLLING — fetch every 1s when task is still running.
  // Ensures frontend always has the latest events without manual refresh.
  // Stops automatically when isTaskRunning becomes false (task completed).
  useEffect(() => {
    if (!isTaskRunning || !taskId) return

    const interval = setInterval(() => {
      if (!document.hidden && !fetchingRef.current) {
        fetchFullEvents()
      }
    }, 1000)

    return () => clearInterval(interval)
  }, [isTaskRunning, taskId, fetchFullEvents])

  // When task transitions from running → completed, cache the final events
  useEffect(() => {
    if (!isTaskRunning && taskId && fullEvents && fullEvents.length > 0) {
      _cache.set(taskId, fullEvents)
      _evictOldestCache()
    }
  }, [isTaskRunning, taskId, fullEvents])

  return {
    fullEvents,
    isLoading,
    error,
    fetchFullEvents,
    isLoaded: fullEvents !== null,
  }
}
