// ============================================================
// Nexus - ProcessViewer
// Main orchestration transparency component — replaces
// ExecutionTracePanel with a full stage-based process view
// ============================================================

import { useState, useEffect, useMemo, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Timer,
  Hash,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Send,
  Eye,
  EyeOff,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import type { ProcessStage } from '../../types/process'

// Sub-components
import { StagePipeline } from './StagePipeline'
import { PlanView } from './PlanView'
import { AgentCommunicationFeed } from './AgentCommunicationFeed'
import { ToolExecutionList } from './ToolExecutionList'
import { DebateView } from './DebateView'
import { SynthesisView } from './SynthesisView'
import { XSkillLearningView } from './XSkillLearningView'

// UI primitives
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { Spinner } from '../ui/Spinner'
import { ProgressBar } from '../ui/ProgressBar'

// ── Duration timer hook ───────────────────────────────────────

function useDurationTimer(startTime: number | null, isRunning: boolean) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!startTime || !isRunning) {
      if (startTime && !isRunning) {
        setElapsed(Date.now() - startTime)
      }
      return
    }
    setElapsed(Date.now() - startTime)
    const interval = setInterval(() => {
      setElapsed(Date.now() - startTime)
    }, 1000)
    return () => clearInterval(interval)
  }, [startTime, isRunning])

  return elapsed
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes > 0) {
    return `${minutes}m ${seconds.toString().padStart(2, '0')}s`
  }
  return `${seconds}s`
}

// ── Stage → content mapping ───────────────────────────────────

const ACTIVE_STAGES: ProcessStage[] = [
  'planning',
  'spawning',
  'communicating',
  'executing',
  'debating',
  'synthesizing',
  'learning',
  'responding',
]

const stageIndex = (stage: ProcessStage): number =>
  ACTIVE_STAGES.indexOf(stage)

function StageContent({ stage }: { stage: ProcessStage }) {
  switch (stage) {
    case 'planning':
      return <PlanView />
    case 'spawning':
      return <SpawningView />
    case 'communicating':
      return <AgentCommunicationFeed />
    case 'executing':
      return <ToolExecutionList />
    case 'debating':
      return <DebateView />
    case 'synthesizing':
      return <SynthesisView />
    case 'learning':
      return <XSkillLearningView />
    case 'responding':
      return <RespondingView />
    default:
      return null
  }
}

// ── Spawning mini-view ────────────────────────────────────────

function SpawningView() {
  const agentMessages = useProcessStore((s) => s.agentMessages)
  const uniqueAgents = useMemo(() => {
    const map = new Map<string, { name: string; role: string }>()
    for (const msg of agentMessages) {
      if (!map.has(msg.fromAgent.id)) {
        map.set(msg.fromAgent.id, {
          name: msg.fromAgent.name,
          role: msg.fromAgent.role,
        })
      }
    }
    return Array.from(map.values())
  }, [agentMessages])

  return (
    <div className="space-y-3 animate-fade-in">
      <div className="flex items-center gap-2">
        <Spinner size="sm" className="text-brand-500 dark:text-brand-400" />
        <span className="text-sm text-text-primary dark:text-gray-200">
          Spawning agents…
        </span>
      </div>
      {uniqueAgents.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {uniqueAgents.map((agent) => (
            <Badge key={agent.name} variant="brand" size="sm">
              {agent.name}
              <span className="ml-1 opacity-60 capitalize">({agent.role})</span>
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Responding mini-view ──────────────────────────────────────

function RespondingView() {
  return (
    <div className="flex items-center gap-3 py-6 animate-fade-in">
      <Send className="h-5 w-5 text-brand-500 dark:text-brand-400 animate-pulse" aria-hidden />
      <div>
        <p className="text-sm font-medium text-text-primary dark:text-gray-200">
          Streaming response…
        </p>
        <p className="text-xs text-text-muted dark:text-gray-500">
          The final answer is being generated.
        </p>
      </div>
      <div className="ml-auto flex gap-1">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-2 w-2 rounded-full bg-brand-500 dark:bg-brand-400"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{
              duration: 1.2,
              repeat: Infinity,
              delay: i * 0.2,
            }}
          />
        ))}
      </div>
    </div>
  )
}

// ── Collapsed stage preview ───────────────────────────────────

function CollapsedStageRow({
  stage,
  onExpand,
}: {
  stage: ProcessStage
  onExpand: () => void
}) {
  return (
    <button
      type="button"
      onClick={onExpand}
      className={cn(
        'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs',
        'bg-gray-50 hover:bg-gray-100 dark:bg-[#12122a] dark:hover:bg-[#1a1a3a]',
        'transition-colors',
      )}
    >
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400 shrink-0" aria-hidden />
      <span className="font-medium text-text-secondary dark:text-gray-400 capitalize">
        {stage}
      </span>
      <ChevronDown className="ml-auto h-3 w-3 text-text-muted dark:text-gray-600" aria-hidden />
    </button>
  )
}

// ── Main ProcessViewer ────────────────────────────────────────

export function ProcessViewer() {
  const {
    taskId,
    stage,
    progress,
    startTime,
    error,
  } = useProcessStore()

  const [expandedPast, setExpandedPast] = useState<ProcessStage | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  const isRunning = stage !== 'idle' && stage !== 'completed' && stage !== 'failed'
  const elapsed = useDurationTimer(startTime, isRunning)

  // Past stages that have been completed
  const currentIdx = stageIndex(stage)
  const pastStages = useMemo(
    () => ACTIVE_STAGES.filter((_, idx) => idx < currentIdx),
    [currentIdx],
  )

  // Auto-scroll content to bottom on stage change
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [stage])

  // Don't render if idle
  if (stage === 'idle') return null

  return (
    <Card
      variant="elevated"
      padding="none"
      className="flex flex-col overflow-hidden"
    >
      {/* ── Header ─────────────────────────────────── */}
      <div
        className={cn(
          'flex flex-wrap items-center gap-2 px-4 py-3 border-b',
          'border-border dark:border-[#2a2a45]',
        )}
      >
        {/* Task ID */}
        {taskId && (
          <div className="flex items-center gap-1 text-xs text-text-muted dark:text-gray-500">
            <Hash className="h-3 w-3" aria-hidden />
            <span className="font-mono truncate max-w-[120px]" title={taskId}>
              {taskId.slice(0, 8)}
            </span>
          </div>
        )}

        {/* Stage badge */}
        <Badge
          variant={
            stage === 'completed'
              ? 'success'
              : stage === 'failed'
                ? 'error'
                : 'info'
          }
          size="sm"
          dot={isRunning}
        >
          <span className="capitalize">{stage}</span>
        </Badge>

        {/* Duration */}
        {startTime && (
          <div className="flex items-center gap-1 text-xs text-text-muted dark:text-gray-500 tabular-nums">
            <Timer className="h-3 w-3" aria-hidden />
            {formatDuration(elapsed)}
          </div>
        )}

        {/* Collapse toggle */}
        <button
          type="button"
          onClick={() => setCollapsed((p) => !p)}
          className={cn(
            'ml-auto flex items-center gap-1 text-xs text-text-muted dark:text-gray-500',
            'hover:text-text-secondary dark:hover:text-gray-300 transition-colors',
          )}
          aria-label={collapsed ? 'Expand process viewer' : 'Collapse process viewer'}
        >
          {collapsed ? (
            <Eye className="h-3.5 w-3.5" />
          ) : (
            <EyeOff className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      {/* ── Stage pipeline ─────────────────────────── */}
      <div className="px-4 pt-2 border-b border-border dark:border-[#2a2a45]">
        <StagePipeline currentStage={stage} />
      </div>

      {/* ── Progress bar ───────────────────────────── */}
      <div className="px-4 py-2">
        <ProgressBar
          value={progress}
          size="sm"
          variant={
            stage === 'failed'
              ? 'error'
              : stage === 'completed'
                ? 'success'
                : 'default'
          }
          animated={isRunning}
        />
      </div>

      {/* ── Collapsible body ───────────────────────── */}
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div
              ref={contentRef}
              className="max-h-[500px] overflow-y-auto px-4 pb-4 space-y-3 scroll-smooth"
            >
              {/* Error banner */}
              {error && (
                <div
                  className={cn(
                    'flex items-start gap-2 rounded-lg p-3',
                    'bg-red-50 border border-red-200',
                    'dark:bg-red-900/20 dark:border-red-800',
                  )}
                >
                  <AlertCircle className="h-4 w-4 shrink-0 text-red-500 dark:text-red-400 mt-0.5" aria-hidden />
                  <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
                </div>
              )}

              {/* Completed banner */}
              {stage === 'completed' && (
                <div
                  className={cn(
                    'flex items-center gap-2 rounded-lg p-3',
                    'bg-emerald-50 border border-emerald-200',
                    'dark:bg-emerald-900/20 dark:border-emerald-800',
                  )}
                >
                  <CheckCircle2 className="h-4 w-4 text-emerald-500 dark:text-emerald-400" aria-hidden />
                  <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
                    Task completed in {formatDuration(elapsed)}
                  </p>
                </div>
              )}

              {/* Past stages (collapsed rows) */}
              {pastStages.length > 0 && (
                <div className="space-y-1.5">
                  {pastStages.map((pastStage) => (
                    <div key={pastStage}>
                      {expandedPast === pastStage ? (
                        <div className="space-y-2">
                          <button
                            type="button"
                            onClick={() => setExpandedPast(null)}
                            className={cn(
                              'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs',
                              'bg-brand-50 dark:bg-brand-900/20',
                              'transition-colors',
                            )}
                          >
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400 shrink-0" aria-hidden />
                            <span className="font-medium text-brand-700 dark:text-brand-400 capitalize">
                              {pastStage}
                            </span>
                            <ChevronDown className="ml-auto h-3 w-3 text-brand-500 dark:text-brand-400 rotate-180" aria-hidden />
                          </button>
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            className="pl-2 border-l-2 border-brand-200 dark:border-brand-800 overflow-hidden"
                          >
                            <StageContent stage={pastStage} />
                          </motion.div>
                        </div>
                      ) : (
                        <CollapsedStageRow
                          stage={pastStage}
                          onExpand={() => setExpandedPast(pastStage)}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Current stage content */}
              {isRunning && (
                <motion.div
                  key={stage}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3 }}
                >
                  <StageContent stage={stage} />
                </motion.div>
              )}

              {/* Show all stages when completed/failed */}
              {(stage === 'completed' || stage === 'failed') &&
                expandedPast === null && (
                  <div className="pt-1">
                    <SynthesisView />
                  </div>
                )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  )
}
