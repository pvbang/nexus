// ============================================================
// MekongAI Nexus - SubtaskTree Component
// Hierarchical subtask display with expand/collapse tree view
// ============================================================

import { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { ProgressBar } from '../ui/ProgressBar'
import { EmptyState } from '../ui/EmptyState'
import type { Task, TaskSubtaskSummary } from '../../types/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SubtaskNode {
  id: string
  title: string
  status: string
  agent?: string
  children?: SubtaskNode[]
}

interface SubtaskTreeProps {
  task: Task
  className?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_VARIANT: Record<string, 'default' | 'info' | 'success' | 'error' | 'warning'> = {
  pending: 'default',
  queued: 'default',
  planning: 'info',
  decomposing: 'warning',
  executing: 'info',
  running: 'info',
  completed: 'success',
  done: 'success',
  failed: 'error',
  cancelled: 'error',
}

const STATUS_ICON: Record<string, string> = {
  pending: '⏳',
  queued: '⏳',
  planning: '📋',
  decomposing: '🔬',
  executing: '⚡',
  running: '⚡',
  completed: '✅',
  done: '✅',
  failed: '❌',
  cancelled: '🚫',
}

function getVariant(status: string): NonNullable<'default' | 'info' | 'success' | 'error' | 'warning'> {
  return STATUS_VARIANT[status] ?? 'default'
}

function getIcon(status: string): string {
  return STATUS_ICON[status] ?? '❓'
}

/**
 * Build subtask nodes using the best available data source:
 * 1. task.subtasks (realtime from DB, enriched by backend) — preferred
 * 2. task.result (static snapshot from decomposition time) — fallback
 */
function buildSubtaskNodes(task: Task): SubtaskNode[] {
  // If backend provides realtime subtasks array, use it (most accurate)
  if (task.subtasks && task.subtasks.length > 0) {
    return task.subtasks.map((s: TaskSubtaskSummary) => ({
      id: s.id,
      title: s.title,
      status: s.status,
    }))
  }
  // Fallback to result-based extraction (stale but still useful for plan display)
  return extractSubtasks(task.result)
}

/**
 * Extract subtask tree from task.result.
 * Supports multiple shapes: { subtasks: [...] }, { plan: { steps: [...] } }, etc.
 */
function extractSubtasks(result: Record<string, unknown> | undefined): SubtaskNode[] {
  if (!result) return []

  // Direct subtasks array
  if (Array.isArray(result.subtasks)) {
    return normalizeNodes(result.subtasks)
  }

  // Plan object with steps
  if (result.plan && typeof result.plan === 'object') {
    const plan = result.plan as Record<string, unknown>
    if (Array.isArray(plan.steps)) {
      return normalizeNodes(plan.steps)
    }
    if (Array.isArray(plan.subtasks)) {
      return normalizeNodes(plan.subtasks)
    }
  }

  // Steps at root
  if (Array.isArray(result.steps)) {
    return normalizeNodes(result.steps)
  }

  return []
}

function normalizeNodes(items: unknown[]): SubtaskNode[] {
  return items.map((item, idx) => {
    if (typeof item === 'string') {
      return { id: `step-${idx}`, title: item, status: 'pending' }
    }
    if (item && typeof item === 'object') {
      const obj = item as Record<string, unknown>
      return {
        id: String(obj.id ?? `step-${idx}`),
        title: String(obj.title ?? obj.name ?? obj.description ?? `Step ${idx + 1}`),
        status: String(obj.status ?? 'pending'),
        agent: obj.agent ? String(obj.agent) : obj.agent_name ? String(obj.agent_name) : undefined,
        children: Array.isArray(obj.children) ? normalizeNodes(obj.children) :
                  Array.isArray(obj.subtasks) ? normalizeNodes(obj.subtasks) : undefined,
      }
    }
    return { id: `step-${idx}`, title: String(item), status: 'pending' }
  })
}

function countNodes(nodes: SubtaskNode[]): { total: number; completed: number } {
  let total = 0
  let completed = 0
  for (const node of nodes) {
    total++
    if (node.status === 'completed' || node.status === 'done') completed++
    if (node.children) {
      const child = countNodes(node.children)
      total += child.total
      completed += child.completed
    }
  }
  return { total, completed }
}

// ---------------------------------------------------------------------------
// TreeNode Component
// ---------------------------------------------------------------------------

function TreeNode({
  node,
  depth,
  isLast,
}: {
  node: SubtaskNode
  depth: number
  isLast: boolean
}) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = node.children && node.children.length > 0

  const toggleExpand = useCallback(() => {
    setExpanded((prev) => !prev)
  }, [])

  return (
    <div className="relative">
      {/* Connector lines */}
      {depth > 0 && (
        <>
          {/* Horizontal connector */}
          <span
            className={cn(
              'absolute top-4 w-4 border-t border-gray-300',
              'dark:border-gray-600',
            )}
            style={{ left: `${(depth - 1) * 24 + 12}px` }}
            aria-hidden="true"
          />
          {/* Vertical connector */}
          {!isLast && (
            <span
              className={cn(
                'absolute border-l border-gray-300',
                'dark:border-gray-600',
              )}
              style={{
                left: `${(depth - 1) * 24 + 12}px`,
                top: '16px',
                bottom: '0',
              }}
              aria-hidden="true"
            />
          )}
        </>
      )}

      {/* Node content */}
      <div
        className={cn(
          'flex items-center gap-2 py-1.5 group',
        )}
        style={{ paddingLeft: `${depth * 24}px` }}
      >
        {/* Expand/collapse toggle */}
        {hasChildren ? (
          <button
            onClick={toggleExpand}
            className={cn(
              'flex h-5 w-5 shrink-0 items-center justify-center rounded',
              'text-gray-500 hover:bg-gray-100 hover:text-gray-700',
              'dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-gray-200',
              'transition-colors',
            )}
            aria-label={expanded ? 'Collapse subtask' : 'Expand subtask'}
          >
            <svg
              className={cn(
                'h-3.5 w-3.5 transition-transform duration-150',
                expanded && 'rotate-90',
              )}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </button>
        ) : (
          <span className="w-5 shrink-0 text-center text-sm" aria-hidden="true">
            {getIcon(node.status)}
          </span>
        )}

        {/* Title */}
        <span
          className={cn(
            'text-sm font-medium truncate',
            node.status === 'completed' || node.status === 'done'
              ? 'text-gray-500 line-through dark:text-gray-500'
              : 'text-gray-800 dark:text-gray-200',
          )}
        >
          {node.title}
        </span>

        {/* Status badge */}
        <Badge variant={getVariant(node.status)} size="sm">
          {node.status}
        </Badge>

        {/* Agent name */}
        {node.agent && (
          <span
            className={cn(
              'ml-auto text-xs text-gray-500 dark:text-gray-400',
              'opacity-0 group-hover:opacity-100 transition-opacity',
            )}
          >
            🤖 {node.agent}
          </span>
        )}
      </div>

      {/* Children */}
      {hasChildren && expanded && (
        <div className="relative">
          {/* Vertical line through children */}
          <span
            className={cn(
              'absolute border-l border-gray-300 dark:border-gray-600',
            )}
            style={{
              left: `${depth * 24 + 12}px`,
              top: '0',
              bottom: '8px',
            }}
            aria-hidden="true"
          />
          {node.children!.map((child, idx) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              isLast={idx === node.children!.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SubtaskTree
// ---------------------------------------------------------------------------

export function SubtaskTree({ task, className }: SubtaskTreeProps) {
  // Prefer realtime subtasks from DB (enriched by backend) over stale result snapshot
  const subtasks = buildSubtaskNodes(task)

  if (subtasks.length === 0) {
    return (
      <EmptyState
        icon="🌿"
        title="No subtasks"
        description="This task has no subtask plan or decomposition."
        className={className}
      />
    )
  }

  const { total, completed } = countNodes(subtasks)
  const progressPercent = total > 0 ? Math.round((completed / total) * 100) : 0

  return (
    <div className={cn('space-y-4', className)}>
      {/* Progress header */}
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
          {completed}/{total} subtasks complete
        </span>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {progressPercent}%
        </span>
      </div>
      <ProgressBar
        value={progressPercent}
        variant={progressPercent === 100 ? 'success' : 'default'}
        size="sm"
        animated={progressPercent > 0 && progressPercent < 100}
      />

      {/* Tree */}
      <div className="mt-2">
        {subtasks.map((node, idx) => (
          <TreeNode
            key={node.id}
            node={node}
            depth={0}
            isLast={idx === subtasks.length - 1}
          />
        ))}
      </div>
    </div>
  )
}
