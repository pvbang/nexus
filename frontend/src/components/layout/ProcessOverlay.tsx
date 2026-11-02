// ============================================================
// Nexus - ProcessOverlay
// Slide-up overlay showing full ProcessViewer + TaskFlowGraph
// ============================================================

import { useState, useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Activity, GitBranch, ExternalLink } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { cn } from '../../lib/cn'
import { ProcessViewer } from '../process/ProcessViewer'
import { TaskFlowGraph } from '../process/TaskFlowGraph'

// ── Types ─────────────────────────────────────────────────────

type OverlayTab = 'viewer' | 'flow'

interface ProcessOverlayProps {
  open: boolean
  onClose: () => void
  taskId: string | null
}

// ── Main component ────────────────────────────────────────────

export function ProcessOverlay({ open, onClose, taskId }: ProcessOverlayProps) {
  const [activeTab, setActiveTab] = useState<OverlayTab>('viewer')
  const navigate = useNavigate()

  const handleViewFull = useCallback(() => {
    if (taskId) {
      onClose()
      navigate(`/tasks/${taskId}`)
    }
  }, [taskId, navigate, onClose])

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-[49] bg-black/30 dark:bg-black/50"
            onClick={onClose}
            aria-hidden
          />

          {/* Overlay panel */}
          <motion.div
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 300 }}
            className={cn(
              'fixed bottom-10 left-0 right-0 z-50',
              'max-h-[60vh] flex flex-col',
              'rounded-t-2xl shadow-2xl border-t border-x overflow-hidden',
              'bg-white border-gray-200',
              'dark:bg-[#0d0d1a] dark:border-[#2a2a45]',
            )}
          >
            {/* Drag handle */}
            <div className="flex justify-center pt-2 pb-1">
              <div className="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-700" />
            </div>

            {/* Header */}
            <div
              className={cn(
                'flex items-center gap-2 px-4 pb-2 border-b',
                'border-gray-200 dark:border-[#2a2a45]',
              )}
            >
              {/* Tabs */}
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setActiveTab('viewer')}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                    activeTab === 'viewer'
                      ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                      : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800',
                  )}
                >
                  <Activity className="h-3 w-3" aria-hidden />
                  Process
                </button>
                {taskId && (
                  <button
                    type="button"
                    onClick={() => setActiveTab('flow')}
                    className={cn(
                      'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                      activeTab === 'flow'
                        ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                        : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800',
                    )}
                  >
                    <GitBranch className="h-3 w-3" aria-hidden />
                    Flow Graph
                  </button>
                )}
              </div>

              <div className="flex-1" />

              {/* View full link */}
              {taskId && (
                <button
                  type="button"
                  onClick={handleViewFull}
                  className={cn(
                    'flex items-center gap-1 px-2 py-1 rounded-md text-xs',
                    'text-gray-500 hover:text-blue-600 hover:bg-blue-50 transition-colors',
                    'dark:text-gray-400 dark:hover:text-blue-400 dark:hover:bg-blue-900/20',
                  )}
                >
                  <ExternalLink className="h-3 w-3" aria-hidden />
                  View full
                </button>
              )}

              {/* Close button */}
              <button
                type="button"
                onClick={onClose}
                className={cn(
                  'p-1.5 rounded-md transition-colors',
                  'text-gray-400 hover:text-gray-600 hover:bg-gray-100',
                  'dark:text-gray-500 dark:hover:text-gray-300 dark:hover:bg-gray-800',
                )}
                aria-label="Close overlay"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Content area */}
            <div className="flex-1 overflow-y-auto">
              {activeTab === 'viewer' && (
                <div className="p-4">
                  <ProcessViewer />
                </div>
              )}
              {activeTab === 'flow' && taskId && (
                <div className="p-4">
                  <TaskFlowGraph taskId={taskId} className="h-[45vh]" />
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
