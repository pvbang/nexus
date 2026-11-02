// ============================================================
// Nexus - Plugin Manager Admin View
// Plugin system overview with detail expansion
// ============================================================

import { useState, useCallback } from 'react'
import { apiGet } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { Modal } from '../ui/Modal'
import { Skeleton } from '../ui/Skeleton'

// ============================================================
// Types
// ============================================================

interface Plugin {
  name: string
  version?: string
  description?: string
  status: 'active' | 'inactive' | 'error' | string
  hooks?: string[]
  hooks_count?: number
  capabilities?: string[]
  config?: Record<string, unknown>
  manifest?: Record<string, unknown>
  author?: string
  homepage?: string
}

// ============================================================
// Helpers
// ============================================================

function pluginStatusVariant(status: string): 'success' | 'error' | 'warning' | 'default' {
  switch (status.toLowerCase()) {
    case 'active':
    case 'enabled':
      return 'success'
    case 'inactive':
    case 'disabled':
      return 'default'
    case 'error':
      return 'error'
    default:
      return 'warning'
  }
}

function redactConfigValue(value: unknown): string {
  if (typeof value === 'string') {
    if (value.length > 8) {
      return value.slice(0, 4) + '••••' + value.slice(-2)
    }
    return '••••••'
  }
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return String(value)
  if (value === null || value === undefined) return '—'
  return '••••••'
}

// ============================================================
// Skeleton loader
// ============================================================

function PluginCardSkeleton() {
  return (
    <Card variant="default" padding="md">
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <Skeleton className="h-5 w-40 mb-1.5" />
          <Skeleton className="h-4 w-full" />
        </div>
        <Skeleton className="h-6 w-16" variant="rectangular" />
      </div>
      <div className="flex items-center gap-2">
        <Skeleton className="h-5 w-12" variant="rectangular" />
        <Skeleton className="h-5 w-20" variant="rectangular" />
        <Skeleton className="h-5 w-16" variant="rectangular" />
      </div>
    </Card>
  )
}

// ============================================================
// Main Component
// ============================================================

export function PluginManager() {
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null)
  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  // Fetch plugin list with polling (30s)
  // Handle 404 gracefully — plugins API may not be implemented yet
  const { data: plugins, loading, error, refresh } = usePolling<Plugin[]>({
    fetcher: useCallback(async () => {
      try {
        return await apiGet<Plugin[]>('/plugins')
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        if (message.includes('404')) {
          throw new Error('Plugins API not available — this feature may not be enabled on your server')
        }
        throw err
      }
    }, []),
    interval: 30_000,
  })

  // Open detail modal with full plugin info
  const handleViewPlugin = async (plugin: Plugin) => {
    setDetailLoading(true)
    setDetailModalOpen(true)
    try {
      const detail = await apiGet<Plugin>(`/plugins/${encodeURIComponent(plugin.name)}`)
      setSelectedPlugin(detail)
    } catch {
      setSelectedPlugin(plugin)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleCloseDetail = () => {
    setDetailModalOpen(false)
    setSelectedPlugin(null)
  }

  // Count active plugins
  const activeCount = plugins?.filter((p) => p.status === 'active' || p.status === 'enabled').length ?? 0

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6 animate-fade-in">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'w-10 h-10 rounded-[12px] flex items-center justify-center',
                'bg-blue-50 dark:bg-blue-900/30',
              )}
            >
              <span className="text-lg" role="img" aria-label="Plugins">
                🔌
              </span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
                  Plugin Manager
                </h2>
                {plugins && (
                  <Badge variant="info" size="sm">
                    {plugins.length} plugins
                  </Badge>
                )}
                {plugins && activeCount > 0 && (
                  <Badge variant="success" size="sm" dot>
                    {activeCount} active
                  </Badge>
                )}
              </div>
              <p className="text-[13px] text-text-muted dark:text-gray-400">
                Manage installed plugins and extensions
              </p>
            </div>
          </div>

          <Button variant="ghost" size="sm" onClick={refresh}>
            Refresh
          </Button>
        </div>

        {/* Error state */}
        {error && (
          <Card variant="default" padding="md" className="mb-6 border-red-200 dark:border-red-800">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <Button variant="ghost" size="sm" onClick={refresh} className="mt-2">
              Retry
            </Button>
          </Card>
        )}

        {/* Loading skeleton */}
        {loading && !plugins && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <PluginCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && plugins && plugins.length === 0 && (
          <EmptyState
            icon="🔌"
            title="No plugins installed"
            description="Plugins extend system capabilities with custom hooks and features"
          />
        )}

        {/* Plugin list */}
        {plugins && plugins.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in">
            {plugins.map((plugin) => {
              const hooksCount = plugin.hooks_count ?? plugin.hooks?.length ?? 0
              return (
                <Card
                  key={plugin.name}
                  variant="interactive"
                  padding="md"
                  onClick={() => handleViewPlugin(plugin)}
                  className="cursor-pointer"
                >
                  {/* Top row: name + status */}
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-sm font-semibold text-text-dark dark:text-gray-100 truncate">
                          {plugin.name}
                        </h3>
                        {plugin.version && (
                          <Badge variant="default" size="sm">
                            v{plugin.version}
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-text-muted dark:text-gray-400 line-clamp-2">
                        {plugin.description || 'No description'}
                      </p>
                    </div>
                    <div className="ml-3 shrink-0">
                      <Badge variant={pluginStatusVariant(plugin.status)} size="sm" dot>
                        {plugin.status}
                      </Badge>
                    </div>
                  </div>

                  {/* Bottom: hooks count + capabilities */}
                  <div className="flex items-center gap-2 flex-wrap mt-3">
                    {hooksCount > 0 && (
                      <Badge variant="purple" size="sm">
                        {hooksCount} hook{hooksCount !== 1 ? 's' : ''}
                      </Badge>
                    )}
                    {plugin.capabilities?.slice(0, 3).map((cap) => (
                      <Badge key={cap} variant="default" size="sm">
                        {cap}
                      </Badge>
                    ))}
                    {plugin.capabilities && plugin.capabilities.length > 3 && (
                      <span className="text-[10px] text-text-muted dark:text-gray-500">
                        +{plugin.capabilities.length - 3} more
                      </span>
                    )}
                  </div>
                </Card>
              )
            })}
          </div>
        )}

        {/* Detail Modal */}
        <Modal
          open={detailModalOpen}
          onClose={handleCloseDetail}
          title={selectedPlugin?.name || 'Plugin Detail'}
          size="lg"
        >
          {detailLoading && !selectedPlugin && (
            <div className="space-y-4">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-20 w-full" variant="rectangular" />
            </div>
          )}

          {selectedPlugin && (
            <div className="space-y-5">
              {/* Status + Version */}
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant={pluginStatusVariant(selectedPlugin.status)} size="md" dot>
                  {selectedPlugin.status}
                </Badge>
                {selectedPlugin.version && (
                  <Badge variant="default" size="md">
                    v{selectedPlugin.version}
                  </Badge>
                )}
                {selectedPlugin.author && (
                  <span className="text-xs text-text-muted dark:text-gray-400">
                    by {selectedPlugin.author}
                  </span>
                )}
              </div>

              {/* Description */}
              <div>
                <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-1">
                  Description
                </h4>
                <p className="text-sm text-text-primary dark:text-gray-200">
                  {selectedPlugin.description || 'No description available'}
                </p>
              </div>

              {/* Homepage */}
              {selectedPlugin.homepage && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-1">
                    Homepage
                  </h4>
                  <a
                    href={selectedPlugin.homepage}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {selectedPlugin.homepage}
                  </a>
                </div>
              )}

              {/* Capabilities */}
              {selectedPlugin.capabilities && selectedPlugin.capabilities.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Capabilities
                  </h4>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedPlugin.capabilities.map((cap) => (
                      <Badge key={cap} variant="info" size="md">
                        {cap}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Registered Hooks */}
              {selectedPlugin.hooks && selectedPlugin.hooks.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Registered Hooks ({selectedPlugin.hooks.length})
                  </h4>
                  <div className="space-y-1">
                    {selectedPlugin.hooks.map((hook) => (
                      <div
                        key={hook}
                        className={cn(
                          'flex items-center gap-2 px-3 py-2 rounded-lg',
                          'bg-[#fafbfc] border border-[#e5e7eb]',
                          'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45]',
                        )}
                      >
                        <span className="text-purple-500 dark:text-purple-400">
                          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m9.86-2.556a4.5 4.5 0 00-1.242-7.244l-4.5-4.5a4.5 4.5 0 00-6.364 6.364L4.47 8.88" />
                          </svg>
                        </span>
                        <code className="text-xs font-mono text-text-dark dark:text-gray-200">
                          {hook}
                        </code>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Config (redacted) */}
              {selectedPlugin.config && Object.keys(selectedPlugin.config).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Configuration (redacted)
                  </h4>
                  <div
                    className={cn(
                      'rounded-lg border overflow-hidden',
                      'border-[#e5e7eb] dark:border-[#2a2a45]',
                    )}
                  >
                    <table className="w-full text-xs">
                      <tbody className="divide-y divide-[#e5e7eb] dark:divide-[#2a2a45]">
                        {Object.entries(selectedPlugin.config).map(([key, value]) => (
                          <tr key={key}>
                            <td className="px-3 py-2 font-mono font-medium text-text-dark dark:text-gray-200 bg-[#fafbfc] dark:bg-[#1a1a2e]/50 w-1/3">
                              {key}
                            </td>
                            <td className="px-3 py-2 font-mono text-text-muted dark:text-gray-400">
                              {redactConfigValue(value)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Manifest */}
              {selectedPlugin.manifest && Object.keys(selectedPlugin.manifest).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Manifest
                  </h4>
                  <pre
                    className={cn(
                      'text-xs p-3 rounded-lg overflow-x-auto max-h-48',
                      'bg-gray-50 text-gray-700',
                      'dark:bg-[#1a1a2e] dark:text-gray-300',
                    )}
                  >
                    {JSON.stringify(selectedPlugin.manifest, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </Modal>
      </div>
    </div>
  )
}
