// ============================================================
// Nexus - Plugin UI System (Barrel Export)
// Feature #10: Dynamic Route/Tab Injection
// ============================================================

// Types
export type {
  PluginSlotName,
  PluginRoute,
  PluginSlotEntry,
  PluginManifest,
} from './types'

// Registry (Zustand store)
export { usePluginRegistry } from './registry'

// Components
export { PluginSlot } from './slots'
export { PluginRoutes } from './PluginRoutes'
export { PluginErrorBoundary } from './PluginErrorBoundary'

// Built-in plugins
export { registerBuiltinPlugins } from './builtins'
