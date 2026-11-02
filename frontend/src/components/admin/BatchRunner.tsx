// ============================================================
// Nexus - Batch Runner Admin View
// Batch task execution with multi-format input and progress
// ============================================================

import { useState, useMemo } from 'react'
import { apiPost } from '../../api'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { ProgressBar } from '../ui/ProgressBar'
import { Select } from '../ui/Select'
import { Spinner } from '../ui/Spinner'

// ============================================================
// Types
// ============================================================

type InputFormat = 'lines' | 'json'

interface BatchTaskResult {
  task: string
  status: 'success' | 'error' | 'pending' | 'running' | string
  result?: unknown
  error?: string
}

interface BatchRunResponse {
  status: string
  results?: BatchTaskResult[]
  total?: number
  completed?: number
  errors?: number
}

// ============================================================
// Helpers
// ============================================================

function taskStatusVariant(status: string): 'success' | 'error' | 'warning' | 'default' {
  switch (status.toLowerCase()) {
    case 'success':
    case 'completed':
    case 'done':
      return 'success'
    case 'error':
    case 'failed':
      return 'error'
    case 'running':
    case 'pending':
    case 'in_progress':
      return 'warning'
    default:
      return 'default'
  }
}

function parseTasks(input: string, format: InputFormat): string[] {
  if (format === 'json') {
    try {
      const parsed = JSON.parse(input)
      if (Array.isArray(parsed)) {
        return parsed.filter((t) => typeof t === 'string' && t.trim()).map((t) => t.trim())
      }
      return []
    } catch {
      return []
    }
  }
  // Lines format
  return input
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
}

// ============================================================
// Main Component
// ============================================================

export function BatchRunner() {
  const [inputText, setInputText] = useState('')
  const [format, setFormat] = useState<InputFormat>('lines')
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [batchResult, setBatchResult] = useState<BatchRunResponse | null>(null)
  const [expandedTasks, setExpandedTasks] = useState<Set<number>>(new Set())

  // Parse tasks from input
  const tasks = useMemo(() => parseTasks(inputText, format), [inputText, format])

  // Validation
  const isValidInput = tasks.length > 0
  const jsonError = format === 'json' && inputText.trim() && !isValidInput
    ? 'Invalid JSON array. Expected: ["task1", "task2", ...]'
    : null

  // Run batch
  const handleRun = async () => {
    if (!isValidInput) return
    setRunning(true)
    setRunError(null)
    setBatchResult(null)
    setExpandedTasks(new Set())
    try {
      const result = await apiPost<BatchRunResponse>('/batch/run', { tasks })
      setBatchResult(result)
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Batch execution failed')
    } finally {
      setRunning(false)
    }
  }

  // Toggle task expansion
  const toggleTask = (index: number) => {
    setExpandedTasks((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  // Progress calculation
  const completedCount = batchResult?.results?.filter(
    (r) => r.status === 'success' || r.status === 'completed' || r.status === 'done' || r.status === 'error' || r.status === 'failed',
  ).length ?? 0
  const totalCount = batchResult?.results?.length ?? 0
  const successCount = batchResult?.results?.filter(
    (r) => r.status === 'success' || r.status === 'completed' || r.status === 'done',
  ).length ?? 0
  const errorCount = batchResult?.results?.filter(
    (r) => r.status === 'error' || r.status === 'failed',
  ).length ?? 0
  const progressPercent = totalCount > 0 ? (completedCount / totalCount) * 100 : 0

  // Clear results
  const handleClear = () => {
    setBatchResult(null)
    setRunError(null)
    setExpandedTasks(new Set())
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[900px] mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6 animate-fade-in">
          <div
            className={cn(
              'w-10 h-10 rounded-[12px] flex items-center justify-center',
              'bg-emerald-50 dark:bg-emerald-900/30',
            )}
          >
            <span className="text-lg" role="img" aria-label="Batch">
              🚀
            </span>
          </div>
          <div>
            <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
              Batch Runner
            </h2>
            <p className="text-[13px] text-text-muted dark:text-gray-400">
              Execute multiple tasks in a single batch
            </p>
          </div>
        </div>

        {/* Input Section */}
        <Card variant="default" padding="md" className="mb-6">
          {/* Format selector + count */}
          <div className="flex items-center justify-between mb-3">
            <div className="w-48">
              <Select
                options={[
                  { value: 'lines', label: 'One per line' },
                  { value: 'json', label: 'JSON array' },
                ]}
                value={format}
                onChange={(v) => setFormat(v as InputFormat)}
                label="Input Format"
                size="sm"
              />
            </div>
            <div className="text-right">
              <Badge
                variant={tasks.length > 0 ? 'info' : 'default'}
                size="md"
              >
                {tasks.length} task{tasks.length !== 1 ? 's' : ''}
              </Badge>
            </div>
          </div>

          {/* Textarea */}
          <div className="mb-4">
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              rows={8}
              placeholder={
                format === 'lines'
                  ? 'Enter tasks, one per line:\n\nSummarize the latest news about AI\nGenerate a weekly report for marketing\nAnalyze sales data for Q4'
                  : '[\n  "Summarize the latest news about AI",\n  "Generate a weekly report for marketing",\n  "Analyze sales data for Q4"\n]'
              }
              className={cn(
                'w-full rounded-comfortable border bg-white text-sm p-3 font-mono',
                'border-[#e5e7eb] dark:border-[#2a2a45]',
                'dark:bg-[#1a1a2e] dark:text-gray-100',
                'placeholder:text-[#8e8e93] dark:placeholder:text-gray-500',
                'focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 focus:outline-none',
                'dark:focus:border-[#60a5fa] dark:focus:ring-[#60a5fa]/20',
                'resize-y min-h-[120px]',
                jsonError && 'border-red-400 dark:border-red-500',
              )}
            />
            {jsonError && (
              <p className="mt-1.5 text-xs text-red-500 dark:text-red-400">{jsonError}</p>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <Button
              variant="primary"
              size="md"
              loading={running}
              onClick={handleRun}
              disabled={!isValidInput}
              icon={
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347a1.125 1.125 0 01-1.667-.986V5.653z" />
                </svg>
              }
            >
              Run Batch ({tasks.length} task{tasks.length !== 1 ? 's' : ''})
            </Button>
            {batchResult && (
              <Button variant="ghost" size="sm" onClick={handleClear}>
                Clear Results
              </Button>
            )}
          </div>
        </Card>

        {/* Error */}
        {runError && (
          <Card variant="default" padding="md" className="mb-6 border-red-200 dark:border-red-800">
            <p className="text-sm text-red-600 dark:text-red-400">{runError}</p>
            <Button variant="ghost" size="sm" onClick={handleRun} className="mt-2">
              Retry
            </Button>
          </Card>
        )}

        {/* Running state */}
        {running && (
          <Card variant="default" padding="lg" className="mb-6">
            <div className="flex flex-col items-center justify-center py-8 gap-4">
              <Spinner size="lg" className="text-blue-500" />
              <div className="text-center">
                <p className="text-sm font-medium text-text-dark dark:text-gray-100">
                  Executing batch...
                </p>
                <p className="text-xs text-text-muted dark:text-gray-400 mt-1">
                  Processing {tasks.length} task{tasks.length !== 1 ? 's' : ''}
                </p>
              </div>
            </div>
          </Card>
        )}

        {/* Results */}
        {batchResult && !running && (
          <div className="space-y-4 animate-fade-in">
            {/* Progress summary */}
            <Card variant="default" padding="md">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-text-dark dark:text-gray-100">
                  Batch Results
                </h3>
                <div className="flex items-center gap-2">
                  {successCount > 0 && (
                    <Badge variant="success" size="sm">
                      {successCount} succeeded
                    </Badge>
                  )}
                  {errorCount > 0 && (
                    <Badge variant="error" size="sm">
                      {errorCount} failed
                    </Badge>
                  )}
                </div>
              </div>
              <ProgressBar
                value={progressPercent}
                variant={errorCount > 0 ? (successCount > 0 ? 'warning' : 'error') : 'success'}
                size="md"
                label={`${completedCount} / ${totalCount} completed`}
                showValue
              />
            </Card>

            {/* Task result list */}
            {batchResult.results && batchResult.results.length > 0 ? (
              <div className="space-y-2">
                {batchResult.results.map((taskResult, index) => {
                  const isExpanded = expandedTasks.has(index)
                  const hasDetail = taskResult.result || taskResult.error

                  return (
                    <Card
                      key={index}
                      variant="default"
                      padding="none"
                      className="overflow-hidden"
                    >
                      {/* Task row */}
                      <button
                        type="button"
                        onClick={() => hasDetail && toggleTask(index)}
                        disabled={!hasDetail}
                        className={cn(
                          'w-full flex items-center gap-3 px-4 py-3 text-left',
                          'transition-colors',
                          hasDetail && 'hover:bg-black/[0.02] dark:hover:bg-white/[0.02] cursor-pointer',
                          !hasDetail && 'cursor-default',
                        )}
                      >
                        {/* Status indicator */}
                        <div className="shrink-0">
                          <Badge variant={taskStatusVariant(taskResult.status)} size="sm">
                            {taskResult.status}
                          </Badge>
                        </div>

                        {/* Task description */}
                        <p className="flex-1 text-sm text-text-primary dark:text-gray-200 truncate">
                          {taskResult.task}
                        </p>

                        {/* Expand chevron */}
                        {hasDetail && (
                          <svg
                            className={cn(
                              'h-4 w-4 shrink-0 text-text-muted dark:text-gray-500 transition-transform',
                              isExpanded && 'rotate-180',
                            )}
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={2}
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                          </svg>
                        )}
                      </button>

                      {/* Expanded detail */}
                      {isExpanded && hasDetail && (
                        <div
                          className={cn(
                            'px-4 pb-4 border-t',
                            'border-[#e5e7eb] dark:border-[#2a2a45]',
                          )}
                        >
                          {taskResult.error && (
                            <div className="mt-3 p-3 rounded-lg bg-red-50 dark:bg-red-900/20">
                              <p className="text-xs font-medium text-red-600 dark:text-red-400 mb-1">
                                Error
                              </p>
                              <p className="text-xs text-red-500 dark:text-red-300">
                                {taskResult.error}
                              </p>
                            </div>
                          )}
                          {taskResult.result !== undefined && taskResult.result !== null && (
                            <div className="mt-3">
                              <p className="text-xs font-medium text-text-muted dark:text-gray-400 mb-1">
                                Result
                              </p>
                              <pre
                                className={cn(
                                  'text-xs p-3 rounded-lg overflow-x-auto max-h-48',
                                  'bg-gray-50 text-gray-700',
                                  'dark:bg-[#1a1a2e] dark:text-gray-300',
                                )}
                              >
                                {typeof taskResult.result === 'string'
                                  ? taskResult.result
                                  : JSON.stringify(taskResult.result, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </Card>
                  )
                })}
              </div>
            ) : (
              <EmptyState
                icon="📋"
                title="No task results"
                description="The batch completed but returned no individual results"
              />
            )}
          </div>
        )}

        {/* Initial empty state (no results yet, no input) */}
        {!batchResult && !running && !inputText.trim() && (
          <EmptyState
            icon="🚀"
            title="Ready to run batch tasks"
            description="Enter tasks above to execute them in a single batch operation"
          />
        )}
      </div>
    </div>
  )
}
