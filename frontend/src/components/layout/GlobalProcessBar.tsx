// ============================================================
// MekongAI Nexus - GlobalProcessBar
// Always-visible bottom bar showing active task status
// ============================================================

import { useState, useEffect, useMemo, useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Loader2,
  CheckCircle2,
  AlertCircle,
  Users,
  Timer,
  Maximize2,
  Minimize2,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import type { ProcessStage } from '../../types/process'
import { ProcessOverlay } from './ProcessOverlay'

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

// ── Mini stage pills ──────────────────────────────────────────

const PIPELINE_STAGES: { key: ProcessStage; label: string }[] = [
  { key: 'planning', label: 'Plan' },
  { key: 'spawning', label: 'Spawn' },
  { key: 'communicating', label: 'Comms' },
  { key: 'executing', label: 'Exec' },
  { key: 'debating', label: 'Debate' },
  { key: 'synthesizing', label: 'Synth' },
  { key: 'learning', label: 'Learn' },
  { key: 'responding', label: 'Reply' },
]

function stageIdx(stage: ProcessStage): number {
  return PIPELINE_STAGES.findIndex((s) => s.key === stage)
}

function MiniPipeline({ currentStage }: { currentStage: ProcessStage }) {
  const activeIdx = stageIdx(currentStage)

  return (
    <div className="hidden sm:flex items-center gap-0.5">
      {PIPELINE_STAGES.map((s, idx) => {
        const isCompleted = activeIdx > idx
        const isActive = activeIdx === idx
        return (
          <div key={s.key} className="flex items-center">
            <span
              className={cn(
                'text-[10px] px-1.5 py-0.5 rounded-full font-medium transition-all whitespace-nowrap',
                isCompleted && 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400',
                isActive && 'bg-blue-100 text-blue-700 ring-1 ring-blue-400/40 dark:bg-blue-900/40 dark:text-blue-300 dark:ring-blue-400/30',
                !isCompleted && !isActive && 'text-gray-400 dark:text-gray-600',
              )}
            >
              {isActive ? `[${s.label}]` : s.label}
            </span>
            {idx < PIPELINE_STAGES.length - 1 && (
              <span
                className={cn(
                  'mx-0.5 text-[8px]',
                  isCompleted ? 'text-emerald-400 dark:text-emerald-600' : 'text-gray-300 dark:text-gray-700',
                )}
              >
                ▸
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Main GlobalProcessBar ─────────────────────────────────────

type BarVisibility = 'hidden' | 'active' | 'completed' | 'failed'

export function GlobalProcessBar() {
  const {
    taskId,
    stage,
    progress,
    startTime,
    error,
    agentMessages,
    plan,
    processes,
  } = useProcessStore()

  // Count active (in-progress) tasks across all processes
  const activeCount = useMemo(() => {
    return Object.values(processes).filter(
      (p) => p.stage !== 'idle' && p.stage !== 'completed' && p.stage !== 'failed',
    ).length
  }, [processes])

  const [overlayOpen, setOverlayOpen] = useState(false)
  const [barVisibility, setBarVisibility] = useState<BarVisibility>('hidden')

  const isRunning = stage !== 'idle' && stage !== 'completed' && stage !== 'failed'
  const elapsed = useDurationTimer(startTime, isRunning)

  // Count unique agents
  const agentCount = useMemo(() => {
    const ids = new Set<string>()
    for (const msg of agentMessages) {
      ids.add(msg.fromAgent.id)
    }
    return ids.size
  }, [agentMessages])

  // Task title from plan or taskId
  const taskTitle = useMemo(() => {
    if (plan?.strategy) return plan.strategy
    if (taskId) return `Task ${taskId.slice(0, 8)}…`
    return 'Processing…'
  }, [plan, taskId])

  // Manage bar visibility with auto-hide after completion/failure
  useEffect(() => {
    if (isRunning) {
      setBarVisibility('active')
      return
    }

    if (stage === 'completed') {
      setBarVisibility('completed')
      const timer = setTimeout(() => setBarVisibility('hidden'), 8000)
      return () => clearTimeout(timer)
    }

    if (stage === 'failed') {
      setBarVisibility('failed')
      const timer = setTimeout(() => setBarVisibility('hidden'), 10000)
      return () => clearTimeout(timer)
    }

    setBarVisibility('hidden')
  }, [stage, isRunning])

  const handleToggleOverlay = useCallback(() => {
    setOverlayOpen((prev) => !prev)
  }, [])

  const handleCloseOverlay = useCallback(() => {
    setOverlayOpen(false)
  }, [])

  const isVisible = barVisibility !== 'hidden'

  return (
    <>
      {/* Process Overlay (slide-up panel) */}
      <ProcessOverlay
        open={overlayOpen}
        onClose={handleCloseOverlay}
        taskId={taskId}
      />

      {/* Bottom bar */}
      <AnimatePresence>
        {isVisible && (
          <motion.div
            initial={{ y: '100%', opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: '100%', opacity: 0 }}
            transition={{ type: 'spring', damping: 25, stiffness: 300 }}
            className={cn(
              'fixed bottom-0 left-0 right-0 z-40',
              'border-t backdrop-blur-sm',
              barVisibility === 'completed' && [
                'bg-emerald-50/95 border-emerald-200',
                'dark:bg-emerald-950/90 dark:border-emerald-800',
              ],
              barVisibility === 'failed' && [
                'bg-red-50/95 border-red-200',
                'dark:bg-red-950/90 dark:border-red-800',
              ],
              barVisibility === 'active' && [
                'bg-white/95 border-gray-200',
                'dark:bg-[#0d0d1a]/95 dark:border-[#2a2a45]',
              ],
            )}
          >
            <div className="flex items-center gap-3 px-4 py-2 max-w-screen-2xl mx-auto">
              {/* Left: Status icon + title */}
              <div className="flex items-center gap-2 min-w-0 shrink">
                {barVisibility === 'active' && (
                  <Loader2
                    className="h-4 w-4 text-blue-500 dark:text-blue-400 animate-spin shrink-0"
                    aria-hidden
                  />
                )}
                {barVisibility === 'completed' && (
                  <CheckCircle2
                    className="h-4 w-4 text-emerald-500 dark:text-emerald-400 shrink-0"
                    aria-hidden
                  />
                )}
                {barVisibility === 'failed' && (
                  <AlertCircle
                    className="h-4 w-4 text-red-500 dark:text-red-400 shrink-0"
                    aria-hidden
                  />
                )}

                <span
                  className={cn(
                    'text-sm font-medium truncate max-w-[200px]',
                    barVisibility === 'completed' && 'text-emerald-700 dark:text-emerald-300',
                    barVisibility === 'failed' && 'text-red-700 dark:text-red-300',
                    barVisibility === 'active' && 'text-gray-800 dark:text-gray-200',
                  )}
                  title={taskTitle}
                >
                  {barVisibility === 'completed'
                    ? '✅ Task completed'
                    : barVisibility === 'failed'
                      ? `❌ Task failed${error ? `: ${error}` : ''}`
                      : taskTitle}
                </span>

                {/* Active task count badge (multi-task indicator) */}
                {activeCount > 1 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 text-[10px] font-semibold dark:bg-blue-900/40 dark:text-blue-300">
                    {activeCount} tasks
                  </span>
                )}
              </div>

              {/* Center: Mini pipeline (only when active) */}
              {barVisibility === 'active' && (
                <div className="flex-1 flex justify-center min-w-0">
                  <MiniPipeline currentStage={stage} />
                </div>
              )}

              {/* Spacer when not active */}
              {barVisibility !== 'active' && <div className="flex-1" />}

              {/* Right: Progress + stats + expand */}
              <div className="flex items-center gap-3 shrink-0">
                {/* Progress bar (compact) */}
                {barVisibility === 'active' && (
                  <div className="hidden md:flex items-center gap-2">
                    <div className="w-20 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-blue-500 dark:bg-blue-400 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${progress}%` }}
                        transition={{ duration: 0.5, ease: 'easeOut' }}
                      />
                    </div>
                    <span className="text-xs font-mono text-gray-500 dark:text-gray-400 tabular-nums w-8">
                      {progress}%
                    </span>
                  </div>
                )}

                {/* Agent count */}
                {barVisibility === 'active' && agentCount > 0 && (
                  <div className="hidden sm:flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                    <Users className="h-3 w-3" aria-hidden />
                    <span className="tabular-nums">{agentCount}</span>
                  </div>
                )}

                {/* Duration */}
                {startTime && (
                  <div className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 tabular-nums">
                    <Timer className="h-3 w-3" aria-hidden />
                    <span>{formatDuration(elapsed)}</span>
                  </div>
                )}

                {/* Expand/collapse overlay button */}
                {barVisibility === 'active' && (
                  <button
                    type="button"
                    onClick={handleToggleOverlay}
                    className={cn(
                      'p-1.5 rounded-md transition-colors',
                      'hover:bg-gray-100 dark:hover:bg-gray-800',
                      'text-gray-500 dark:text-gray-400',
                      overlayOpen && 'bg-gray-100 dark:bg-gray-800 text-blue-500 dark:text-blue-400',
                    )}
                    aria-label={overlayOpen ? 'Collapse process overlay' : 'Expand process overlay'}
                  >
                    {overlayOpen ? (
                      <Minimize2 className="h-4 w-4" />
                    ) : (
                      <Maximize2 className="h-4 w-4" />
                    )}
                  </button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
