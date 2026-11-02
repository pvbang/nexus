// ============================================================
// Nexus - Utility functions
// ============================================================

import { format, formatDistanceToNowStrict } from 'date-fns'

/**
 * Format a date string or Date object to "Jan 15, 2026 3:45 PM" format.
 */
export function formatDate(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return format(d, 'MMM d, yyyy h:mm a')
}

/**
 * Format a date as relative time: "2m ago", "1h ago", "3d ago".
 */
export function formatRelativeTime(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return formatDistanceToNowStrict(d, { addSuffix: true })
}

/**
 * Format byte size to human-readable string: "1.2 KB", "3.5 MB".
 */
export function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const k = 1024
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  const size = bytes / Math.pow(k, i)

  return `${size % 1 === 0 ? size.toString() : size.toFixed(1)} ${units[i]}`
}

/**
 * Truncate a string to maxLen characters, appending "..." if truncated.
 */
export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen) + '...'
}

/**
 * Generate a unique identifier using crypto.randomUUID().
 */
export function generateId(): string {
  return crypto.randomUUID()
}

/**
 * Async delay for the specified number of milliseconds.
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Debounce a function call by the specified delay in milliseconds.
 * Returns a debounced version of the function that delays invocation
 * until after `delay` ms have elapsed since the last call.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function debounce<T extends (...args: any[]) => any>(fn: T, delay: number): T {
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  const debounced = (...args: Parameters<T>) => {
    if (timeoutId !== null) {
      clearTimeout(timeoutId)
    }
    timeoutId = setTimeout(() => {
      fn(...args)
      timeoutId = null
    }, delay)
  }

  return debounced as unknown as T
}

/**
 * Strip raw XML tool-call blocks from AI content for clean display.
 * Removes patterns like function_calls, invoke, result, document XML tags
 * and other common LLM tool-call XML artifacts that leak into proposals.
 */
export function sanitizeDebateContent(content: string): string {
  if (!content) return content

  let cleaned = content

  // Remove <function_calls>...</function_calls> blocks (multiline)
  cleaned = cleaned.replace(/<function_calls>[\s\S]*?<\/function_calls>/gi, '')

  // Remove <function>...</function> blocks (LLM leaks tool calls as XML)
  cleaned = cleaned.replace(/<function>[\s\S]*?<\/function>/gi, '')

  // Remove standalone XML-like tool tags (antml:invoke, antml:parameter, etc.)
  cleaned = cleaned.replace(/<\/?antml:[^>]*>/gi, '')

  // Remove <invoke ...>...</invoke> blocks
  cleaned = cleaned.replace(/<invoke[^>]*>[\s\S]*?<\/invoke>/gi, '')

  // Remove <parameter ...>...</parameter> blocks
  cleaned = cleaned.replace(/<parameter[^>]*>[\s\S]*?<\/parameter>/gi, '')

  // Remove <result>...</result> blocks
  cleaned = cleaned.replace(/<result>[\s\S]*?<\/result>/gi, '')

  // Remove <document>...</document> blocks
  cleaned = cleaned.replace(/<document>[\s\S]*?<\/document>/gi, '')

  // Remove leftover empty lines (more than 2 consecutive)
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n')

  return cleaned.trim()
}
