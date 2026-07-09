// ============================================================
// MekongAI Nexus - Main router component with Suspense wrapper
// ============================================================

import { Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ui/ErrorBoundary'
import { ChatLayout } from '@/layouts/ChatLayout'
import { usePluginRegistry } from '@/plugins'
import {
  AgentMonitor,
  TaskManager,
  SystemDashboard,
  XSkillView,
  ArtifactsPage,
  KnowledgeGraph,
  TaskDetail,
  SettingsPanel,
  SkillsHub,
  CronManager,
  PluginManager,
  BatchRunner,
  MultiTaskDashboard,
  TerminalPanel,
  AnalyticsDashboard,
  NotFoundPage,
  LoginPage,
  PageLoadingFallback,
} from './routes'

export function AppRouter() {
  const pluginRoutes = usePluginRegistry((s) => s.routes)

  return (
    <Suspense fallback={<PageLoadingFallback />}>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/chat" element={<ErrorBoundary><ChatLayout /></ErrorBoundary>} />
        <Route path="/chat/:sessionId" element={<ErrorBoundary><ChatLayout /></ErrorBoundary>} />
        <Route path="/dashboard" element={<ErrorBoundary><SystemDashboard /></ErrorBoundary>} />
        <Route path="/agents" element={<ErrorBoundary><AgentMonitor /></ErrorBoundary>} />
        <Route path="/tasks" element={<ErrorBoundary><TaskManager /></ErrorBoundary>} />
        <Route path="/tasks/:taskId" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
        <Route path="/graph" element={<ErrorBoundary><KnowledgeGraph /></ErrorBoundary>} />
        <Route path="/xskill" element={<ErrorBoundary><XSkillView /></ErrorBoundary>} />
        <Route path="/artifacts" element={<ErrorBoundary><ArtifactsPage /></ErrorBoundary>} />
        <Route path="/artifacts/:taskId" element={<ErrorBoundary><ArtifactsPage /></ErrorBoundary>} />
        <Route path="/skills" element={<ErrorBoundary><SkillsHub /></ErrorBoundary>} />
        <Route path="/process" element={<ErrorBoundary><MultiTaskDashboard /></ErrorBoundary>} />
        <Route path="/analytics" element={<ErrorBoundary><AnalyticsDashboard /></ErrorBoundary>} />
        <Route path="/terminal" element={<ErrorBoundary><TerminalPanel /></ErrorBoundary>} />
        <Route path="/admin/cron" element={<ErrorBoundary><CronManager /></ErrorBoundary>} />
        <Route path="/admin/plugins" element={<ErrorBoundary><PluginManager /></ErrorBoundary>} />
        <Route path="/admin/batch" element={<ErrorBoundary><BatchRunner /></ErrorBoundary>} />
        <Route path="/settings" element={<ErrorBoundary><SettingsPanel /></ErrorBoundary>} />
        {/* Dynamic plugin routes */}
        {pluginRoutes.map((route) => {
          const PageComponent = route.component
          return (
            <Route
              key={route.id}
              path={route.path}
              element={
                <ErrorBoundary>
                  <PageComponent />
                </ErrorBoundary>
              }
            />
          )
        })}
        {/* 404 catch-all route */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  )
}
