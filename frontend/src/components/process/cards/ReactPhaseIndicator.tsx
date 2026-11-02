// ============================================================
// Nexus - ReactPhaseIndicator
// Visual pipeline indicator for ReAct loop phases showing
// completed, active, and pending phases with animations.
// Enhanced: smooth spring transitions, glowing active state,
// gradient connectors, and phase completion celebrations.
// ============================================================

import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../../lib/cn'

// ── Props ─────────────────────────────────────────────────────

interface ReactPhaseIndicatorProps {
  currentPhase: string
  round?: number
}

// ── Constants ─────────────────────────────────────────────────

const PHASES = [
  { key: 'plan', label: 'Plan', icon: '📋', color: 'from-blue-400 to-blue-600' },
  { key: 'act', label: 'Act', icon: '⚡', color: 'from-amber-400 to-amber-600' },
  { key: 'observe', label: 'Observe', icon: '👁️', color: 'from-cyan-400 to-cyan-600' },
  { key: 'adapt', label: 'Adapt', icon: '🔄', color: 'from-purple-400 to-purple-600' },
  { key: 'reflect', label: 'Reflect', icon: '🪞', color: 'from-indigo-400 to-indigo-600' },
  { key: 'synthesize', label: 'Synthesize', icon: '🧩', color: 'from-emerald-400 to-emerald-600' },
]

// ── Spring Transition Configs ─────────────────────────────────

const springTransition = {
  type: 'spring' as const,
  stiffness: 400,
  damping: 25,
  mass: 0.8,
}

const containerVariants = {
  hidden: { opacity: 0, y: 12, scale: 0.97 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      ...springTransition,
      staggerChildren: 0.04,
    },
  },
}

const phaseNodeVariants = {
  hidden: { opacity: 0, scale: 0.6 },
  visible: {
    opacity: 1,
    scale: 1,
    transition: springTransition,
  },
}

// ── Component ─────────────────────────────────────────────────

export function ReactPhaseIndicator({ currentPhase, round }: ReactPhaseIndicatorProps) {
  const normalizedPhase = (currentPhase || '').toLowerCase()
  // When phase is "completed" or "completed", mark all phases as completed
  const isDone = normalizedPhase === 'done' || normalizedPhase === 'completed'
  const currentIndex = isDone
    ? PHASES.length // All phases completed — index beyond last phase
    : PHASES.findIndex((p) => p.key === normalizedPhase)

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className={cn(
        'rounded-xl border overflow-hidden px-4 py-3',
        'bg-gradient-to-r from-slate-50/90 via-white/80 to-gray-50/90',
        'border-slate-200/80 shadow-sm backdrop-blur-sm',
        'dark:from-slate-900/40 dark:via-[#1a1a2e]/60 dark:to-gray-900/40',
        'dark:border-slate-700/60 dark:shadow-lg dark:shadow-black/10',
      )}
    >
      {/* Header */}
      <motion.div
        className="flex items-center gap-2 mb-2.5"
        initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 0.1, ...springTransition }}
      >
        <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
          ⚛️ ReAct Loop
        </span>
        <AnimatePresence mode="wait">
          {round != null && (
            <motion.span
              key={`round-${round}`}
              initial={{ opacity: 0, scale: 0.7, y: -4 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.7, y: 4 }}
              transition={{ type: 'spring', stiffness: 500, damping: 25 }}
              className={cn(
                'inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold leading-none',
                'bg-gradient-to-r from-blue-100 to-indigo-100 text-blue-700',
                'dark:from-blue-900/50 dark:to-indigo-900/50 dark:text-blue-300',
                'shadow-sm',
              )}
            >
              Round {round}
            </motion.span>
          )}
        </AnimatePresence>
        {isDone && (
          <motion.span
            initial={{ opacity: 0, scale: 0.5 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: 'spring', stiffness: 400, damping: 15 }}
            className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 ml-auto"
          >
            ✓ Complete
          </motion.span>
        )}
      </motion.div>

      {/* Phase pipeline */}
      <div className="flex items-center gap-0" role="list" aria-label="ReAct loop phases">
        {PHASES.map((phase, idx) => {
          const isCompleted = idx < currentIndex
          const isActive = idx === currentIndex
          const isPending = idx > currentIndex

          return (
            <motion.div
              key={phase.key}
              variants={phaseNodeVariants}
              className="flex items-center flex-1 min-w-0"
              role="listitem"
              aria-current={isActive ? 'step' : undefined}
            >
              {/* Phase node */}
              <div className="flex flex-col items-center gap-1 flex-1 min-w-0">
                {/* Circle indicator */}
                <div className="relative">
                  <motion.div
                    animate={{
                      scale: isActive ? 1 : 1,
                      boxShadow: isActive
                        ? '0 0 12px rgba(59, 130, 246, 0.5), 0 0 24px rgba(59, 130, 246, 0.2)'
                        : isCompleted
                          ? '0 2px 4px rgba(34, 197, 94, 0.2)'
                          : '0 1px 2px rgba(0, 0, 0, 0.05)',
                    }}
                    transition={springTransition}
                    className={cn(
                      'w-7 h-7 rounded-full flex items-center justify-center text-[12px] transition-all duration-300',
                      isCompleted && 'bg-gradient-to-br from-emerald-400 to-emerald-600 dark:from-emerald-500 dark:to-emerald-700',
                      isActive && 'bg-gradient-to-br from-blue-400 to-blue-600 dark:from-blue-500 dark:to-blue-700',
                      isPending && 'bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700',
                    )}
                  >
                    <AnimatePresence mode="wait">
                      {isCompleted ? (
                        <motion.span
                          key="check"
                          initial={{ scale: 0, rotate: -45 }}
                          animate={{ scale: 1, rotate: 0 }}
                          transition={{ type: 'spring', stiffness: 500, damping: 20 }}
                          className="text-white text-[10px] font-bold"
                        >
                          ✓
                        </motion.span>
                      ) : (
                        <motion.span
                          key="icon"
                          initial={{ scale: 0.5, opacity: 0 }}
                          animate={{ scale: 1, opacity: isActive ? 1 : 0.5 }}
                          transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                          className={cn(
                            isActive ? 'grayscale-0' : 'grayscale opacity-50',
                          )}
                        >
                          {phase.icon}
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </motion.div>

                  {/* Active ring animation — pulsing glow */}
                  {isActive && (
                    <>
                      <motion.span
                        className="absolute inset-0 rounded-full bg-blue-400/20 dark:bg-blue-500/20"
                        animate={{
                          scale: [1, 1.6, 1],
                          opacity: [0.6, 0, 0.6],
                        }}
                        transition={{
                          duration: 2,
                          repeat: Infinity,
                          ease: 'easeInOut',
                        }}
                      />
                      <motion.span
                        className="absolute inset-0 rounded-full bg-blue-400/10 dark:bg-blue-500/10"
                        animate={{
                          scale: [1, 2, 1],
                          opacity: [0.4, 0, 0.4],
                        }}
                        transition={{
                          duration: 2,
                          repeat: Infinity,
                          ease: 'easeInOut',
                          delay: 0.3,
                        }}
                      />
                    </>
                  )}

                  {/* Completion celebration burst */}
                  {isCompleted && idx === currentIndex - 1 && (
                    <motion.span
                      className="absolute inset-0 rounded-full bg-emerald-400/30"
                      initial={{ scale: 1, opacity: 0.6 }}
                      animate={{ scale: 2, opacity: 0 }}
                      transition={{ duration: 0.6, ease: 'easeOut' }}
                    />
                  )}
                </div>

                {/* Label */}
                <motion.span
                  animate={{
                    color: isCompleted
                      ? 'rgb(16, 185, 129)'
                      : isActive
                        ? 'rgb(59, 130, 246)'
                        : 'rgb(156, 163, 175)',
                  }}
                  transition={{ duration: 0.3 }}
                  className={cn(
                    'text-[9px] font-semibold leading-none text-center truncate w-full',
                  )}
                >
                  {phase.label}
                </motion.span>
              </div>

              {/* Connector line */}
              {idx < PHASES.length - 1 && (
                <div className="relative w-3 sm:w-5 shrink-0 -mt-3">
                  {/* Background track */}
                  <div className="h-[2px] w-full rounded-full bg-gray-200 dark:bg-gray-700" />
                  {/* Filled progress */}
                  <motion.div
                    className="absolute inset-y-0 left-0 h-[2px] rounded-full bg-gradient-to-r from-emerald-400 to-emerald-500 dark:from-emerald-500 dark:to-emerald-600"
                    initial={{ width: '0%' }}
                    animate={{
                      width: idx < currentIndex ? '100%' : '0%',
                    }}
                    transition={{
                      type: 'spring',
                      stiffness: 200,
                      damping: 20,
                      delay: idx * 0.05,
                    }}
                  />
                  {/* Active flowing dot */}
                  {idx === currentIndex - 1 && !isDone && (
                    <motion.div
                      className="absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-blue-500"
                      animate={{ x: [0, 12, 20] }}
                      transition={{
                        duration: 1.5,
                        repeat: Infinity,
                        ease: 'easeInOut',
                      }}
                    />
                  )}
                </div>
              )}
            </motion.div>
          )
        })}
      </div>
    </motion.div>
  )
}
