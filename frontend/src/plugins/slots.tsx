// ============================================================
// Nexus - PluginSlot Component
// Feature #10: Renders plugin components into named UI slots
// ============================================================

import { Suspense } from 'react'
import { usePluginRegistry } from './registry'
import { PluginErrorBoundary } from './PluginErrorBoundary'
import type { PluginSlotName } from './types'

// ── Props ─────────────────────────────────────────────────────

interface PluginSlotProps {
  /** Which slot to render */
  name: PluginSlotName
  /** Content shown when no plugins registered for this slot */
  fallback?: React.ReactNode
  /** Additional CSS class for the wrapper */
  className?: string
  /** Wrapper element type */
  wrapper?: 'div' | 'span' | 'fragment'
}

// ── Component ─────────────────────────────────────────────────

export function PluginSlot({
  name,
  fallback = null,
  className,
  wrapper = 'div',
}: PluginSlotProps) {
  const entries = usePluginRegistry((s) => s.slots[name])

  // No entries → render fallback or nothing
  if (!entries || entries.length === 0) {
    return <>{fallback}</>
  }

  const children = entries.map((entry) => {
    const PluginComponent = entry.component
    return (
      <PluginErrorBoundary
        key={entry.id}
        pluginId={entry.pluginId}
        pluginName={entry.pluginId}
      >
        <Suspense
          fallback={
            <span className="inline-flex items-center gap-1 text-[11px] text-text-muted dark:text-gray-500">
              <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Loading...
            </span>
          }
        >
          <PluginComponent />
        </Suspense>
      </PluginErrorBoundary>
    )
  })

  // Fragment wrapper — no extra DOM node
  if (wrapper === 'fragment') {
    return <>{children}</>
  }

  // Span wrapper
  if (wrapper === 'span') {
    return (
      <span className={className} data-plugin-slot={name}>
        {children}
      </span>
    )
  }

  // Default: div wrapper
  return (
    <div className={className} data-plugin-slot={name}>
      {children}
    </div>
  )
}
