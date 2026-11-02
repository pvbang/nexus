// ============================================================
// Nexus - Plugin UI Type Definitions
// Feature #10: Dynamic Route/Tab Injection
// ============================================================

import type { ComponentType } from 'react'

/**
 * Predefined UI slot locations where plugins can inject content.
 * Each slot is a specific area in the app layout.
 */
export type PluginSlotName =
  | 'chat-toolbar'          // buttons in chat toolbar area
  | 'chat-sidebar-top'      // top of conversation sidebar
  | 'chat-sidebar-bottom'   // bottom of conversation sidebar
  | 'settings-section'      // additional settings sections
  | 'process-panel-tab'     // tabs in process panel
  | 'header-right'          // right side of header bar
  | 'message-action'        // actions on message bubbles
  | 'global-footer'         // bottom of page

/**
 * A route registered by a plugin, shown as a navigable page.
 */
export interface PluginRoute {
  /** Unique route identifier */
  id: string
  /** URL path, e.g., '/plugins/my-plugin' */
  path: string
  /** Display label for navigation */
  label: string
  /** Optional icon component (lucide-react compatible) */
  icon?: ComponentType<{ className?: string }>
  /** Page component to render at this route */
  component: ComponentType
  /** Where to show in nav: 'main' tabs, 'admin' section, or 'tool' area */
  category?: 'main' | 'admin' | 'tool'
}

/**
 * A UI component injected into a named slot by a plugin.
 */
export interface PluginSlotEntry {
  /** Unique entry identifier */
  id: string
  /** Plugin that owns this entry */
  pluginId: string
  /** Target slot location */
  slot: PluginSlotName
  /** Component to render in the slot */
  component: ComponentType<Record<string, unknown>>
  /** Render priority — lower numbers render first (default: 100) */
  priority?: number
}

/**
 * Full plugin manifest describing a registered UI plugin.
 */
export interface PluginManifest {
  /** Unique plugin identifier */
  id: string
  /** Human-readable name */
  name: string
  /** Semantic version string */
  version: string
  /** Short description */
  description: string
  /** Plugin author */
  author?: string
  /** Whether the plugin is currently enabled */
  enabled: boolean
  /** Routes this plugin registers */
  routes?: PluginRoute[]
  /** Slot entries this plugin injects */
  slots?: PluginSlotEntry[]
}
