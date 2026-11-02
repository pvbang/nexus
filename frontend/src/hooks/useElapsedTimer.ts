// ============================================================
// Nexus - useElapsedTimer Hook
// Live elapsed timer for tool execution tracking.
// Updates every 100ms while active, returns formatted string.
// ============================================================

import { useState, useEffect, useRef } from 'react'

/** Format elapsed milliseconds into human-readable string */
export function formatElapsed(ms: number): string {
  if (ms < 0) return '0.0s'
  const totalSeconds = ms / 1000
  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1)}s`
  }
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.floor(totalSeconds % 60)
  return `${minutes}m ${seconds}s`
}

interface UseElapsedTimerOptions {
  /** Unix timestamp (ms) when the timer started */
  startTimestamp: number
  /** Whether the timer is currently active */
  isActive: boolean
  /** Final duration from server (ms) — displayed when timer stops */
  finalDurationMs?: number
}

interface UseElapsedTimerResult {
  /** Current elapsed time in ms */
  elapsedMs: number
  /** Formatted elapsed string (e.g. "1.2s", "1m 5s") */
  elapsedFormatted: string
}

/**
 * Hook that provides a live elapsed timer counting from a start timestamp.
 * Updates at ~100ms intervals while active. When stopped, displays
 * the server-provided duration if available, otherwise the last elapsed value.
 */
export function useElapsedTimer({
  startTimestamp,
  isActive,
  finalDurationMs,
}: UseElapsedTimerOptions): UseElapsedTimerResult {
  const [elapsedMs, setElapsedMs] = useState(() => {
    if (!isActive && finalDurationMs != null) return finalDurationMs
    return Math.max(0, Date.now() - startTimestamp)
  })

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (isActive) {
      // Immediately sync
      setElapsedMs(Math.max(0, Date.now() - startTimestamp))

      intervalRef.current = setInterval(() => {
        setElapsedMs(Math.max(0, Date.now() - startTimestamp))
      }, 100)

      return () => {
        if (intervalRef.current) {
          clearInterval(intervalRef.current)
          intervalRef.current = null
        }
      }
    }

    // Timer stopped — use server duration if available
    if (finalDurationMs != null) {
      setElapsedMs(finalDurationMs)
    }

    // Cleanup any lingering interval
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }

    return undefined
  }, [isActive, startTimestamp, finalDurationMs])

  return {
    elapsedMs,
    elapsedFormatted: formatElapsed(
      !isActive && finalDurationMs != null ? finalDurationMs : elapsedMs,
    ),
  }
}
