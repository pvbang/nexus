// ============================================================
// Nexus - Plugin Dynamic Routes
// Feature #10: Renders <Route> elements from registered plugins
// ============================================================

import { Suspense } from 'react'
import { Route } from 'react-router-dom'
import { usePluginRegistry } from './registry'
import { PluginErrorBoundary } from './PluginErrorBoundary'

// ── Loading fallback ──────────────────────────────────────────

function PluginPageLoader() {
  return (
    <div className="flex items-center justify-center h-full min-h-[200px]">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <span className="text-[13px] text-text-muted dark:text-gray-500 font-medium">
          Loading plugin...
        </span>
      </div>
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

/**
 * Renders dynamic Route elements from all enabled plugins.
 * Place inside <Routes> in App.tsx.
 *
 * Each plugin route is wrapped in:
 * - Suspense (for lazy-loaded components)
 * - PluginErrorBoundary (crash isolation)
 */
export function PluginRoutes() {
  const routes = usePluginRegistry((s) => s.routes)

  if (routes.length === 0) return null

  return (
    <>
      {routes.map((route) => {
        const PageComponent = route.component
        return (
          <Route
            key={route.id}
            path={route.path}
            element={
              <PluginErrorBoundary
                pluginId={route.id}
                pluginName={route.label}
              >
                <Suspense fallback={<PluginPageLoader />}>
                  <PageComponent />
                </Suspense>
              </PluginErrorBoundary>
            }
          />
        )
      })}
    </>
  )
}
