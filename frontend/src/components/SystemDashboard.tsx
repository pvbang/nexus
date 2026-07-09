// ============================================================
// MekongAI Nexus - Enhanced System Dashboard
// Professional dashboard with live metrics, health checks,
// real-time activity feed, and system configuration
// ============================================================

import { useCallback } from 'react'
import { apiGet, type SystemStats } from '../api'
import { usePolling } from '../hooks/usePolling'
import { POLLING_INTERVALS } from '../lib/constants'
import { Spinner } from './ui/Spinner'
import { Card } from './ui/Card'
import { Badge } from './ui/Badge'
import { StatsCard } from './dashboard/StatsCard'
import { HealthCheck } from './dashboard/HealthCheck'
import { LiveMetrics } from './dashboard/LiveMetrics'
import { RecentActivity } from './dashboard/RecentActivity'
import { SystemConfig, type SystemInfo } from './dashboard/SystemConfig'

// ---------------------------------------------------------------------------
// Gradient presets for stat cards
// ---------------------------------------------------------------------------

const STAT_GRADIENTS = [
  'from-blue-500 to-blue-600',
  'from-emerald-500 to-emerald-600',
  'from-purple-500 to-purple-600',
  'from-amber-500 to-amber-600',
] as const

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemDashboard() {
  // Fetch system stats every 10s
  const statsFetcher = useCallback(
    () => apiGet<SystemStats>('/system/stats'),
    [],
  )
  const { data: stats, loading: statsLoading } = usePolling<SystemStats>({
    fetcher: statsFetcher,
    interval: POLLING_INTERVALS.slow,
  })

  // Fetch system info every 30s (rarely changes)
  const infoFetcher = useCallback(
    () => apiGet<SystemInfo>('/system/info'),
    [],
  )
  const { data: info } = usePolling<SystemInfo>({
    fetcher: infoFetcher,
    interval: POLLING_INTERVALS.lazy,
  })

  // Initial loading state
  if (statsLoading && !stats) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <Spinner size="lg" className="text-brand-500 mx-auto mb-3" />
          <p className="text-text-muted dark:text-gray-400 text-sm">Loading dashboard...</p>
        </div>
      </div>
    )
  }

  const statCards = [
    { icon: '🤖', label: 'Agents', value: stats?.agents.total ?? 0, gradient: STAT_GRADIENTS[0] },
    { icon: '📋', label: 'Tasks', value: stats?.tasks.total ?? 0, gradient: STAT_GRADIENTS[1] },
    { icon: '🐟', label: 'Swarms', value: stats?.swarms.total ?? 0, gradient: STAT_GRADIENTS[2] },
    { icon: '💬', label: 'Messages', value: stats?.messages.total ?? 0, gradient: STAT_GRADIENTS[3] },
  ]

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-6">
        {/* Header */}
        <div className="animate-fade-in">
          <h2 className="font-display text-2xl font-semibold text-text-dark dark:text-gray-100">
            System Dashboard
          </h2>
          <p className="text-text-secondary dark:text-gray-400 mt-1">
            Tổng quan hệ thống MekongAI Nexus — real-time monitoring & health checks
          </p>
        </div>

        {/* Row 1: Stat Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 animate-slide-up">
          {statCards.map((stat) => (
            <StatsCard
              key={stat.label}
              icon={stat.icon}
              label={stat.label}
              value={stat.value}
              gradient={stat.gradient}
            />
          ))}
        </div>

        {/* Row 2: LiveMetrics + HealthCheck */}
        <div
          className="grid grid-cols-1 lg:grid-cols-3 gap-4 animate-slide-up"
          style={{ animationDelay: '100ms' }}
        >
          <LiveMetrics className="lg:col-span-2" />
          <HealthCheck />
        </div>

        {/* Row 3: RecentActivity + Knowledge Graph */}
        <div
          className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-slide-up"
          style={{ animationDelay: '200ms' }}
        >
          <RecentActivity maxItems={20} />

          {/* Knowledge Graph summary */}
          <Card variant="brand" padding="md">
            <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100 mb-4">
              🧬 Knowledge Graph
            </h3>
            {stats?.knowledge_graph ? (
              <>
                <div className="grid grid-cols-3 gap-4">
                  <div className="text-center">
                    <div className="text-2xl font-bold font-display text-brand-600 dark:text-brand-400 tabular-nums">
                      {stats.knowledge_graph.node_count.toLocaleString()}
                    </div>
                    <div className="text-xs text-text-muted dark:text-gray-400 mt-1">Nodes</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold font-display text-purple-600 dark:text-purple-400 tabular-nums">
                      {stats.knowledge_graph.edge_count.toLocaleString()}
                    </div>
                    <div className="text-xs text-text-muted dark:text-gray-400 mt-1">Edges</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold font-display text-amber-600 dark:text-amber-400 tabular-nums">
                      {stats.knowledge_graph.entity_types.length}
                    </div>
                    <div className="text-xs text-text-muted dark:text-gray-400 mt-1">Entity Types</div>
                  </div>
                </div>
                {stats.knowledge_graph.entity_types.length > 0 && (
                  <div className="mt-4 pt-4 border-t border-border-light dark:border-[#2a2a45] flex flex-wrap gap-2">
                    {stats.knowledge_graph.entity_types.map((type) => (
                      <Badge key={type} variant="brand" size="sm">
                        {type}
                      </Badge>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <span className="text-3xl mb-2" role="img" aria-hidden="true">🧬</span>
                <p className="text-sm text-text-muted dark:text-gray-400">
                  Knowledge graph data unavailable
                </p>
              </div>
            )}
          </Card>
        </div>

        {/* Row 4: SystemConfig (full width) */}
        <div
          className="animate-slide-up"
          style={{ animationDelay: '300ms' }}
        >
          <SystemConfig info={info} />
        </div>
      </div>
    </div>
  )
}
