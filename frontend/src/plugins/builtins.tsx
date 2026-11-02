// ============================================================
// Nexus - Built-in Demo Plugins
// Feature #10: Registers demo plugins to prove the system works
// ============================================================

import { useState, useEffect } from 'react'
import { usePluginRegistry } from './registry'
import type { PluginManifest } from './types'

// ── Demo: System Info Page Component ──────────────────────────

function SystemInfoPage() {
  const [info, setInfo] = useState<Record<string, string>>({})

  useEffect(() => {
    setInfo({
      'User Agent': navigator.userAgent,
      Language: navigator.language,
      Platform: navigator.platform,
      'Screen': `${screen.width}×${screen.height}`,
      'Window': `${window.innerWidth}×${window.innerHeight}`,
      'Color Depth': `${screen.colorDepth}-bit`,
      'Device Pixel Ratio': String(window.devicePixelRatio),
      'Online': String(navigator.onLine),
      Timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      'Current Time': new Date().toISOString(),
    })
  }, [])

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[800px] mx-auto px-4 sm:px-6 py-8">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-[12px] bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center">
            <span className="text-lg">🖥️</span>
          </div>
          <div>
            <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
              System Info
            </h2>
            <p className="text-[13px] text-text-muted dark:text-gray-400">
              Browser and environment information (demo plugin)
            </p>
          </div>
        </div>

        <div className="rounded-[16px] border border-[#e5e7eb] dark:border-[#2a2a45] overflow-hidden">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-[#e5e7eb] dark:divide-[#2a2a45]">
              {Object.entries(info).map(([key, value]) => (
                <tr key={key}>
                  <td className="px-4 py-3 font-medium text-text-dark dark:text-gray-200 bg-[#fafbfc] dark:bg-[#1a1a2e]/50 w-1/3">
                    {key}
                  </td>
                  <td className="px-4 py-3 font-mono text-[13px] text-text-secondary dark:text-gray-400 break-all">
                    {value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-4 text-[12px] text-text-muted dark:text-gray-500 text-center">
          This page is rendered by the{' '}
          <code className="bg-[#f4f5f7] dark:bg-[#1a1a2e] px-1.5 py-0.5 rounded text-[11px]">
            system-info
          </code>{' '}
          demo plugin via the Plugin UI System.
        </p>
      </div>
    </div>
  )
}

// ── Demo: System Info Icon ────────────────────────────────────

function MonitorIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  )
}

// ── Demo: Chat Share Slot Button ──────────────────────────────

function ChatShareSlotButton() {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    const url = window.location.href
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // Clipboard API not available — silent
    })
  }

  return (
    <button
      onClick={handleCopy}
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[11px] font-medium transition-colors bg-white border border-[#e5e7eb] text-text-secondary hover:border-[#bfdbfe] hover:text-text-dark dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:text-gray-400 dark:hover:border-[#3b82f6]/40 dark:hover:text-gray-200"
      title="Copy chat URL (demo plugin)"
    >
      <svg
        className="w-3 h-3"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </svg>
      <span>{copied ? 'Copied!' : 'Share'}</span>
    </button>
  )
}

// ── Manifest definitions ──────────────────────────────────────

const SYSTEM_INFO_PLUGIN: PluginManifest = {
  id: 'builtin-system-info',
  name: 'System Info',
  version: '1.0.0',
  description: 'Shows browser and environment information',
  author: 'Nexus',
  enabled: true,
  routes: [
    {
      id: 'system-info-page',
      path: '/plugins/system-info',
      label: 'System Info',
      icon: MonitorIcon,
      component: SystemInfoPage,
      category: 'tool',
    },
  ],
  slots: [],
}

const CHAT_SHARE_PLUGIN: PluginManifest = {
  id: 'builtin-chat-share',
  name: 'Chat Share',
  version: '1.0.0',
  description: 'Adds a share button to the chat toolbar',
  author: 'Nexus',
  enabled: true,
  routes: [],
  slots: [
    {
      id: 'chat-share-toolbar-btn',
      pluginId: 'builtin-chat-share',
      slot: 'chat-toolbar',
      component: ChatShareSlotButton,
      priority: 50,
    },
  ],
}

// ── Registration function ─────────────────────────────────────

/**
 * Register built-in demo plugins.
 * Call once on app mount (idempotent — re-registering overwrites).
 */
export function registerBuiltinPlugins(): void {
  const { registerPlugin } = usePluginRegistry.getState()
  registerPlugin(SYSTEM_INFO_PLUGIN)
  registerPlugin(CHAT_SHARE_PLUGIN)
}
