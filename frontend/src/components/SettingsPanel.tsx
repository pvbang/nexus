// ============================================================
// MekongAI Nexus - Settings Panel (Enhanced)
// Feature #7: Theme presets + accent color picker
// Feature #9: i18n Language selector
// ============================================================

import { useState, useEffect, useCallback } from 'react'
import { Settings, Monitor, Moon, Sun, Keyboard, Eye, Bell, Palette, Globe } from 'lucide-react'
import { usePreferenceStore } from '../stores/preferenceStore'
import { useNotificationCenterStore } from '../stores/notificationCenterStore'
import type { NotificationKind } from '../stores/notificationCenterStore'
import type { TransparencyLevel } from '../stores/preferenceStore'
import type { ThemePreset } from '../themes/presets'
import { THEME_PRESETS, ACCENT_SWATCHES } from '../themes'
import { listProfiles, listModels, apiGet, type ConversationProfile, type LLMModel } from '../api'
import { cn } from '../lib/cn'
import { Badge } from './ui/Badge'
import { Skeleton } from './ui/Skeleton'
import { useTranslation, LanguageSwitcher } from '../i18n'

// ============================================================
// Types
// ============================================================

interface SystemInfo {
  version?: string
  name?: string
  uptime?: string
  environment?: string
  python_version?: string
  [key: string]: unknown
}

// ============================================================
// Admin link definitions
// ============================================================

interface AdminLink {
  label: string
  description: string
  path: string
  icon: string
  color: string
  darkColor: string
}

const ADMIN_LINKS: AdminLink[] = [
  {
    label: 'Skills Hub',
    description: 'Browse and invoke AI skills',
    path: '/admin/skills',
    icon: '🧩',
    color: 'bg-purple-50',
    darkColor: 'dark:bg-purple-900/30',
  },
  {
    label: 'Cron Manager',
    description: 'Schedule recurring tasks',
    path: '/admin/cron',
    icon: '⏰',
    color: 'bg-amber-50',
    darkColor: 'dark:bg-amber-900/30',
  },
  {
    label: 'Plugin Manager',
    description: 'Manage installed plugins',
    path: '/admin/plugins',
    icon: '🔌',
    color: 'bg-blue-50',
    darkColor: 'dark:bg-blue-900/30',
  },
  {
    label: 'Batch Runner',
    description: 'Execute batch operations',
    path: '/admin/batch',
    icon: '🚀',
    color: 'bg-emerald-50',
    darkColor: 'dark:bg-emerald-900/30',
  },
]

// ============================================================
// Keyboard shortcuts
// ============================================================

const SHORTCUTS = [
  { keys: 'Ctrl + N', description: 'New conversation' },
  { keys: 'Ctrl + K', description: 'Search conversations' },
  { keys: 'Ctrl + /', description: 'Toggle sidebar' },
  { keys: 'Ctrl + .', description: 'Toggle progress panel' },
  { keys: 'Enter', description: 'Send message' },
  { keys: 'Shift + Enter', description: 'New line in message' },
  { keys: 'Escape', description: 'Cancel / Close' },
]

// ============================================================
// Theme options
// ============================================================

type ThemeValue = 'light' | 'dark' | 'system'

interface ThemeOption {
  value: ThemeValue
  label: string
  icon: typeof Sun
}

const THEME_OPTIONS: ThemeOption[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

// ============================================================
// Theme Preset Preview Colors (for thumbnail cards)
// ============================================================

interface PresetPreview {
  id: ThemePreset
  name: string
  description: string
  colors: {
    bg: string
    secondary: string
    accent: string
    text: string
  }
}

const PRESET_PREVIEWS: PresetPreview[] = [
  {
    id: 'default',
    name: 'Default Blue',
    description: 'Clean professional look',
    colors: { bg: '#ffffff', secondary: '#f8f9fb', accent: '#3B82F6', text: '#222222' },
  },
  {
    id: 'hermes-gold',
    name: 'Hermes Gold',
    description: 'Warm gold accent',
    colors: { bg: '#fffdf7', secondary: '#faf6ee', accent: '#D97706', text: '#2d2518' },
  },
  {
    id: 'github-dark',
    name: 'GitHub Dark',
    description: 'GitHub-inspired green',
    colors: { bg: '#0d1117', secondary: '#161b22', accent: '#238636', text: '#e6edf3' },
  },
  {
    id: 'solarized',
    name: 'Solarized',
    description: 'Classic cyan scheme',
    colors: { bg: '#fdf6e3', secondary: '#eee8d5', accent: '#2AA198', text: '#073642' },
  },
]

// ============================================================
// Main Component
// ============================================================

const TRANSPARENCY_DESCRIPTIONS: Record<TransparencyLevel, string> = {
  minimal: 'Only progress steps',
  normal: 'Steps + tools + agents',
  detailed: 'All events including thinking',
  debug: 'Raw events with metadata',
}

// Notification type config for preferences UI
const NOTIFICATION_TYPE_OPTIONS: ReadonlyArray<{
  type: NotificationKind
  label: string
  icon: string
}> = [
  { type: 'task_complete', label: 'Task completed', icon: '✅' },
  { type: 'task_failed', label: 'Task failed', icon: '❌' },
  { type: 'agent_spawn', label: 'Agent spawned', icon: '🤖' },
  { type: 'swarm_done', label: 'Debate resolved', icon: '🗣️' },
  { type: 'tool_created', label: 'Tool created', icon: '🔧' },
  { type: 'xskill_learned', label: 'Skill learned', icon: '🧠' },
  { type: 'system', label: 'System events', icon: '⚙️' },
  { type: 'info', label: 'Info', icon: 'ℹ️' },
]

export function SettingsPanel() {
  const { t, locale } = useTranslation()
  const theme = usePreferenceStore((s) => s.theme)
  const setTheme = usePreferenceStore((s) => s.setTheme)
  const themePreset = usePreferenceStore((s) => s.themePreset)
  const setThemePreset = usePreferenceStore((s) => s.setThemePreset)
  const accentColor = usePreferenceStore((s) => s.accentColor)
  const setAccentColor = usePreferenceStore((s) => s.setAccentColor)
  const sidebarCollapsed = usePreferenceStore((s) => s.sidebarCollapsed)
  const setSidebarCollapsed = usePreferenceStore((s) => s.setSidebarCollapsed)
  const rightPanelCollapsed = usePreferenceStore((s) => s.rightPanelCollapsed)
  const setRightPanelCollapsed = usePreferenceStore((s) => s.setRightPanelCollapsed)
  const transparencyLevel = usePreferenceStore((s) => s.transparencyLevel)
  const setTransparencyLevel = usePreferenceStore((s) => s.setTransparencyLevel)

  // Notification preferences
  const mutedTypes = useNotificationCenterStore((s) => s.mutedTypes)
  const muteType = useNotificationCenterStore((s) => s.muteType)
  const unmuteType = useNotificationCenterStore((s) => s.unmuteType)

  const [profiles, setProfiles] = useState<ConversationProfile[]>([])
  const [models, setModels] = useState<LLMModel[]>([])
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [loadingProfiles, setLoadingProfiles] = useState(true)
  const [loadingModels, setLoadingModels] = useState(true)
  const [loadingSystem, setLoadingSystem] = useState(true)

  // Fetch data on mount
  useEffect(() => {
    listProfiles()
      .then(setProfiles)
      .catch(() => {})
      .finally(() => setLoadingProfiles(false))

    listModels()
      .then(setModels)
      .catch(() => {})
      .finally(() => setLoadingModels(false))

    apiGet<SystemInfo>('/system/info')
      .then(setSystemInfo)
      .catch(() => {})
      .finally(() => setLoadingSystem(false))
  }, [])

  // Navigate to admin page
  const navigateTo = useCallback((path: string) => {
    // Use hash routing or dispatch custom event for SPA navigation
    window.dispatchEvent(new CustomEvent('nexus:navigate', { detail: { path } }))
  }, [])

  /** Handle preset selection */
  const handlePresetSelect = useCallback(
    (preset: ThemePreset) => {
      if (preset === 'custom') return
      setThemePreset(preset)
      // Update accent color to match preset default
      const config = THEME_PRESETS[preset]
      if (config) {
        setAccentColor(config.light.accentPrimary)
      }
    },
    [setThemePreset, setAccentColor],
  )

  /** Handle custom accent selection */
  const handleCustomAccent = useCallback(
    (color: string) => {
      setAccentColor(color)
      setThemePreset('custom')
    },
    [setAccentColor, setThemePreset],
  )

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[700px] mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* Header */}
        <div className="animate-fade-in">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'w-10 h-10 rounded-[12px] flex items-center justify-center',
                'bg-[#f4f5f7] dark:bg-[#2a2a45]',
              )}
            >
              <Settings className="w-5 h-5 text-text-secondary dark:text-gray-400" />
            </div>
            <div>
              <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
                {t('settings.title')}
              </h2>
              <p className="text-[13px] text-text-muted dark:text-gray-400">
                Customize your MekongAI Nexus experience
              </p>
            </div>
          </div>
        </div>

        {/* Appearance */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-4">
            {t('settings.appearance')}
          </h3>

          <div className="space-y-4">
            {/* Theme */}
            <div>
              <label className="text-[12px] font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2 block">
                {t('settings.themeMode')}
              </label>
              <div className="flex gap-2">
                {THEME_OPTIONS.map((opt) => {
                  const Icon = opt.icon
                  const isActive = theme === opt.value
                  return (
                    <button
                      key={opt.value}
                      onClick={() => setTheme(opt.value)}
                      className={cn(
                        'flex items-center gap-2 px-4 py-2.5 rounded-[10px] text-[13px] font-medium transition-all border',
                        isActive
                          ? [
                              'bg-[#eff6ff] text-[#1d4ed8] border-[#bfdbfe]',
                              'dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
                            ]
                          : [
                              'bg-[#f4f5f7] text-text-secondary border-transparent',
                              'hover:bg-[#e5e7eb]',
                              'dark:bg-[#2a2a45] dark:text-gray-300 dark:hover:bg-[#353560]',
                            ],
                      )}
                    >
                      <Icon className="w-4 h-4" />
                      {opt.label}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Layout */}
            <div>
              <label className="text-[12px] font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2 block">
                Layout
              </label>
              <div className="space-y-2">
                <label
                  className={cn(
                    'flex items-center justify-between p-3 rounded-[10px] cursor-pointer border transition-colors',
                    'bg-[#fafbfc] border-[#e5e7eb] hover:border-[#bfdbfe]',
                    'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45] dark:hover:border-[#353560]',
                  )}
                >
                  <span className="text-[13px] text-text-primary dark:text-gray-200">
                    Show sidebar by default
                  </span>
                  <input
                    type="checkbox"
                    checked={!sidebarCollapsed}
                    onChange={(e) => setSidebarCollapsed(!e.target.checked)}
                    className="w-4 h-4 rounded border-gray-300 text-[#3b82f6] focus:ring-[#3b82f6] dark:border-gray-600"
                  />
                </label>
                <label
                  className={cn(
                    'flex items-center justify-between p-3 rounded-[10px] cursor-pointer border transition-colors',
                    'bg-[#fafbfc] border-[#e5e7eb] hover:border-[#bfdbfe]',
                    'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45] dark:hover:border-[#353560]',
                  )}
                >
                  <span className="text-[13px] text-text-primary dark:text-gray-200">
                    Show progress panel by default
                  </span>
                  <input
                    type="checkbox"
                    checked={!rightPanelCollapsed}
                    onChange={(e) => setRightPanelCollapsed(!e.target.checked)}
                    className="w-4 h-4 rounded border-gray-300 text-[#3b82f6] focus:ring-[#3b82f6] dark:border-gray-600"
                  />
                </label>
              </div>
            </div>
          </div>
        </section>

        {/* ── Feature #9: Language ── */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-1 flex items-center gap-2">
            <Globe className="w-4 h-4 text-text-muted dark:text-gray-400" />
            {t('settings.language')}
          </h3>
          <p className="text-[12px] text-text-muted dark:text-gray-400 mb-3">
            {locale === 'vi' ? 'Chọn ngôn ngữ hiển thị' : 'Choose display language'}
          </p>
          <LanguageSwitcher />
        </section>

        {/* ── Feature #7: Theme Presets ── */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-1 flex items-center gap-2">
            <Palette className="w-4 h-4 text-text-muted dark:text-gray-400" />
            {t('settings.themePreset')}
          </h3>
          <p className="text-[12px] text-text-muted dark:text-gray-400 mb-4">
            Choose a color scheme for your workspace
          </p>

          {/* Preset Cards Grid */}
          <div className="grid grid-cols-2 gap-3">
            {PRESET_PREVIEWS.map((preset) => {
              const isActive =
                themePreset === preset.id ||
                (themePreset === 'custom' && preset.id === 'default')
                  ? themePreset === preset.id
                  : false
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => handlePresetSelect(preset.id)}
                  className={cn(
                    'group relative rounded-[12px] border-2 p-3 text-left transition-all',
                    isActive
                      ? 'border-[var(--color-accent)] shadow-md'
                      : [
                          'border-[#e5e7eb] hover:border-[#d1d5db]',
                          'dark:border-[#2a2a45] dark:hover:border-[#3a3a55]',
                        ],
                  )}
                >
                  {/* Color preview thumbnail */}
                  <div className="flex gap-1.5 mb-2.5">
                    <div
                      className="w-8 h-8 rounded-[6px] border border-black/10"
                      style={{ backgroundColor: preset.colors.bg }}
                    />
                    <div
                      className="w-8 h-8 rounded-[6px] border border-black/10"
                      style={{ backgroundColor: preset.colors.secondary }}
                    />
                    <div
                      className="w-8 h-8 rounded-[6px] border border-black/10"
                      style={{ backgroundColor: preset.colors.accent }}
                    />
                    <div
                      className="w-8 h-8 rounded-[6px] border border-black/10"
                      style={{ backgroundColor: preset.colors.text }}
                    />
                  </div>
                  <p className="text-[13px] font-medium text-text-dark dark:text-gray-100">
                    {preset.name}
                  </p>
                  <p className="text-[11px] text-text-muted dark:text-gray-400 mt-0.5">
                    {preset.description}
                  </p>
                  {/* Active indicator */}
                  {isActive && (
                    <div
                      className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center"
                      style={{ backgroundColor: 'var(--color-accent)' }}
                    >
                      <svg className="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
                        <path
                          d="M2.5 6L5 8.5L9.5 3.5"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          {/* ── Accent Color (Custom) ── */}
          <div className="mt-5 pt-4 border-t border-[#e5e7eb] dark:border-[#2a2a45]">
            <div className="flex items-center justify-between mb-3">
              <div>
                <label className="text-[12px] font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider block">
                  Custom Accent Color
                </label>
                <p className="text-[11px] text-text-muted dark:text-gray-500 mt-0.5">
                  {themePreset === 'custom' ? 'Active — using your custom color' : 'Pick a color to enable custom theme'}
                </p>
              </div>
              {themePreset === 'custom' && (
                <Badge variant="info" size="sm">Custom</Badge>
              )}
            </div>

            {/* Preset accent swatches */}
            <div className="flex items-center gap-2 mb-3">
              {ACCENT_SWATCHES.map((swatch) => (
                <button
                  key={swatch.color}
                  type="button"
                  onClick={() => handleCustomAccent(swatch.color)}
                  title={swatch.name}
                  className={cn(
                    'w-7 h-7 rounded-full border-2 transition-all hover:scale-110',
                    themePreset === 'custom' && accentColor === swatch.color
                      ? 'border-text-dark dark:border-white scale-110 ring-2 ring-offset-1 ring-black/10 dark:ring-white/20'
                      : 'border-transparent hover:border-gray-300 dark:hover:border-gray-600',
                  )}
                  style={{ backgroundColor: swatch.color }}
                />
              ))}
            </div>

            {/* Color picker input */}
            <div className="flex items-center gap-3">
              <div className="relative">
                <input
                  type="color"
                  value={themePreset === 'custom' ? accentColor : '#3B82F6'}
                  onChange={(e) => handleCustomAccent(e.target.value)}
                  className="w-10 h-10 rounded-[8px] border border-[#e5e7eb] dark:border-[#2a2a45] cursor-pointer bg-transparent p-0.5"
                />
              </div>
              <div className="flex-1">
                <input
                  type="text"
                  value={themePreset === 'custom' ? accentColor : ''}
                  placeholder="#3B82F6"
                  onChange={(e) => {
                    const val = e.target.value
                    if (/^#[0-9A-Fa-f]{6}$/.test(val)) {
                      handleCustomAccent(val)
                    }
                  }}
                  className={cn(
                    'w-full px-3 py-2 rounded-[8px] border text-[13px] font-mono transition-colors',
                    'border-[#e5e7eb] bg-[#fafbfc] text-text-primary',
                    'dark:border-[#2a2a45] dark:bg-[#1a1a2e]/50 dark:text-gray-200',
                    'focus:outline-none focus:border-[var(--color-accent)] focus:ring-1 focus:ring-[var(--color-accent-ring)]',
                  )}
                />
              </div>
              {themePreset === 'custom' && (
                <button
                  type="button"
                  onClick={() => handlePresetSelect('default')}
                  className="text-[12px] text-text-muted dark:text-gray-400 hover:text-text-primary dark:hover:text-gray-200 underline shrink-0"
                >
                  Reset
                </button>
              )}
            </div>
          </div>
        </section>

        {/* Process Transparency */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-1 flex items-center gap-2">
            <Eye className="w-4 h-4 text-text-muted dark:text-gray-400" />
            {t('settings.transparency')}
          </h3>
          <p className="text-[12px] text-text-muted dark:text-gray-400 mb-3">
            Control how much process detail is shown in chat
          </p>
          <div className="grid grid-cols-2 gap-2">
            {(['minimal', 'normal', 'detailed', 'debug'] as const).map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setTransparencyLevel(level)}
                className={cn(
                  'px-3 py-2 rounded-[10px] border text-left transition-all',
                  transparencyLevel === level
                    ? [
                        'border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]',
                        'dark:border-blue-400 dark:bg-blue-900/30 dark:text-blue-300',
                      ]
                    : [
                        'border-[#e5e7eb] bg-[#fafbfc] text-text-secondary',
                        'hover:border-[#d1d5db]',
                        'dark:border-[#2a2a45] dark:bg-[#1a1a2e]/50 dark:text-gray-400',
                        'dark:hover:border-[#353560]',
                      ],
                )}
              >
                <span className="block text-[13px] font-semibold capitalize">{level}</span>
                <span className="block text-[11px] text-text-muted dark:text-gray-500 mt-0.5">
                  {TRANSPARENCY_DESCRIPTIONS[level]}
                </span>
              </button>
            ))}
          </div>
        </section>

        {/* Notification Preferences */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-1 flex items-center gap-2">
            <Bell className="w-4 h-4 text-text-muted dark:text-gray-400" />
            Notification Preferences
          </h3>
          <p className="text-[12px] text-text-muted dark:text-gray-400 mb-3">
            Choose which notifications to receive
          </p>
          <div className="space-y-1">
            {NOTIFICATION_TYPE_OPTIONS.map(({ type, label, icon }) => (
              <label
                key={type}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 rounded-[10px] cursor-pointer transition-colors',
                  'hover:bg-[#f4f5f7]',
                  'dark:hover:bg-[#2a2a45]/50',
                )}
              >
                <span className="text-base shrink-0">{icon}</span>
                <span className="text-[13px] text-text-primary dark:text-gray-300 flex-1">
                  {label}
                </span>
                <input
                  type="checkbox"
                  checked={!mutedTypes.includes(type)}
                  onChange={() => {
                    if (mutedTypes.includes(type)) {
                      unmuteType(type)
                    } else {
                      muteType(type)
                    }
                  }}
                  className="w-4 h-4 rounded border-gray-300 text-[#3b82f6] focus:ring-[#3b82f6] dark:border-gray-600"
                />
              </label>
            ))}
          </div>
        </section>

        {/* Admin Quick Links */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-4">
            Admin & Management
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {ADMIN_LINKS.map((link) => (
              <button
                key={link.path}
                type="button"
                onClick={() => navigateTo(link.path)}
                className={cn(
                  'flex items-center gap-3 p-3 rounded-[10px] text-left transition-all border',
                  'bg-[#fafbfc] border-[#e5e7eb]',
                  'hover:border-[#bfdbfe] hover:shadow-sm',
                  'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45]',
                  'dark:hover:border-[#353560] dark:hover:bg-[#2a2a45]/50',
                )}
              >
                <div
                  className={cn(
                    'w-9 h-9 rounded-[8px] flex items-center justify-center shrink-0',
                    link.color,
                    link.darkColor,
                  )}
                >
                  <span className="text-base" role="img" aria-hidden="true">
                    {link.icon}
                  </span>
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-text-dark dark:text-gray-100">
                    {link.label}
                  </p>
                  <p className="text-[11px] text-text-muted dark:text-gray-400 truncate">
                    {link.description}
                  </p>
                </div>
                <svg
                  className="w-4 h-4 ml-auto shrink-0 text-text-muted dark:text-gray-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </button>
            ))}
          </div>
        </section>

        {/* Available Profiles */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100">
              Conversation Profiles
            </h3>
            {profiles.length > 0 && (
              <Badge variant="default" size="sm">
                {profiles.length}
              </Badge>
            )}
          </div>

          {loadingProfiles && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="p-3 rounded-[10px] bg-[#fafbfc] dark:bg-[#1a1a2e]/50 border border-[#e5e7eb] dark:border-[#2a2a45]">
                  <Skeleton className="h-4 w-24 mb-1" />
                  <Skeleton className="h-3 w-full" />
                </div>
              ))}
            </div>
          )}

          {!loadingProfiles && profiles.length === 0 && (
            <p className="text-[12px] text-text-muted dark:text-gray-400">
              No profiles configured. Profiles customize agent behavior per conversation.
            </p>
          )}

          {!loadingProfiles && profiles.length > 0 && (
            <>
              <p className="text-[12px] text-text-muted dark:text-gray-400 mb-3">
                Apply profiles to customize agent behavior per session
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {profiles.map((p) => (
                  <div
                    key={p.name}
                    className={cn(
                      'p-3 rounded-[10px] border',
                      'bg-[#fafbfc] border-[#e5e7eb]',
                      'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45]',
                    )}
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <p className="text-[13px] font-medium text-text-dark dark:text-gray-100">
                        {p.name}
                      </p>
                      {p.temperature !== undefined && (
                        <Badge variant="default" size="sm">
                          temp: {p.temperature}
                        </Badge>
                      )}
                    </div>
                    <p className="text-[11px] text-text-muted dark:text-gray-400">
                      {p.description}
                    </p>
                    {p.tools && p.tools.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {p.tools.slice(0, 3).map((t) => (
                          <Badge key={t} variant="info" size="sm">
                            {t}
                          </Badge>
                        ))}
                        {p.tools.length > 3 && (
                          <span className="text-[10px] text-text-muted dark:text-gray-500">
                            +{p.tools.length - 3}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </section>

        {/* Available Models */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100">
              Available LLM Models
            </h3>
            {models.length > 0 && (
              <Badge variant="default" size="sm">
                {models.length}
              </Badge>
            )}
          </div>

          {loadingModels && (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-[10px] bg-[#fafbfc] dark:bg-[#1a1a2e]/50 border border-[#e5e7eb] dark:border-[#2a2a45]">
                  <Skeleton className="h-8 w-8" variant="rectangular" />
                  <div className="flex-1">
                    <Skeleton className="h-4 w-32 mb-1" />
                    <Skeleton className="h-3 w-48" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loadingModels && models.length === 0 && (
            <p className="text-[12px] text-text-muted dark:text-gray-400">
              No models configured. Check your LLM provider settings.
            </p>
          )}

          {!loadingModels && models.length > 0 && (
            <div className="space-y-2">
              {models.map((m) => (
                <div
                  key={m.name}
                  className={cn(
                    'flex items-center gap-3 p-3 rounded-[10px] border',
                    'bg-[#fafbfc] border-[#e5e7eb]',
                    'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45]',
                  )}
                >
                  <div className="w-8 h-8 rounded-[8px] bg-gradient-to-br from-[#3b82f6] to-[#818cf8] flex items-center justify-center shrink-0">
                    <span className="text-[10px] text-white font-bold">
                      {m.provider.slice(0, 2).toUpperCase()}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-[13px] font-medium text-text-dark dark:text-gray-100">
                        {m.name}
                      </p>
                      <Badge variant="default" size="sm">
                        {m.provider}
                      </Badge>
                    </div>
                    <p className="text-[11px] text-text-muted dark:text-gray-400 truncate">
                      {m.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Keyboard Shortcuts */}
        <section
          className={cn(
            'rounded-[14px] p-5 border',
            'bg-white border-[#e5e7eb]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
          )}
        >
          <h3 className="text-[14px] font-semibold text-text-dark dark:text-gray-100 mb-4 flex items-center gap-2">
            <Keyboard className="w-4 h-4 text-text-muted dark:text-gray-400" />
            Keyboard Shortcuts
          </h3>
          <div className="space-y-1.5">
            {SHORTCUTS.map((s) => (
              <div key={s.keys} className="flex items-center justify-between py-1.5">
                <span className="text-[13px] text-text-secondary dark:text-gray-300">
                  {s.description}
                </span>
                <kbd
                  className={cn(
                    'text-[11px] font-mono rounded-[6px] px-2 py-1',
                    'bg-[#f4f5f7] border border-[#e5e7eb] text-text-muted',
                    'dark:bg-[#2a2a45] dark:border-[#353560] dark:text-gray-400',
                  )}
                >
                  {s.keys}
                </kbd>
              </div>
            ))}
          </div>
        </section>

        {/* About / Version */}
        <section className="text-center py-4 space-y-2">
          {loadingSystem && (
            <div className="flex justify-center">
              <Skeleton className="h-4 w-40" />
            </div>
          )}
          {!loadingSystem && systemInfo && (
            <div className="space-y-1">
              <p className="text-[12px] font-medium text-text-dark dark:text-gray-200">
                {systemInfo.name || 'MekongAI Nexus'}{' '}
                {systemInfo.version && (
                  <Badge variant="info" size="sm" className="ml-1">
                    v{systemInfo.version}
                  </Badge>
                )}
              </p>
              {systemInfo.environment && (
                <p className="text-[11px] text-text-muted dark:text-gray-400">
                  Environment: {systemInfo.environment}
                </p>
              )}
              {systemInfo.python_version && (
                <p className="text-[11px] text-text-muted dark:text-gray-400">
                  Python {systemInfo.python_version}
                </p>
              )}
              {systemInfo.uptime && (
                <p className="text-[11px] text-text-muted dark:text-gray-400">
                  Uptime: {systemInfo.uptime}
                </p>
              )}
            </div>
          )}
          {!loadingSystem && !systemInfo && (
            <div>
              <p className="text-[12px] text-text-muted dark:text-gray-400">
                MekongAI Nexus
              </p>
              <p className="text-[11px] text-text-muted dark:text-gray-400 mt-1">
                Multi-Agent AI Orchestration Platform
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
