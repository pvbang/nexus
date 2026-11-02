// ============================================================
// Nexus - TodoRenderer
// Specialized renderer for add_todo, update_todo, complete_todo
// Shows checklist UI with status indicators
// ============================================================

import { motion } from 'framer-motion'
import { ListTodo, CheckCircle2, Circle, XCircle, Plus, Edit3 } from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
import type { ToolExecution } from '../../../../types/process'

interface TodoRendererProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Determine todo action type from tool name */
function getTodoAction(toolName: string): { icon: typeof Plus; label: string; color: string } {
  const name = (toolName || '').toLowerCase()
  if (name.includes('complete') || name.includes('done') || name.includes('check')) {
    return { icon: CheckCircle2, label: 'Complete', color: 'text-emerald-500' }
  }
  if (name.includes('update') || name.includes('edit')) {
    return { icon: Edit3, label: 'Update', color: 'text-amber-500' }
  }
  if (name.includes('delete') || name.includes('remove')) {
    return { icon: XCircle, label: 'Delete', color: 'text-red-500' }
  }
  return { icon: Plus, label: 'Add', color: 'text-blue-500' }
}

export function TodoRenderer({ execution }: TodoRendererProps) {
  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'
  const action = getTodoAction(execution.toolName)
  const ActionIcon = action.icon

  // Extract todo data from args
  const title = (execution.toolArgs?.title as string)
    || (execution.toolArgs?.task as string)
    || (execution.toolArgs?.description as string)
    || ''
  const todoId = (execution.toolArgs?.id as string) || (execution.toolArgs?.todo_id as string) || ''
  const priority = (execution.toolArgs?.priority as string) || ''
  const status = (execution.toolArgs?.status as string) || ''

  // Parse result for todo list display
  let resultItems: Array<{ title: string; completed: boolean }> = []
  if (execution.result) {
    try {
      const parsed = typeof execution.result === 'string' ? JSON.parse(execution.result) : execution.result
      if (Array.isArray(parsed)) {
        resultItems = parsed.map((item: any) => ({
          title: item.title || item.task || item.description || String(item),
          completed: item.completed || item.done || item.status === 'done',
        }))
      } else if (parsed.todos && Array.isArray(parsed.todos)) {
        resultItems = parsed.todos.map((item: any) => ({
          title: item.title || item.task || String(item),
          completed: item.completed || item.done || false,
        }))
      }
    } catch {
      // Not parseable as todo list
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e]',
        isFailed
          ? 'border-red-200 dark:border-red-800'
          : 'border-blue-200 dark:border-blue-800/50',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <ListTodo className="h-4 w-4 text-blue-500 dark:text-blue-400" />
        <span className="font-medium text-text-primary dark:text-gray-100 text-xs">
          {execution.toolName}
        </span>
        <Badge
          variant={isFailed ? 'error' : 'info'}
          size="sm"
        >
          <ActionIcon className={cn('h-2.5 w-2.5 mr-0.5', action.color)} />
          {action.label}
        </Badge>
        <span className="text-xs text-text-muted dark:text-gray-500">
          by{' '}
          <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
            {execution.agentName}
          </span>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {execution.duration != null && (
            <Badge variant="default" size="sm">{formatDuration(execution.duration)}</Badge>
          )}
          {isRunning && <Spinner size="xs" className="text-blue-500" />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* Todo item being acted on */}
      {title && (
        <div className="mt-2 flex items-start gap-2 pl-1">
          <ActionIcon className={cn('h-4 w-4 mt-0.5 shrink-0', action.color)} />
          <div className="flex-1 min-w-0">
            <span className="text-[13px] text-text-primary dark:text-gray-200">{title}</span>
            <div className="flex items-center gap-2 mt-0.5">
              {todoId && <span className="text-[10px] text-gray-400 font-mono">ID: {todoId}</span>}
              {priority && (
                <Badge variant="default" size="sm">
                  Priority: {priority}
                </Badge>
              )}
              {status && (
                <Badge variant={status === 'done' ? 'success' : 'default'} size="sm">
                  {status}
                </Badge>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Result: todo list display */}
      {resultItems.length > 0 && (
        <div className="mt-2 space-y-1 border-t border-gray-100 dark:border-[#2a2a45] pt-2">
          <span className="text-[9px] text-gray-400 uppercase font-bold">Todo List</span>
          {resultItems.slice(0, 10).map((item, idx) => (
            <div key={idx} className="flex items-center gap-2 text-xs">
              {item.completed ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
              ) : (
                <Circle className="h-3.5 w-3.5 text-gray-300 dark:text-gray-600 shrink-0" />
              )}
              <span className={cn(
                'flex-1',
                item.completed && 'line-through text-gray-400 dark:text-gray-500',
                !item.completed && 'text-text-primary dark:text-gray-200',
              )}>
                {item.title}
              </span>
            </div>
          ))}
          {resultItems.length > 10 && (
            <span className="text-[10px] text-gray-400">+{resultItems.length - 10} more items</span>
          )}
        </div>
      )}

      {/* Error display */}
      {isFailed && execution.error && (
        <div className="mt-2 text-[11px] text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded px-2 py-1">
          {execution.error}
        </div>
      )}
    </motion.div>
  )
}
