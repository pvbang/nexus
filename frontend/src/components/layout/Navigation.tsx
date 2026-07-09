// ============================================================
// MekongAI Nexus - Top navigation bar component
// Includes desktop nav, "More" dropdown, mobile menu, offline banner
// ============================================================

import { useState, memo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  MessageSquare, LayoutDashboard, Bot, ListTodo,
  Brain, FolderOpen, Share2, Menu, X, Zap, PanelRight,
  Settings, Sparkles, Activity, BarChart3, Terminal,
  MoreHorizontal, ChevronDown, LogOut,
  Clock, Puzzle, PlayCircle, Shield,
} from 'lucide-react'
import { ConnectionStatus } from '@/components/ui/ConnectionStatus'
import { RateLimitIndicator } from '@/components/ui/RateLimitIndicator'
import { NotificationCenter } from '@/components/notifications'
import { usePluginRegistry } from '@/plugins'
import { usePreferenceStore } from '@/stores/preferenceStore'
import { useAuthStore } from '@/stores/authStore'

// ============================================================
// Nav types and data
// ============================================================

type NavId = 'chat' | 'dashboard' | 'agents' | 'tasks' | 'process' | 'graph' | 'xskill' | 'artifacts' | 'skills' | 'analytics' | 'terminal' | 'settings' | 'admin-cron' | 'admin-plugins' | 'admin-batch'

type NavItem = { id: NavId; label: string; icon: typeof MessageSquare; path: string }

/** Primary nav items — always visible */
const primaryNavItems: NavItem[] = [
  { id: 'chat', label: 'Chat', icon: MessageSquare, path: '/chat' },
  { id: 'artifacts', label: 'Artifacts', icon: FolderOpen, path: '/artifacts' },
  { id: 'agents', label: 'Agents', icon: Bot, path: '/agents' },
  { id: 'skills', label: 'Skills', icon: Sparkles, path: '/skills' },
  { id: 'xskill', label: 'XSkill', icon: Brain, path: '/xskill' },
  { id: 'graph', label: 'Graph', icon: Share2, path: '/graph' },
]

/** Secondary nav items — hidden inside "More" dropdown on desktop */
const moreNavItems: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, path: '/dashboard' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, path: '/analytics' },
  { id: 'tasks', label: 'Tasks', icon: ListTodo, path: '/tasks' },
  { id: 'process', label: 'Process', icon: Activity, path: '/process' },
  { id: 'terminal', label: 'Terminal', icon: Terminal, path: '/terminal' },
]

/** Admin nav items — shown in a separate "Admin" section inside More dropdown */
const adminNavItems: NavItem[] = [
  { id: 'admin-cron', label: 'Cron Jobs', icon: Clock, path: '/admin/cron' },
  { id: 'admin-plugins', label: 'Plugins', icon: Puzzle, path: '/admin/plugins' },
  { id: 'admin-batch', label: 'Batch Runner', icon: PlayCircle, path: '/admin/batch' },
]

/** All nav items combined (for mobile menu + getActiveNav) */
const navItems: NavItem[] = [...primaryNavItems, ...moreNavItems, ...adminNavItems]

// ============================================================
// Memoized NavItem components to prevent unnecessary re-renders
// ============================================================

interface NavItemLinkProps {
  item: NavItem
  isActive: boolean
}

/** Primary nav item (pill-style in top bar) */
const PrimaryNavItemLink = memo(function PrimaryNavItemLink({ item, isActive }: NavItemLinkProps) {
  const Icon = item.icon
  return (
    <Link
      to={item.path}
      aria-current={isActive ? 'page' : undefined}
      className={`relative flex items-center gap-[6px] px-3.5 py-[7px] rounded-[11px] text-[13px] font-medium transition-all duration-200 ${
        isActive
          ? 'bg-white dark:bg-surface-tertiary text-text-dark dark:text-white shadow-[0_1px_3px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.06)] dark:shadow-none'
          : 'text-text-secondary dark:text-text-secondary hover:text-text-dark dark:hover:text-white'
      }`}
    >
      <Icon className={`w-[15px] h-[15px] ${isActive ? 'text-brand-500' : ''}`} strokeWidth={isActive ? 2.2 : 1.8} />
      <span>{item.label}</span>
    </Link>
  )
})

interface DropdownNavItemLinkProps {
  item: NavItem
  isActive: boolean
  onClick?: () => void
}

/** Dropdown nav item (inside "More" menu or mobile menu) */
const DropdownNavItemLink = memo(function DropdownNavItemLink({ item, isActive, onClick }: DropdownNavItemLinkProps) {
  const Icon = item.icon
  return (
    <Link
      to={item.path}
      onClick={onClick}
      role="menuitem"
      aria-current={isActive ? 'page' : undefined}
      className={`flex items-center gap-2.5 px-3.5 py-2 text-[13px] font-medium transition-colors ${
        isActive
          ? 'bg-brand-50 dark:bg-blue-900/20 text-brand-700 dark:text-blue-400'
          : 'text-text-secondary dark:text-text-secondary hover:bg-surface-secondary dark:hover:bg-surface-tertiary hover:text-text-dark dark:hover:text-white'
      }`}
    >
      <Icon className={`w-4 h-4 ${isActive ? 'text-brand-500' : ''}`} strokeWidth={isActive ? 2 : 1.5} />
      <span>{item.label}</span>
    </Link>
  )
})

interface MobileNavItemLinkProps {
  item: NavItem
  isActive: boolean
  onClick?: () => void
}

/** Mobile nav item (full-width in mobile dropdown) */
const MobileNavItemLink = memo(function MobileNavItemLink({ item, isActive, onClick }: MobileNavItemLinkProps) {
  const Icon = item.icon
  return (
    <Link
      to={item.path}
      onClick={onClick}
      role="menuitem"
      aria-current={isActive ? 'page' : undefined}
      className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-[10px] text-left text-[14px] font-medium transition-all ${
        isActive
          ? 'bg-brand-50 dark:bg-blue-900/20 text-brand-700 dark:text-blue-400'
          : 'text-text-secondary dark:text-text-secondary hover:bg-surface-secondary dark:hover:bg-surface-secondary hover:text-text-dark dark:hover:text-white'
      }`}
    >
      <Icon className={`w-[18px] h-[18px] ${isActive ? 'text-brand-500' : ''}`} strokeWidth={isActive ? 2 : 1.5} />
      <span>{item.label}</span>
    </Link>
  )
})

function getActiveNav(pathname: string): NavId {
  if (pathname.startsWith('/chat')) return 'chat'
  if (pathname.startsWith('/dashboard')) return 'dashboard'
  if (pathname.startsWith('/agents')) return 'agents'
  if (pathname.startsWith('/tasks')) return 'tasks'
  if (pathname.startsWith('/process')) return 'process'
  if (pathname.startsWith('/graph')) return 'graph'
  if (pathname.startsWith('/xskill')) return 'xskill'
  if (pathname.startsWith('/artifacts')) return 'artifacts'
  if (pathname.startsWith('/skills')) return 'skills'
  if (pathname.startsWith('/analytics')) return 'analytics'
  if (pathname.startsWith('/terminal')) return 'terminal'
  if (pathname.startsWith('/settings')) return 'settings'
  if (pathname.startsWith('/admin/cron')) return 'admin-cron'
  if (pathname.startsWith('/admin/plugins')) return 'admin-plugins'
  if (pathname.startsWith('/admin/batch')) return 'admin-batch'
  return 'chat'
}

// ============================================================
// Navigation component props
// ============================================================

interface NavigationProps {
  systemOnline: boolean
  isOffline: boolean
  retryConnection: () => void
}

export function Navigation({ systemOnline, isOffline, retryConnection }: NavigationProps) {
  const location = useLocation()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)
  const activeNav = getActiveNav(location.pathname)
  const rightPanelCollapsed = usePreferenceStore((s) => s.rightPanelCollapsed)
  const toggleRightPanel = usePreferenceStore((s) => s.toggleRightPanel)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const logout = useAuthStore((s) => s.logout)
  const authUserId = useAuthStore((s) => s.userId)

  // Plugin routes for navigation — only 'main' category plugins show in nav
  const pluginRoutes = usePluginRegistry((s) => s.routes)
  const mainPluginNavItems = pluginRoutes.filter((r) => r.category === 'main')

  return (
    <>
      <header className="sticky top-0 z-50 border-b border-border-light dark:border-border-subtle bg-white/80 dark:bg-surface-base/80 backdrop-blur-xl">
        <div className="max-w-[1440px] mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-[60px]">
            {/* Logo */}
            <Link
              to="/chat"
              className="flex items-center gap-3 shrink-0 cursor-pointer group"
            >
              <div className="relative">
                <div className="w-9 h-9 rounded-[10px] bg-gradient-to-br from-brand-800 via-brand-500 to-brand-400 flex items-center justify-center shadow-md group-hover:shadow-lg transition-shadow duration-300">
                  <Zap className="w-[18px] h-[18px] text-white" strokeWidth={2.5} />
                </div>
                <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 border-2 border-white dark:border-surface-base rounded-full ${
                  systemOnline ? 'bg-emerald-400' : 'bg-red-400'
                }`} />
              </div>
              <div className="hidden sm:block">
                <h1 className="text-[15px] font-semibold text-text-dark dark:text-text-heading tracking-[-0.01em] leading-none">
                  MekongAI
                </h1>
                <p className="text-[11px] text-text-muted leading-none mt-[3px] font-medium tracking-wide uppercase">
                  Nexus
                </p>
              </div>
            </Link>

            {/* Desktop Nav — Primary items + "More" dropdown */}
            <nav className="hidden md:flex items-center">
              <div className="flex items-center gap-[2px] bg-surface-secondary rounded-[14px] p-[3px]">
                {/* Primary nav items (always visible) */}
                {primaryNavItems.map((item) => (
                  <PrimaryNavItemLink key={item.id} item={item} isActive={activeNav === item.id} />
                ))}

                {/* "More" dropdown for secondary items */}
                <div className="relative">
                  <button
                    onClick={() => setMoreMenuOpen(!moreMenuOpen)}
                    aria-expanded={moreMenuOpen}
                    aria-haspopup="menu"
                    aria-controls="more-nav-menu"
                    className={`relative flex items-center gap-[4px] px-3 py-[7px] rounded-[11px] text-[13px] font-medium transition-all duration-200 ${
                      moreNavItems.some((m) => activeNav === m.id)
                        ? 'bg-white dark:bg-surface-tertiary text-text-dark dark:text-white shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                        : 'text-text-secondary dark:text-text-secondary hover:text-text-dark dark:hover:text-white'
                    }`}
                  >
                    <MoreHorizontal className="w-[15px] h-[15px]" strokeWidth={1.8} />
                    <span>More</span>
                    <ChevronDown className={`w-3 h-3 transition-transform ${moreMenuOpen ? 'rotate-180' : ''}`} />
                  </button>

                  {/* Dropdown panel */}
                  {moreMenuOpen && (
                    <>
                      {/* Backdrop to close */}
                      <div className="fixed inset-0 z-40" onClick={() => setMoreMenuOpen(false)} />
                      <div
                        id="more-nav-menu"
                        role="menu"
                        aria-label="More navigation options"
                        className="absolute top-full left-0 mt-1.5 w-48 bg-surface-base border border-border dark:border-border-subtle rounded-[12px] shadow-lg z-50 py-1.5 animate-fade-in"
                      >
                        {moreNavItems.map((item) => (
                          <DropdownNavItemLink
                            key={item.id}
                            item={item}
                            isActive={activeNav === item.id}
                            onClick={() => setMoreMenuOpen(false)}
                          />
                        ))}
                        {/* Plugin nav items inside More dropdown */}
                        {mainPluginNavItems.length > 0 && (
                          <>
                            <div className="h-px bg-border dark:bg-border-subtle my-1" />
                            {mainPluginNavItems.map((pluginRoute) => {
                              const PluginIcon = pluginRoute.icon
                              const isActive = location.pathname === pluginRoute.path
                              return (
                                <Link
                                  key={pluginRoute.id}
                                  to={pluginRoute.path}
                                  onClick={() => setMoreMenuOpen(false)}
                                  role="menuitem"
                                  className={`flex items-center gap-2.5 px-3.5 py-2 text-[13px] font-medium transition-colors ${
                                    isActive
                                      ? 'bg-brand-50 dark:bg-blue-900/20 text-brand-700 dark:text-blue-400'
                                      : 'text-text-secondary dark:text-text-secondary hover:bg-surface-secondary dark:hover:bg-surface-tertiary hover:text-text-dark dark:hover:text-white'
                                  }`}
                                >
                                  {PluginIcon && <PluginIcon className={`w-4 h-4 ${isActive ? 'text-brand-500' : ''}`} />}
                                  <span>{pluginRoute.label}</span>
                                </Link>
                              )
                            })}
                          </>
                        )}
                        {/* Admin nav items */}
                        <div className="h-px bg-border dark:bg-border-subtle my-1" />
                        <div className="px-3.5 py-1">
                          <span className="flex items-center gap-1.5 text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                            <Shield className="w-3 h-3" /> Admin
                          </span>
                        </div>
                        {adminNavItems.map((item) => (
                          <DropdownNavItemLink
                            key={item.id}
                            item={item}
                            isActive={activeNav === item.id}
                            onClick={() => setMoreMenuOpen(false)}
                          />
                        ))}
                      </div>
                    </>
                  )}
                </div>
              </div>
            </nav>

            {/* Right side */}
            <div className="flex items-center gap-2">
              {/* Notification Center */}
              <NotificationCenter />

              {/* Toggle right panel (only on chat) */}
              {activeNav === 'chat' && (
                <button
                  onClick={toggleRightPanel}
                  className={`hidden sm:flex w-8 h-8 rounded-[8px] items-center justify-center transition-colors ${
                    !rightPanelCollapsed
                      ? 'bg-brand-50 dark:bg-blue-900/30 text-brand-500'
                      : 'hover:bg-surface-secondary dark:hover:bg-surface-secondary text-text-secondary dark:text-text-secondary'
                  }`}
                  title="Toggle progress panel (Ctrl+.)"
                >
                  <PanelRight className="w-4 h-4" />
                </button>
              )}

              {/* Settings */}
              <Link
                to="/settings"
                className={`hidden sm:flex w-8 h-8 rounded-[8px] items-center justify-center transition-colors ${
                  activeNav === 'settings'
                    ? 'bg-brand-50 dark:bg-blue-900/30 text-brand-500'
                    : 'hover:bg-surface-secondary dark:hover:bg-surface-secondary text-text-secondary dark:text-text-secondary'
                }`}
                title="Settings"
              >
                <Settings className="w-4 h-4" />
              </Link>

              {/* Logout */}
              {isAuthenticated && (
                <button
                  onClick={logout}
                  className="hidden sm:flex w-8 h-8 rounded-[8px] items-center justify-center transition-colors hover:bg-red-50 dark:hover:bg-red-900/20 text-text-secondary dark:text-text-secondary hover:text-red-500 dark:hover:text-red-400"
                  title={`Logout${authUserId ? ` (${authUserId})` : ''}`}
                >
                  <LogOut className="w-4 h-4" />
                </button>
              )}

              {/* Status pill + ConnectionStatus + RateLimit */}
              <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 bg-surface-secondary rounded-full">
                <span className={`w-[7px] h-[7px] rounded-full ${
                  systemOnline
                    ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]'
                    : 'bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.5)]'
                }`} />
                <span className="text-[12px] font-medium text-text-secondary">
                  {systemOnline ? 'Online' : 'Offline'}
                </span>
                <span className="w-px h-3 bg-border-light dark:bg-border-subtle" />
                <ConnectionStatus compact />
                <span className="w-px h-3 bg-border-light dark:bg-border-subtle" />
                <RateLimitIndicator />
              </div>

              {/* Mobile hamburger */}
              <button
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                aria-expanded={mobileMenuOpen}
                aria-controls="mobile-nav-menu"
                aria-label="Toggle navigation menu"
                className="md:hidden w-9 h-9 flex items-center justify-center rounded-[10px] hover:bg-surface-secondary transition-colors"
              >
                {mobileMenuOpen
                  ? <X className="w-5 h-5 text-text-secondary" />
                  : <Menu className="w-5 h-5 text-text-secondary" />}
              </button>
            </div>
          </div>
        </div>

        {/* Mobile Nav Dropdown */}
        {mobileMenuOpen && (
          <div
            id="mobile-nav-menu"
            role="menu"
            aria-label="Navigation menu"
            className="md:hidden border-t border-border-light dark:border-border-subtle bg-white/95 dark:bg-surface-base/95 backdrop-blur-lg animate-slide-down"
          >
            <div className="px-3 py-2 space-y-0.5">
              {navItems.map((item) => (
                <MobileNavItemLink
                  key={item.id}
                  item={item}
                  isActive={activeNav === item.id}
                  onClick={() => setMobileMenuOpen(false)}
                />
              ))}
              <Link
                to="/settings"
                onClick={() => setMobileMenuOpen(false)}
                className="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-[10px] text-left text-[14px] font-medium text-text-secondary hover:bg-surface-secondary hover:text-text-dark dark:hover:text-white transition-all"
              >
                <Settings className="w-[18px] h-[18px]" />
                <span>Settings</span>
              </Link>
            </div>
          </div>
        )}
      </header>

      {/* Offline Banner (MISS-09) */}
      {isOffline && (
        <div className="bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800 px-4 py-2 flex items-center justify-center gap-2">
          <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
          <span className="text-[13px] text-amber-800 dark:text-amber-300 font-medium">
            Connection lost. Reconnecting...
          </span>
          <button
            onClick={retryConnection}
            className="text-[12px] text-amber-700 dark:text-amber-400 underline hover:text-amber-900 dark:hover:text-amber-200 ml-2"
          >
            Retry now
          </button>
        </div>
      )}
    </>
  )
}
