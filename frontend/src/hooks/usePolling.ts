// ============================================================
// Nexus - Polling hook with tab visibility awareness
// ============================================================

import { useState, useEffect, useCallback, useRef } from 'react'

interface UsePollingOptions<T> {
  /** Async function that fetches data */
  fetcher: () => Promise<T>
  /** Polling interval in milliseconds */
  interval: number
  /** Whether polling is enabled (default: true) */
  enabled?: boolean
}

interface UsePollingResult<T> {
  /** The fetched data */
  data: T | null
  /** Whether a fetch is in progress */
  loading: boolean
  /** Error message if the last fetch failed */
  error: string | null
  /** Manually trigger a refresh */
  refresh: () => Promise<void>
}

/**
 * Hook for auto-refreshing data at configurable intervals.
 * Pauses polling when the browser tab is not visible (document.hidden).
 */
export function usePolling<T>({
  fetcher,
  interval,
  enabled = true,
}: UsePollingOptions<T>): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetcherRef = useRef(fetcher)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Keep fetcher ref up to date to avoid stale closures
  fetcherRef.current = fetcher

  const inflightRef = useRef(false)

  const refresh = useCallback(async () => {
    if (inflightRef.current) return  // Skip if already fetching
    inflightRef.current = true
    setLoading(true)
    setError(null)
    try {
      const result = await fetcherRef.current()
      setData(result)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Fetch failed'
      setError(message)
    } finally {
      setLoading(false)
      inflightRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) return

    // Initial fetch
    refresh()

    const startPolling = () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
      intervalRef.current = setInterval(() => {
        if (!document.hidden) {
          refresh()
        }
      }, interval)
    }

    const handleVisibilityChange = () => {
      if (!document.hidden) {
        // Tab became visible — fetch immediately and restart interval
        refresh()
        startPolling()
      }
    }

    startPolling()
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [interval, enabled, refresh])

  return { data, loading, error, refresh }
}
