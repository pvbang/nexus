// ============================================================
// MekongAI Nexus - ReportGenerator Component
// UI for generating reports via POST /api/v1/tasks/report
// ============================================================

import { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { apiPost } from '../../api'
import { Button } from '../ui/Button'
import { Input } from '../ui/Input'
import { Select } from '../ui/Select'
import { Card } from '../ui/Card'
import { Badge } from '../ui/Badge'
import type { Task } from '../../types/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ReportGeneratorProps {
  className?: string
}

type FormState = 'idle' | 'submitting' | 'success' | 'error'

interface ReportForm {
  topic: string
  context: string
  format: string
  maxSections: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FORMAT_OPTIONS = [
  { value: 'markdown', label: 'Markdown' },
  { value: 'docx', label: 'DOCX' },
]

const INITIAL_FORM: ReportForm = {
  topic: '',
  context: '',
  format: 'markdown',
  maxSections: 5,
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ReportGenerator({ className }: ReportGeneratorProps) {
  const [form, setForm] = useState<ReportForm>(INITIAL_FORM)
  const [state, setState] = useState<FormState>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [createdTask, setCreatedTask] = useState<Task | null>(null)

  const isValid = form.topic.trim().length > 0

  const handleSubmit = useCallback(async () => {
    if (!isValid) return

    setState('submitting')
    setErrorMessage(null)

    try {
      const body: Record<string, unknown> = {
        topic: form.topic.trim(),
        options: {
          format: form.format,
          max_sections: form.maxSections,
        },
      }
      if (form.context.trim()) {
        body.context = form.context.trim()
      }

      const task = await apiPost<Task>('/tasks/report', body)
      setCreatedTask(task)
      setState('success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create report'
      setErrorMessage(message)
      setState('error')
    }
  }, [form, isValid])

  const handleReset = useCallback(() => {
    setForm(INITIAL_FORM)
    setState('idle')
    setErrorMessage(null)
    setCreatedTask(null)
  }, [])

  const handleRetry = useCallback(() => {
    setState('idle')
    setErrorMessage(null)
  }, [])

  // ---- Success state ----
  if (state === 'success' && createdTask) {
    return (
      <div className={cn('space-y-4', className)}>
        <div className="flex flex-col items-center py-6 text-center">
          <span className="text-4xl mb-3" role="img" aria-hidden="true">📊</span>
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Report task created
          </h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Your report on &ldquo;{form.topic}&rdquo; is being generated.
          </p>

          <div className="mt-4 flex items-center gap-2">
            <Badge variant="info" dot>
              {createdTask.status}
            </Badge>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              ID: {createdTask.id.slice(0, 8)}…
            </span>
          </div>

          <div className="mt-6 flex gap-3">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.location.hash = `#/tasks/${createdTask.id}`
              }}
            >
              View Task
            </Button>
            <Button variant="secondary" size="sm" onClick={handleReset}>
              New Report
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // ---- Form state (idle / submitting / error) ----
  return (
    <div className={cn('space-y-5', className)}>
      {/* Topic */}
      <Input
        label="Topic"
        placeholder="e.g. Market analysis of AI in Southeast Asia"
        value={form.topic}
        onChange={(e) => setForm((f) => ({ ...f, topic: e.target.value }))}
        disabled={state === 'submitting'}
      />

      {/* Context */}
      <div>
        <label
          htmlFor="report-context"
          className={cn(
            'mb-1.5 block text-sm font-medium',
            'text-[#222] dark:text-gray-200',
          )}
        >
          Context <span className="text-gray-400 font-normal">(optional)</span>
        </label>
        <textarea
          id="report-context"
          rows={3}
          placeholder="Additional context or constraints for the report..."
          value={form.context}
          onChange={(e) => setForm((f) => ({ ...f, context: e.target.value }))}
          disabled={state === 'submitting'}
          className={cn(
            'w-full rounded-comfortable border bg-white text-sm transition-all duration-150',
            'placeholder:text-[#8e8e93] resize-y min-h-[80px] px-3 py-2.5',
            'border-[#e5e7eb] dark:border-[#2a2a45]',
            'hover:border-[#d1d5db] dark:hover:border-[#353560]',
            'focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 focus:outline-none',
            'dark:bg-[#1a1a2e] dark:text-gray-100 dark:placeholder:text-gray-500',
            'dark:focus:border-[#60a5fa] dark:focus:ring-[#60a5fa]/20',
            state === 'submitting' && 'opacity-50 cursor-not-allowed',
          )}
        />
      </div>

      {/* Options row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Select
          label="Format"
          options={FORMAT_OPTIONS}
          value={form.format}
          onChange={(v) => setForm((f) => ({ ...f, format: v }))}
          disabled={state === 'submitting'}
        />

        <div>
          <label
            htmlFor="max-sections"
            className={cn(
              'mb-1.5 block text-sm font-medium',
              'text-[#222] dark:text-gray-200',
            )}
          >
            Max Sections: {form.maxSections}
          </label>
          <input
            id="max-sections"
            type="range"
            min={2}
            max={10}
            step={1}
            value={form.maxSections}
            onChange={(e) =>
              setForm((f) => ({ ...f, maxSections: Number(e.target.value) }))
            }
            disabled={state === 'submitting'}
            className={cn(
              'w-full h-2 rounded-full appearance-none cursor-pointer',
              'bg-gray-200 dark:bg-[#2a2a45]',
              'accent-[#3b82f6] dark:accent-[#60a5fa]',
              state === 'submitting' && 'opacity-50 cursor-not-allowed',
            )}
          />
          <div className="flex justify-between mt-1 text-[10px] text-gray-400 dark:text-gray-500">
            <span>2</span>
            <span>10</span>
          </div>
        </div>
      </div>

      {/* Error message */}
      {state === 'error' && errorMessage && (
        <Card variant="default" padding="sm" className="border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20">
          <div className="flex items-start gap-2">
            <span className="text-red-500 shrink-0">⚠️</span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to generate report
              </p>
              <p className="text-xs text-red-600 dark:text-red-400/80 mt-0.5">
                {errorMessage}
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Actions */}
      <div className="flex justify-end gap-3 pt-1">
        {state === 'error' && (
          <Button variant="ghost" size="sm" onClick={handleRetry}>
            Back to form
          </Button>
        )}
        <Button
          variant="primary"
          size="md"
          loading={state === 'submitting'}
          disabled={!isValid}
          onClick={handleSubmit}
          icon={
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
            </svg>
          }
        >
          Generate Report
        </Button>
      </div>
    </div>
  )
}
