// ============================================================
// Nexus - HealthCheck Dashboard Component
// Visualizes system doctor API health checks with expandable
// details, overall health score, and auto-refresh via polling
// ============================================================

import { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { apiGet } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { POLLING_INTERVALS } from '../../lib/constants'
import { Card } from '../ui/Card'
import { Badge } from '../ui/Badge'
import { Skeleton } from '../ui/Skeleton'
import { Button } from '../ui/Button'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface HealthCheckItem {
  name: string
  status: 'healthy' | 'warning' | 'error'
  message: string
  details?: Record<string, unknown>
}

export interface HealthCheckProps {
  /** Additional CSS classes */
  className?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getOverallStatus(checks: HealthCheckItem[]): 'healthy' | 'warning' | 'error' {
  if (checks.some((c) => c.status === 'error')) return 'error'
  if (checks.some((c) => c.status === 'warning')) return 'warning'
  return 'healthy'
}

const statusConfig = {
  healthy: {
    icon: '✅',
    badge: 'success' as const,
    label: 'All Healthy',
  },
  warning: {
    icon: '⚠️',
    badge: 'warning' as const,
    label: 'Warnings',
  },
  error: {
    icon: '❌',
    badge: 'error' as const,
    label: 'Errors Detected',
  },
}

const checkStatusIcon: Record<string, string> = {
  healthy: '✅',
  warning: '⚠️',
  error: '❌',
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HealthCheck({ className }: HealthCheckProps) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null)

  const fetcher = useCallback(
    () => apiGet<{ summary: Record<string, number>; results: HealthCheckItem[] }>('/doctor')
      .then(data => data.results),
    [],
  )

  const { data: checks, loading, error, refresh } = usePolling<HealthCheckItem[]>({
    fetcher,
    interval: POLLING_INTERVALS.lazy,
  })

  const toggleExpand = (index: number) => {
    setExpandedIndex((prev) => (prev === index ? null : index))
  }

  // Loading state
  if (loading && !checks) {
    return (
      <Card variant="default" padding="md" className={className}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100">
            🏥 Health Checks
          </h3>
          <Skeleton className="w-20 h-6" />
        </div>
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton variant="circular" className="w-5 h-5" />
              <Skeleton className="flex-1 h-4" />
            </div>
          ))}
        </div>
      </Card>
    )
  }

  // Error state
  if (error && !checks) {
    return (
      <Card variant="default" padding="md" className={className}>
        <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100 mb-3">
          🏥 Health Checks
        </h3>
        <div className="flex flex-col items-center py-6 text-center">
          <span className="text-3xl mb-2" role="img" aria-hidden="true">⚠️</span>
          <p className="text-sm text-red-600 dark:text-red-400 mb-3">{error}</p>
          <Button variant="secondary" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      </Card>
    )
  }

  if (!checks || !Array.isArray(checks) || checks.length === 0) {
    return (
      <Card variant="default" padding="md" className={className}>
        <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100 mb-3">
          🏥 Health Checks
        </h3>
        <p className="text-sm text-text-muted dark:text-gray-400 text-center py-4">
          No health checks available
        </p>
      </Card>
    )
  }

  const overall = getOverallStatus(checks)
  const config = statusConfig[overall]

  return (
    <Card variant="default" padding="md" className={className}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100">
          🏥 Health Checks
        </h3>
        <Badge variant={config.badge} dot>
          {config.label}
        </Badge>
      </div>

      {/* Check items */}
      <div className="space-y-2">
        {checks.map((check, index) => (
          <div key={check.name}>
            <button
              type="button"
              onClick={() => check.details ? toggleExpand(index) : undefined}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left',
                'transition-colors duration-150',
                'hover:bg-gray-50 dark:hover:bg-[#252540]',
                check.details && 'cursor-pointer',
                !check.details && 'cursor-default',
              )}
              aria-expanded={check.details ? expandedIndex === index : undefined}
            >
              {/* Status icon */}
              <span className="text-base shrink-0" aria-hidden="true">
                {checkStatusIcon[check.status] || '❓'}
              </span>

              {/* Name and message */}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-text-primary dark:text-gray-200">
                  {check.name}
                </div>
                <div className="text-xs text-text-muted dark:text-gray-400 truncate">
                  {check.message}
                </div>
              </div>

              {/* Expand indicator */}
              {check.details && (
                <span
                  className={cn(
                    'text-xs text-text-muted dark:text-gray-500 transition-transform duration-200',
                    expandedIndex === index && 'rotate-180',
                  )}
                  aria-hidden="true"
                >
                  ▼
                </span>
              )}
            </button>

            {/* Expanded details */}
            {check.details && expandedIndex === index && (
              <div className="ml-9 mr-3 mt-1 mb-2 p-3 rounded-lg bg-gray-50 dark:bg-[#1f1f38] text-xs font-mono">
                <pre className="whitespace-pre-wrap break-words text-text-secondary dark:text-gray-400">
                  {JSON.stringify(check.details, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
