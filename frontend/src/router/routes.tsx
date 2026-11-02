// ============================================================
// Nexus - Route definitions with lazy imports
// ============================================================

import { lazy } from 'react'
import {
  ChatSkeleton, DashboardSkeleton, CardGridSkeleton,
  TableSkeleton, TerminalSkeleton, GenericPageSkeleton,
} from '@/components/ui/PageSkeletons'

// ============================================================
// Lazy-loaded page components (code splitting)
// ChatLayout is kept as direct import in AppRouter (main page, always needed)
// ============================================================
export const AgentMonitor = lazy(() => import('@/components/AgentMonitor').then(m => ({ default: m.AgentMonitor })))
export const TaskManager = lazy(() => import('@/components/TaskManager').then(m => ({ default: m.TaskManager })))
export const SystemDashboard = lazy(() => import('@/components/SystemDashboard').then(m => ({ default: m.SystemDashboard })))
export const XSkillView = lazy(() => import('@/components/XSkillView').then(m => ({ default: m.XSkillView })))
export const ArtifactsPage = lazy(() => import('@/components/artifacts/ArtifactsPage').then(m => ({ default: m.ArtifactsPage })))
export const KnowledgeGraph = lazy(() => import('@/components/KnowledgeGraph').then(m => ({ default: m.KnowledgeGraph })))
export const TaskDetail = lazy(() => import('@/components/tasks/TaskDetail').then(m => ({ default: m.TaskDetail })))
export const SettingsPanel = lazy(() => import('@/components/SettingsPanel').then(m => ({ default: m.SettingsPanel })))
export const SkillsHub = lazy(() => import('@/components/admin/SkillsHub').then(m => ({ default: m.SkillsHub })))
export const CronManager = lazy(() => import('@/components/admin/CronManager').then(m => ({ default: m.CronManager })))
export const PluginManager = lazy(() => import('@/components/admin/PluginManager').then(m => ({ default: m.PluginManager })))
export const BatchRunner = lazy(() => import('@/components/admin/BatchRunner').then(m => ({ default: m.BatchRunner })))
export const MultiTaskDashboard = lazy(() => import('@/components/process').then(m => ({ default: m.MultiTaskDashboard })))
export const TerminalPanel = lazy(() => import('@/components/terminal').then(m => ({ default: m.TerminalPanel })))
export const AnalyticsDashboard = lazy(() => import('@/components/analytics/AnalyticsDashboard').then(m => ({ default: m.AnalyticsDashboard })))
export const NotFoundPage = lazy(() => import('@/components/NotFoundPage').then(m => ({ default: m.NotFoundPage })))
export const LoginPage = lazy(() => import('@/components/LoginPage').then(m => ({ default: m.LoginPage })))

// ============================================================
// Route-aware loading fallback
// ============================================================
export function PageLoadingFallback() {
  const path = window.location.pathname
  if (path.startsWith('/chat')) return <ChatSkeleton />
  if (path.startsWith('/dashboard')) return <DashboardSkeleton />
  if (path.startsWith('/agents')) return <CardGridSkeleton />
  if (path.startsWith('/tasks')) return <TableSkeleton />
  if (path.startsWith('/terminal')) return <TerminalSkeleton />
  if (path.startsWith('/analytics') || path.startsWith('/settings') || path.startsWith('/admin')) return <GenericPageSkeleton />
  // Default fallback
  return (
    <div className="flex items-center justify-center h-full">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-3 border-[var(--accent,#3b82f6)] border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-[var(--text-secondary,#a0a0b8)]">Loading...</p>
      </div>
    </div>
  )
}
