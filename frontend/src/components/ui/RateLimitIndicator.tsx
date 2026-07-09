// ============================================================
// MekongAI Nexus - Rate Limit Indicator
// Shows remaining API rate limit in the status bar
// ============================================================

import { useState, useEffect } from 'react'
import { Gauge } from 'lucide-react'
import { cn } from '../../lib/cn'

// Singleton to track rate limit state across all API calls
let _rateLimitState = {
  limit: 0,
  remaining: 0,
  lastUpdated: 0,
}

/**
 * Call this from the API layer after receiving a response with rate limit headers.
 * Exported so api.ts can invoke it.
 */
export function updateRateLimit(limit: number, remaining: number): void {
  _rateLimitState = { limit, remaining, lastUpdated: Date.now() }
  // Dispatch custom event so React component re-renders
  window.dispatchEvent(new CustomEvent('nexus:ratelimit-update', { detail: _rateLimitState }))
}

export function getRateLimitState() {
  return _rateLimitState
}

interface RateLimitIndicatorProps {
  className?: string
}

export function RateLimitIndicator({ className }: RateLimitIndicatorProps) {
  const [state, setState] = useState(_rateLimitState)

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      setState({ ...detail })
    }
    window.addEventListener('nexus:ratelimit-update', handler)
    return () => window.removeEventListener('nexus:ratelimit-update', handler)
  }, [])

  // Don't show if no rate limit data yet
  if (state.limit === 0 || state.lastUpdated === 0) return null

  const pct = state.limit > 0 ? (state.remaining / state.limit) * 100 : 100
  const isLow = pct < 20
  const isCritical = pct < 5

  return (
    <div
      className={cn(
        'flex items-center gap-1.5 text-[11px] font-medium',
        isCritical
          ? 'text-red-500'
          : isLow
            ? 'text-amber-500'
            : 'text-gray-400 dark:text-text-muted',
        className,
      )}
      title={`Rate limit: ${state.remaining}/${state.limit} requests remaining`}
    >
      <Gauge className="w-3 h-3" />
      <span>{state.remaining}/{state.limit}</span>
      {isLow && (
        <span className={cn(
          'inline-block w-1.5 h-1.5 rounded-full animate-pulse',
          isCritical ? 'bg-red-500' : 'bg-amber-500',
        )} />
      )}
    </div>
  )
}
