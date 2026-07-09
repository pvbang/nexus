// ============================================================
// MekongAI Nexus - Plugin Registry (Zustand Store)
// Feature #10: Manages registered plugins, routes, and slots
// ============================================================

import { create } from 'zustand'
import type {
  PluginManifest,
  PluginRoute,
  PluginSlotEntry,
  PluginSlotName,
} from './types'

// ── All known slot names for initializing empty arrays ────────

const ALL_SLOT_NAMES: PluginSlotName[] = [
  'chat-toolbar',
  'chat-sidebar-top',
  'chat-sidebar-bottom',
  'settings-section',
  'process-panel-tab',
  'header-right',
  'message-action',
  'global-footer',
]

function createEmptySlots(): Record<PluginSlotName, PluginSlotEntry[]> {
  const slots = {} as Record<PluginSlotName, PluginSlotEntry[]>
  for (const name of ALL_SLOT_NAMES) {
    slots[name] = []
  }
  return slots
}

// ── Store Interface ───────────────────────────────────────────

interface PluginRegistryState {
  /** All registered plugins keyed by plugin ID */
  plugins: Map<string, PluginManifest>
  /** Aggregated routes from all enabled plugins */
  routes: PluginRoute[]
  /** Aggregated slot entries grouped by slot name, sorted by priority */
  slots: Record<PluginSlotName, PluginSlotEntry[]>

  // Actions
  registerPlugin: (manifest: PluginManifest) => void
  unregisterPlugin: (pluginId: string) => void
  enablePlugin: (pluginId: string) => void
  disablePlugin: (pluginId: string) => void
  getSlotEntries: (slot: PluginSlotName) => PluginSlotEntry[]
  getRoutes: () => PluginRoute[]
}

// ── Helpers ───────────────────────────────────────────────────

/** Rebuild aggregated routes from all enabled plugins */
function rebuildRoutes(plugins: Map<string, PluginManifest>): PluginRoute[] {
  const routes: PluginRoute[] = []
  for (const manifest of plugins.values()) {
    if (manifest.enabled && manifest.routes) {
      routes.push(...manifest.routes)
    }
  }
  return routes
}

/** Rebuild aggregated slots from all enabled plugins, sorted by priority */
function rebuildSlots(
  plugins: Map<string, PluginManifest>,
): Record<PluginSlotName, PluginSlotEntry[]> {
  const slots = createEmptySlots()
  for (const manifest of plugins.values()) {
    if (manifest.enabled && manifest.slots) {
      for (const entry of manifest.slots) {
        slots[entry.slot].push(entry)
      }
    }
  }
  // Sort each slot by priority (lower = first)
  for (const name of ALL_SLOT_NAMES) {
    slots[name].sort((a, b) => (a.priority ?? 100) - (b.priority ?? 100))
  }
  return slots
}

// ── Store ─────────────────────────────────────────────────────

export const usePluginRegistry = create<PluginRegistryState>((set, get) => ({
  plugins: new Map(),
  routes: [],
  slots: createEmptySlots(),

  registerPlugin: (manifest) => {
    set((state) => {
      const plugins = new Map(state.plugins)
      plugins.set(manifest.id, manifest)
      return {
        plugins,
        routes: rebuildRoutes(plugins),
        slots: rebuildSlots(plugins),
      }
    })
  },

  unregisterPlugin: (pluginId) => {
    set((state) => {
      const plugins = new Map(state.plugins)
      plugins.delete(pluginId)
      return {
        plugins,
        routes: rebuildRoutes(plugins),
        slots: rebuildSlots(plugins),
      }
    })
  },

  enablePlugin: (pluginId) => {
    set((state) => {
      const plugins = new Map(state.plugins)
      const manifest = plugins.get(pluginId)
      if (manifest) {
        plugins.set(pluginId, { ...manifest, enabled: true })
      }
      return {
        plugins,
        routes: rebuildRoutes(plugins),
        slots: rebuildSlots(plugins),
      }
    })
  },

  disablePlugin: (pluginId) => {
    set((state) => {
      const plugins = new Map(state.plugins)
      const manifest = plugins.get(pluginId)
      if (manifest) {
        plugins.set(pluginId, { ...manifest, enabled: false })
      }
      return {
        plugins,
        routes: rebuildRoutes(plugins),
        slots: rebuildSlots(plugins),
      }
    })
  },

  getSlotEntries: (slot) => {
    return get().slots[slot] ?? []
  },

  getRoutes: () => {
    return get().routes
  },
}))
