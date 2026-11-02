// ============================================================
// Nexus - StatusDot UI Primitive
// Animated status indicator dot with color mapping
// ============================================================

import { cn } from '../../lib/cn'

export interface StatusDotProps {
  /** Current status */
  status: 'online' | 'offline' | 'busy' | 'idle' | 'error'
  /** Dot size */
  size?: 'sm' | 'md' | 'lg'
  /** Enable pulse animation */
  pulse?: boolean
  /** Additional classes */
  className?: string
}

const statusColors: Record<StatusDotProps['status'], string> = {
  online: 'bg-emerald-500',
  offline: 'bg-gray-400 dark:bg-gray-600',
  busy: 'bg-blue-500',
  idle: 'bg-gray-400 dark:bg-gray-500',
  error: 'bg-red-500',
}

const pulseColors: Record<StatusDotProps['status'], string> = {
  online: 'bg-emerald-400',
  offline: 'bg-gray-300 dark:bg-gray-500',
  busy: 'bg-blue-400',
  idle: 'bg-gray-300 dark:bg-gray-400',
  error: 'bg-red-400',
}

const sizeStyles: Record<NonNullable<StatusDotProps['size']>, string> = {
  sm: 'h-2 w-2',
  md: 'h-2.5 w-2.5',
  lg: 'h-3.5 w-3.5',
}

const statusLabels: Record<StatusDotProps['status'], string> = {
  online: 'Online',
  offline: 'Offline',
  busy: 'Busy',
  idle: 'Idle',
  error: 'Error',
}

export function StatusDot({
  status,
  size = 'md',
  pulse = true,
  className,
}: StatusDotProps) {
  return (
    <span
      className={cn('relative inline-flex shrink-0', sizeStyles[size], className)}
      role="status"
      aria-label={statusLabels[status]}
    >
      {pulse && status !== 'offline' && status !== 'idle' && (
        <span
          className={cn(
            'absolute inset-0 inline-flex h-full w-full rounded-full opacity-75 animate-pulse-dot',
            pulseColors[status],
          )}
        />
      )}
      <span
        className={cn(
          'relative inline-flex h-full w-full rounded-full',
          statusColors[status],
        )}
      />
    </span>
  )
}
