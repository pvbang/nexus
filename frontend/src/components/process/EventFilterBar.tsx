// ============================================================
// MekongAI Nexus - Event Filter Bar
// Quick filters for event timeline: type, agent, search
// ============================================================

import { useState, useCallback } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface EventFilters {
  types: Set<string>
  agentName: string
  searchText: string
}

interface EventFilterBarProps {
  filters: EventFilters
  onFiltersChange: (filters: EventFilters) => void
  totalEvents: number
  filteredCount: number
  className?: string
}

const QUICK_FILTERS = [
  { label: 'Tools', types: ['tool_start', 'tool_done'], icon: '🔧' },
  { label: 'Debates', types: ['debate_proposal', 'debate_rebuttal', 'debate_verdict', 'debate_critique', 'debate_revision'], icon: '💬' },
  { label: 'Steps', types: ['step'], icon: '⚡' },
  { label: 'Agents', types: ['agent_spawn', 'agent_thinking'], icon: '🤖' },
  { label: 'Errors', types: ['system_warning', 'task_crashed'], icon: '⚠️' },
]

export function EventFilterBar({
  filters,
  onFiltersChange,
  totalEvents,
  filteredCount,
  className,
}: EventFilterBarProps) {
  const [showSearch, setShowSearch] = useState(false)

  const toggleQuickFilter = useCallback((types: string[]) => {
    const newTypes = new Set(filters.types)
    const allActive = types.every((t) => newTypes.has(t))
    
    if (allActive) {
      // Remove all types of this filter
      types.forEach((t) => newTypes.delete(t))
    } else {
      // Add all types of this filter
      types.forEach((t) => newTypes.add(t))
    }
    
    onFiltersChange({ ...filters, types: newTypes })
  }, [filters, onFiltersChange])

  const clearFilters = useCallback(() => {
    onFiltersChange({ types: new Set(), agentName: '', searchText: '' })
    setShowSearch(false)
  }, [onFiltersChange])

  const hasActiveFilters = filters.types.size > 0 || filters.searchText || filters.agentName

  return (
    <div className={cn('flex items-center gap-1 flex-wrap', className)}>
      {/* Quick filter buttons */}
      {QUICK_FILTERS.map((qf) => {
        const isActive = qf.types.every((t) => filters.types.has(t))
        return (
          <button
            key={qf.label}
            onClick={() => toggleQuickFilter(qf.types)}
            className={cn(
              'px-2 py-0.5 rounded text-xs transition-colors',
              isActive
                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40'
                : 'bg-white/5 text-muted-foreground hover:bg-white/10 border border-transparent',
            )}
          >
            {qf.icon} {qf.label}
          </button>
        )
      })}

      {/* Search toggle */}
      <button
        onClick={() => setShowSearch(!showSearch)}
        className={cn(
          'p-1 rounded transition-colors',
          showSearch ? 'bg-blue-500/20 text-blue-300' : 'text-muted-foreground hover:bg-white/10',
        )}
      >
        <Search className="w-3.5 h-3.5" />
      </button>

      {/* Search input */}
      {showSearch && (
        <input
          type="text"
          placeholder="Search events..."
          value={filters.searchText}
          onChange={(e) => onFiltersChange({ ...filters, searchText: e.target.value })}
          className="px-2 py-0.5 text-xs rounded bg-white/5 border border-white/10 
                     text-foreground placeholder:text-muted-foreground w-32
                     focus:outline-none focus:border-blue-500/50"
          autoFocus
        />
      )}

      {/* Clear all filters */}
      {hasActiveFilters && (
        <button
          onClick={clearFilters}
          className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs
                     text-red-300 hover:bg-red-500/10 transition-colors"
        >
          <X className="w-3 h-3" />
          Clear
        </button>
      )}

      {/* Count indicator */}
      {hasActiveFilters && (
        <span className="text-xs text-muted-foreground ml-auto">
          {filteredCount}/{totalEvents}
        </span>
      )}
    </div>
  )
}

/** Default empty filters */
export function createEmptyFilters(): EventFilters {
  return { types: new Set(), agentName: '', searchText: '' }
}

/** Apply filters to event list */
export function applyEventFilters(
  events: Array<{ type: string; [key: string]: unknown }>,
  filters: EventFilters,
): Array<{ type: string; [key: string]: unknown }> {
  if (filters.types.size === 0 && !filters.searchText && !filters.agentName) {
    return events
  }

  return events.filter((evt) => {
    // Type filter
    if (filters.types.size > 0 && !filters.types.has(evt.type)) {
      return false
    }
    // Agent name filter
    if (filters.agentName) {
      const agentName = (evt as any).agent_name || (evt as any).agentName || ''
      if (!agentName.toLowerCase().includes(filters.agentName.toLowerCase())) {
        return false
      }
    }
    // Search text filter
    if (filters.searchText) {
      const searchLower = filters.searchText.toLowerCase()
      const content = JSON.stringify(evt).toLowerCase()
      if (!content.includes(searchLower)) {
        return false
      }
    }
    return true
  })
}
