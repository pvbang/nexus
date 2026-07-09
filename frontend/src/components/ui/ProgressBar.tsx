// ============================================================
// MekongAI Nexus - ProgressBar UI Primitive
// Horizontal progress bar with variants, animation, and label support
// ============================================================

import { cn } from '../../lib/cn'

export interface ProgressBarProps {
  /** Progress value 0-100 */
  value: number
  /** Color variant */
  variant?: 'default' | 'success' | 'warning' | 'error'
  /** Bar height */
  size?: 'sm' | 'md'
  /** Optional label displayed above the bar */
  label?: string
  /** Show percentage value on the right */
  showValue?: boolean
  /** Animate the bar fill with a shimmer effect */
  animated?: boolean
  /** Additional class names */
  className?: string
}

const variantColors: Record<NonNullable<ProgressBarProps['variant']>, string> = {
  default: 'bg-brand-500 dark:bg-brand-400',
  success: 'bg-emerald-500 dark:bg-emerald-400',
  warning: 'bg-amber-500 dark:bg-amber-400',
  error: 'bg-red-500 dark:bg-red-400',
}

const sizeStyles: Record<NonNullable<ProgressBarProps['size']>, string> = {
  sm: 'h-1.5',
  md: 'h-2.5',
}

export function ProgressBar({
  value,
  variant = 'default',
  size = 'md',
  label,
  showValue = false,
  animated = false,
  className,
}: ProgressBarProps) {
  // Clamp value between 0 and 100
  const clampedValue = Math.min(100, Math.max(0, value))

  return (
    <div className={cn('w-full', className)}>
      {/* Header: label + percentage */}
      {(label || showValue) && (
        <div className="mb-1.5 flex items-center justify-between">
          {label && (
            <span className="text-sm font-medium text-text-heading dark:text-gray-200">
              {label}
            </span>
          )}
          {showValue && (
            <span className="text-xs font-medium text-text-muted dark:text-gray-400">
              {Math.round(clampedValue)}%
            </span>
          )}
        </div>
      )}

      {/* Track */}
      <div
        className={cn(
          'w-full overflow-hidden rounded-full',
          'bg-border dark:bg-surface-tertiary',
          sizeStyles[size],
        )}
        role="progressbar"
        aria-valuenow={clampedValue}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label || 'Progress'}
      >
        {/* Fill */}
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500 ease-out',
            variantColors[variant],
            // Shimmer animation overlay
            animated && 'relative overflow-hidden after:absolute after:inset-0 after:animate-shimmer after:bg-gradient-to-r after:from-transparent after:via-white/25 after:to-transparent',
          )}
          style={{ width: `${clampedValue}%` }}
        />
      </div>
    </div>
  )
}
