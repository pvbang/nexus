// ============================================================
// MekongAI Nexus - Plugin Error Boundary
// Feature #10: Isolates plugin crashes from the main app
// ============================================================

import { Component, type ReactNode, type ErrorInfo } from 'react'
import { usePluginRegistry } from './registry'

// ── Props & State ─────────────────────────────────────────────

interface PluginErrorBoundaryProps {
  /** Plugin ID for error reporting and disable action */
  pluginId: string
  /** Plugin display name */
  pluginName: string
  children: ReactNode
}

interface PluginErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

// ── Component ─────────────────────────────────────────────────

export class PluginErrorBoundary extends Component<
  PluginErrorBoundaryProps,
  PluginErrorBoundaryState
> {
  constructor(props: PluginErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): PluginErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error(
      `[PluginErrorBoundary] Plugin "${this.props.pluginName}" (${this.props.pluginId}) crashed:`,
      error,
      errorInfo,
    )
  }

  private handleDisablePlugin = (): void => {
    usePluginRegistry.getState().disablePlugin(this.props.pluginId)
    this.setState({ hasError: false, error: null })
  }

  private handleRetry = (): void => {
    this.setState({ hasError: false, error: null })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex items-center gap-2 px-3 py-2 rounded-[11px] border border-red-200 bg-red-50 dark:border-red-800/50 dark:bg-red-900/10 text-[12px]">
          <span className="text-red-500 dark:text-red-400 shrink-0">⚠️</span>
          <span className="text-red-700 dark:text-red-300 font-medium truncate">
            Plugin &quot;{this.props.pluginName}&quot; error
          </span>
          <span className="text-red-500/70 dark:text-red-400/60 truncate hidden sm:inline">
            {this.state.error?.message}
          </span>
          <div className="flex items-center gap-1.5 ml-auto shrink-0">
            <button
              onClick={this.handleRetry}
              className="px-2 py-0.5 rounded-md text-[11px] font-medium text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors"
            >
              Retry
            </button>
            <button
              onClick={this.handleDisablePlugin}
              className="px-2 py-0.5 rounded-md text-[11px] font-medium text-red-700 dark:text-red-300 bg-red-100 dark:bg-red-900/30 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
            >
              Disable
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
