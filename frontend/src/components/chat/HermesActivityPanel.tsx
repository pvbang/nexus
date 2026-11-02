// ============================================================
// Nexus - Hermes Activity Panel (Unified)
// Combined right panel: Process Log + Files + Graph + Agents + Tools + Analysis
// Flat tab structure — no nested sub-tabs for clarity.
// ============================================================

import { useState, useMemo } from 'react'
import {
  Bot, Wrench, Brain, ChevronLeft, Activity, ScrollText,
  FolderOpen, GitBranch,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../lib/cn'
import { ToolActivityPanel, type ToolCall } from './ToolActivityPanel'
import { AgentStatusDisplay, type AgentInfo, type SwarmInfo } from './AgentStatusDisplay'
import { useExecutionStore } from '../../stores/executionStore'
import { useProcessStore, getPersistedTaskId } from '../../stores/processStore'

// Sub-panels for process log, files, graph (previously nested inside UnifiedProcessPanel)
import { UnifiedTimeline } from '../process/UnifiedTimeline'
import { TaskKnowledgeGraph } from '../process/TaskKnowledgeGraph'
import { SessionArtifactsView } from '../process/SessionArtifactsView'

// ── Types ─────────────────────────────────────────────────────

type PanelTab = 'process' | 'files' | 'graph' | 'agents' | 'tools' | 'analysis'

export interface HermesActivityPanelProps {
  agents: AgentInfo[]
  toolCalls: ToolCall[]
  swarm?: SwarmInfo | null
  deepAnalysis?: {
    dimensions?: string[]
    strategy?: string
    complexity?: string
    progress?: number
  } | null
  sessionId?: string
  onCollapse?: () => void
  className?: string
}

// ── Deep Analysis View ────────────────────────────────────────

function DeepAnalysisView({ analysis }: {
  analysis: NonNullable<HermesActivityPanelProps['deepAnalysis']>
}) {
  return (
    <div className="p-3 space-y-3">
      {/* Strategy */}
      {analysis.strategy && (
        <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800/40 p-3">
          <div className="text-[10px] font-semibold text-blue-600 dark:text-blue-400 uppercase tracking-wider mb-1">
            Strategy
          </div>
          <p className="text-[12px] text-blue-800 dark:text-blue-200">
            {analysis.strategy}
          </p>
        </div>
      )}

      {/* Complexity */}
      {analysis.complexity && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase">
            Complexity
          </span>
          <span className={cn(
            'text-[10px] font-bold px-2 py-0.5 rounded-full',
            analysis.complexity === 'low' && 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
            analysis.complexity === 'medium' && 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
            analysis.complexity === 'high' && 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
            analysis.complexity === 'very_high' && 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
          )}>
            {analysis.complexity}
          </span>
        </div>
      )}

      {/* Dimensions */}
      {analysis.dimensions && analysis.dimensions.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1.5">
            Analysis Dimensions
          </div>
          <div className="flex flex-wrap gap-1">
            {analysis.dimensions.map((dim, i) => (
              <span
                key={i}
                className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-[#252545] text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-[#3a3a5a]"
              >
                {dim}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Progress */}
      {analysis.progress !== undefined && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase">
              Progress
            </span>
            <span className="text-[10px] font-mono text-gray-600 dark:text-gray-300">
              {Math.round(analysis.progress)}%
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-gray-200 dark:bg-[#252545] overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500"
              initial={{ width: 0 }}
              animate={{ width: `${analysis.progress}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// Stable empty array to avoid re-render loops
const EMPTY_EVENTS: never[] = []

// ── Main Panel ────────────────────────────────────────────────

export function HermesActivityPanel({
  agents,
  toolCalls,
  swarm,
  deepAnalysis,
  sessionId,
  onCollapse,
  className,
}: HermesActivityPanelProps) {
  // Default to 'process' tab when idle (no active agents), 'agents' when processing
  const hasActiveAgents = agents.some(a => a.status !== 'idle' && a.status !== 'done')
  const [activeTab, setActiveTab] = useState<PanelTab>(hasActiveAgents ? 'agents' : 'process')

  const activeAgentCount = agents.filter(a => a.status !== 'idle' && a.status !== 'done').length
  const runningToolCount = toolCalls.filter(t => t.status === 'running').length

  // Process store data for timeline
  const processTaskId = useProcessStore((s) => s.taskId)
  const effectiveTaskId = processTaskId || (sessionId ? getPersistedTaskId(sessionId) : null)

  // Execution events for timeline
  const executionEvents = useExecutionStore((s) =>
    sessionId ? (s.eventsBySession[sessionId] ?? EMPTY_EVENTS) : EMPTY_EVENTS
  )
  const executionEventCount = executionEvents.length

  const tabs: { id: PanelTab; label: string; icon: typeof Bot; count?: number }[] = useMemo(() => [
    { id: 'process', label: 'Process', icon: ScrollText, count: executionEventCount || undefined },
    { id: 'files', label: 'Files', icon: FolderOpen },
    { id: 'graph', label: 'Graph', icon: GitBranch },
    { id: 'agents', label: 'Agents', icon: Bot, count: activeAgentCount || undefined },
    { id: 'tools', label: 'Tools', icon: Wrench, count: runningToolCount || undefined },
    { id: 'analysis', label: 'Analysis', icon: Brain },
  ], [executionEventCount, activeAgentCount, runningToolCount])

  return (
    <div className={cn(
      'w-72 h-full flex flex-col border-l border-gray-200 dark:border-[#2a2a45]',
      'bg-gray-50 dark:bg-[#0d0d1a]',
      className
    )}>
      {/* Panel header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-[#2a2a45] shrink-0">
        <Activity className="w-4 h-4 text-blue-500" />
        <span className="text-[12px] font-semibold text-gray-700 dark:text-gray-300 flex-1">
          Activity
        </span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="w-6 h-6 rounded flex items-center justify-center hover:bg-gray-200 dark:hover:bg-[#252545] transition-colors"
            title="Collapse panel"
          >
            <ChevronLeft className="w-3.5 h-3.5 text-gray-400" />
          </button>
        )}
      </div>

      {/* Tab selector — wraps on 2 rows for 6 tabs */}
      <div className="flex flex-wrap items-center gap-0.5 px-2 py-1.5 border-b border-gray-200 dark:border-[#2a2a45] shrink-0">
        {tabs.map(tab => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium transition-colors',
                isActive
                  ? 'bg-white dark:bg-[#1e1e35] text-gray-800 dark:text-white shadow-sm border border-gray-200 dark:border-[#3a3a5a]'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
              )}
            >
              <Icon className="w-3 h-3" />
              <span>{tab.label}</span>
              {tab.count && tab.count > 0 && (
                <span className={cn(
                  'min-w-[14px] h-[14px] rounded-full flex items-center justify-center text-[9px] font-bold',
                  isActive
                    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
                    : 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                )}>
                  {tab.count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Panel content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <AnimatePresence mode="wait">
          {activeTab === 'process' && (
            <motion.div
              key="process"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full overflow-y-auto"
            >
              {sessionId ? (
                <UnifiedTimeline
                  sessionId={sessionId}
                />
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-center px-4">
                  <ScrollText className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                  <p className="text-[12px] text-gray-400 dark:text-gray-500">
                    No execution events yet
                  </p>
                  <p className="text-[11px] text-gray-300 dark:text-gray-600 mt-1">
                    Events will appear here when a task is being processed
                  </p>
                </div>
              )}
            </motion.div>
          )}

          {activeTab === 'files' && (
            <motion.div
              key="files"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full overflow-y-auto"
            >
              {sessionId ? (
                <SessionArtifactsView
                  sessionId={sessionId}
                />
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-center px-4">
                  <FolderOpen className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                  <p className="text-[12px] text-gray-400 dark:text-gray-500">
                    No files yet
                  </p>
                  <p className="text-[11px] text-gray-300 dark:text-gray-600 mt-1">
                    Generated files and artifacts will appear here
                  </p>
                </div>
              )}
            </motion.div>
          )}

          {activeTab === 'graph' && (
            <motion.div
              key="graph"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full overflow-y-auto"
            >
              {effectiveTaskId ? (
                <TaskKnowledgeGraph
                  taskId={effectiveTaskId}
                />
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-center px-4">
                  <GitBranch className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                  <p className="text-[12px] text-gray-400 dark:text-gray-500">
                    No knowledge graph yet
                  </p>
                  <p className="text-[11px] text-gray-300 dark:text-gray-600 mt-1">
                    The task knowledge graph will appear here during execution
                  </p>
                </div>
              )}
            </motion.div>
          )}

          {activeTab === 'agents' && (
            <motion.div
              key="agents"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full overflow-y-auto p-2"
            >
              <AgentStatusDisplay agents={agents} swarm={swarm} />
            </motion.div>
          )}

          {activeTab === 'tools' && (
            <motion.div
              key="tools"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full"
            >
              <ToolActivityPanel toolCalls={toolCalls} />
            </motion.div>
          )}

          {activeTab === 'analysis' && (
            <motion.div
              key="analysis"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              transition={{ duration: 0.15 }}
              className="h-full overflow-y-auto"
            >
              {deepAnalysis ? (
                <DeepAnalysisView analysis={deepAnalysis} />
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-center px-4">
                  <Brain className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                  <p className="text-[12px] text-gray-400 dark:text-gray-500">
                    No deep analysis yet
                  </p>
                  <p className="text-[11px] text-gray-300 dark:text-gray-600 mt-1">
                    Analysis data will appear when the agent performs deep reasoning
                  </p>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
