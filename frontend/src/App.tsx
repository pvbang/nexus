// ============================================================
// MekongAI Nexus - App (slim orchestrator)
// Composes: Navigation, Router, GlobalProviders, hooks
// ============================================================

import React, { Component } from 'react'
import { AppRouter, useAuthGuard } from '@/router'
import { Navigation, GlobalProviders } from '@/components/layout'
import { useSystemHealth } from '@/hooks/useSystemHealth'
import { useGlobalShortcuts } from '@/hooks/useGlobalShortcuts'
import { useWebSocket } from '@/hooks/useWebSocket'

// ============================================================
// Top-level App Error Boundary (class component)
// ============================================================
interface AppErrorBoundaryState {
  hasError: boolean
  error?: Error
}

class AppErrorBoundary extends Component<
  { children: React.ReactNode },
  AppErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('App Error:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-screen bg-[var(--bg-primary,#0f0f17)] text-[var(--text-primary,#e4e4ed)]">
          <div className="w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center mb-6">
            <span className="text-3xl">⚠️</span>
          </div>
          <h1 className="text-2xl font-bold mb-4">Something went wrong</h1>
          <p className="text-[var(--text-secondary,#a0a0b8)] mb-6 max-w-md text-center">
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => { this.setState({ hasError: false }); window.location.reload() }}
            className="px-5 py-2.5 bg-[var(--accent,#3b82f6)] text-white rounded-lg font-medium hover:opacity-90 transition-opacity"
          >
            Reload Application
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

// ============================================================
// AppContent — hooks + layout composition
// ============================================================
function AppContent() {
  const { systemOnline, isOffline, isInitializing, retryConnection } = useSystemHealth()

  // Auth guard: redirect to /login if not authenticated
  useAuthGuard()

  // WebSocket auto-connect on mount
  useWebSocket()

  // Global keyboard shortcuts (Ctrl+N, Ctrl+K, Ctrl+/, Ctrl+.)
  useGlobalShortcuts()

  // Loading skeleton during initial app boot
  if (isInitializing) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface-base">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-[var(--accent,#3b82f6)] border-t-transparent rounded-full animate-spin" />
          <p className="text-[var(--text-secondary,#a0a0b8)] text-sm font-medium">Loading MekongAI Nexus...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-full bg-surface-base text-text-primary flex flex-col overflow-hidden">
      <Navigation systemOnline={systemOnline} isOffline={isOffline} retryConnection={retryConnection} />
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <AppRouter />
      </main>
      <GlobalProviders />
    </div>
  )
}

// ============================================================
// App — wrap with error boundary
// ============================================================
export default function App() {
  return (
    <AppErrorBoundary>
      <AppContent />
    </AppErrorBoundary>
  )
}
