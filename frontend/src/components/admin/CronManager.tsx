// ============================================================
// Nexus - Cron Manager Admin View
// Cron job management with create, list, and delete
// ============================================================

import { useState, useCallback } from 'react'
import { apiGet, apiPost, apiDelete } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { Input } from '../ui/Input'
import { Modal } from '../ui/Modal'
import { Skeleton } from '../ui/Skeleton'

// ============================================================
// Types
// ============================================================

interface CronJob {
  id: string
  name: string
  cron_expression: string
  task_description?: string
  channel?: string
  status: 'active' | 'paused' | 'disabled' | string
  next_run?: string
  last_run?: string
  last_result?: 'success' | 'failure' | 'pending' | string
  created_at?: string
}

interface CreateJobPayload {
  name: string
  cron_expression: string
  task_description?: string
  channel?: string
}

// ============================================================
// Helpers
// ============================================================

function formatDateTime(dateStr?: string): string {
  if (!dateStr) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(dateStr))
  } catch {
    return dateStr
  }
}

function statusVariant(status: string): 'success' | 'warning' | 'error' | 'default' {
  switch (status.toLowerCase()) {
    case 'active':
      return 'success'
    case 'paused':
      return 'warning'
    case 'disabled':
      return 'error'
    default:
      return 'default'
  }
}

function resultVariant(result?: string): 'success' | 'error' | 'warning' | 'default' {
  if (!result) return 'default'
  switch (result.toLowerCase()) {
    case 'success':
      return 'success'
    case 'failure':
    case 'error':
      return 'error'
    case 'pending':
    case 'running':
      return 'warning'
    default:
      return 'default'
  }
}

// ============================================================
// Skeleton loader for table rows
// ============================================================

function CronRowSkeleton() {
  return (
    <tr>
      <td className="px-4 py-3"><Skeleton className="h-4 w-28" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-24" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-32" /></td>
      <td className="px-4 py-3"><Skeleton className="h-5 w-16" variant="rectangular" /></td>
      <td className="px-4 py-3"><Skeleton className="h-5 w-16" variant="rectangular" /></td>
      <td className="px-4 py-3"><Skeleton className="h-8 w-8" variant="rectangular" /></td>
    </tr>
  )
}

// ============================================================
// Main Component
// ============================================================

export function CronManager() {
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  // Create form state
  const [formName, setFormName] = useState('')
  const [formCron, setFormCron] = useState('')
  const [formTask, setFormTask] = useState('')
  const [formChannel, setFormChannel] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // Fetch cron jobs with polling (10s)
  const { data: jobs, loading, error, refresh } = usePolling<CronJob[]>({
    fetcher: useCallback(() => apiGet<CronJob[]>('/cron/jobs'), []),
    interval: 10_000,
  })

  // Create job
  const handleCreate = async () => {
    if (!formName.trim() || !formCron.trim()) return
    setCreating(true)
    setCreateError(null)
    try {
      const payload: CreateJobPayload = {
        name: formName.trim(),
        cron_expression: formCron.trim(),
      }
      if (formTask.trim()) payload.task_description = formTask.trim()
      if (formChannel.trim()) payload.channel = formChannel.trim()

      await apiPost('/cron/jobs', payload)
      setCreateModalOpen(false)
      resetForm()
      await refresh()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create job')
    } finally {
      setCreating(false)
    }
  }

  // Delete job
  const handleDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await apiDelete(`/cron/jobs/${encodeURIComponent(id)}`)
      setConfirmDeleteId(null)
      await refresh()
    } catch {
      // Error is handled silently; user can retry
    } finally {
      setDeletingId(null)
    }
  }

  // Reset create form
  const resetForm = () => {
    setFormName('')
    setFormCron('')
    setFormTask('')
    setFormChannel('')
    setCreateError(null)
  }

  const openCreateModal = () => {
    resetForm()
    setCreateModalOpen(true)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6 animate-fade-in">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'w-10 h-10 rounded-[12px] flex items-center justify-center',
                'bg-amber-50 dark:bg-amber-900/30',
              )}
            >
              <span className="text-lg" role="img" aria-label="Cron">
                ⏰
              </span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
                  Cron Manager
                </h2>
                {jobs && (
                  <Badge variant="warning" size="sm">
                    {jobs.length} jobs
                  </Badge>
                )}
              </div>
              <p className="text-[13px] text-text-muted dark:text-gray-400">
                Schedule and manage recurring tasks
              </p>
            </div>
          </div>

          <Button
            variant="primary"
            size="sm"
            onClick={openCreateModal}
            icon={
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
            }
          >
            Create Job
          </Button>
        </div>

        {/* Error state */}
        {error && (
          <Card variant="default" padding="md" className="mb-6 border-red-200 dark:border-red-800">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <Button variant="ghost" size="sm" onClick={refresh} className="mt-2">
              Retry
            </Button>
          </Card>
        )}

        {/* Loading skeleton */}
        {loading && !jobs && (
          <Card variant="default" padding="none">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#e5e7eb] dark:border-[#2a2a45]">
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Schedule</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Next Run</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Last Result</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: 3 }).map((_, i) => (
                    <CronRowSkeleton key={i} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* Empty state */}
        {!loading && jobs && jobs.length === 0 && (
          <EmptyState
            icon="⏰"
            title="No cron jobs configured"
            description="Create a scheduled job to automate recurring tasks"
            action={{ label: 'Create Job', onClick: openCreateModal }}
          />
        )}

        {/* Jobs table */}
        {jobs && jobs.length > 0 && (
          <Card variant="default" padding="none" className="animate-fade-in">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#e5e7eb] dark:border-[#2a2a45]">
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Schedule</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Next Run</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Last Result</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#e5e7eb] dark:divide-[#2a2a45]">
                  {jobs.map((job) => (
                    <tr
                      key={job.id}
                      className="hover:bg-black/[0.02] dark:hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="px-4 py-3">
                        <div>
                          <p className="font-medium text-text-dark dark:text-gray-100">
                            {job.name}
                          </p>
                          {job.task_description && (
                            <p className="text-xs text-text-muted dark:text-gray-400 line-clamp-1 mt-0.5">
                              {job.task_description}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <code
                          className={cn(
                            'text-xs px-2 py-1 rounded font-mono',
                            'bg-gray-100 text-gray-700',
                            'dark:bg-[#2a2a45] dark:text-gray-300',
                          )}
                        >
                          {job.cron_expression}
                        </code>
                      </td>
                      <td className="px-4 py-3 text-xs text-text-secondary dark:text-gray-300">
                        {formatDateTime(job.next_run)}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={statusVariant(job.status)} size="sm" dot>
                          {job.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        {job.last_result ? (
                          <Badge variant={resultVariant(job.last_result)} size="sm">
                            {job.last_result}
                          </Badge>
                        ) : (
                          <span className="text-xs text-text-muted dark:text-gray-500">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {confirmDeleteId === job.id ? (
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="danger"
                              size="sm"
                              loading={deletingId === job.id}
                              onClick={() => handleDelete(job.id)}
                            >
                              Confirm
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setConfirmDeleteId(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            iconOnly
                            onClick={() => setConfirmDeleteId(job.id)}
                            aria-label={`Delete job ${job.name}`}
                            icon={
                              <svg className="h-4 w-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"
                                />
                              </svg>
                            }
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* Create Job Modal */}
        <Modal
          open={createModalOpen}
          onClose={() => setCreateModalOpen(false)}
          title="Create Cron Job"
          size="md"
        >
          <div className="space-y-4">
            <Input
              label="Job Name"
              placeholder="e.g., Daily Report"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
            />

            <Input
              label="Cron Expression"
              placeholder="e.g., 0 9 * * *"
              value={formCron}
              onChange={(e) => setFormCron(e.target.value)}
              helperText="Format: minute hour day month weekday (e.g., '0 9 * * *' = daily at 9 AM)"
            />

            <div>
              <label className="block text-sm font-medium text-[#222] dark:text-gray-200 mb-1.5">
                Task Description
              </label>
              <textarea
                value={formTask}
                onChange={(e) => setFormTask(e.target.value)}
                rows={3}
                placeholder="Describe the task to execute on each run..."
                className={cn(
                  'w-full rounded-comfortable border bg-white text-sm p-3',
                  'border-[#e5e7eb] dark:border-[#2a2a45]',
                  'dark:bg-[#1a1a2e] dark:text-gray-100',
                  'placeholder:text-[#8e8e93] dark:placeholder:text-gray-500',
                  'focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 focus:outline-none',
                  'dark:focus:border-[#60a5fa] dark:focus:ring-[#60a5fa]/20',
                )}
              />
            </div>

            <Input
              label="Channel / Delivery Target"
              placeholder="e.g., slack, email, webhook"
              value={formChannel}
              onChange={(e) => setFormChannel(e.target.value)}
              helperText="Optional: where to send the result"
            />

            {createError && (
              <div className="p-3 rounded-lg bg-red-50 border border-red-200 dark:bg-red-900/20 dark:border-red-800">
                <p className="text-sm text-red-600 dark:text-red-400">{createError}</p>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" size="md" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="md"
                loading={creating}
                onClick={handleCreate}
                disabled={!formName.trim() || !formCron.trim()}
              >
                Create Job
              </Button>
            </div>
          </div>
        </Modal>
      </div>
    </div>
  )
}
