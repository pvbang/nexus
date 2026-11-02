// ============================================================
// Nexus - Badge UI Primitive
// Status badge with variants, sizes, and animated dot
// ============================================================

import { type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface BadgeProps {
  /** Visual variant */
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info' | 'purple' | 'brand'
  /** Badge size */
  size?: 'sm' | 'md'
  /** Show animated status dot before text */
  dot?: boolean
  /** Badge content */
  children: ReactNode
  /** Additional classes */
  className?: string
}

const variantStyles: Record<NonNullable<BadgeProps['variant']>, string> = {
  default: [
    'bg-gray-100 text-gray-700',
    'dark:bg-surface-tertiary dark:text-gray-300',
  ].join(' '),
  success: [
    'bg-emerald-50 text-emerald-700',
    'dark:bg-emerald-900/30 dark:text-emerald-400',
  ].join(' '),
  warning: [
    'bg-amber-50 text-amber-700',
    'dark:bg-amber-900/30 dark:text-amber-400',
  ].join(' '),
  error: [
    'bg-red-50 text-red-700',
    'dark:bg-red-900/30 dark:text-red-400',
  ].join(' '),
  info: [
    'bg-blue-50 text-blue-700',
    'dark:bg-blue-900/30 dark:text-blue-400',
  ].join(' '),
  purple: [
    'bg-purple-50 text-purple-700',
    'dark:bg-purple-900/30 dark:text-purple-400',
  ].join(' '),
  brand: [
    'bg-brand-50 text-brand-700',
    'dark:bg-brand-900/30 dark:text-brand-400',
  ].join(' '),
}

const dotColorMap: Record<NonNullable<BadgeProps['variant']>, string> = {
  default: 'bg-gray-500',
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  error: 'bg-red-500',
  info: 'bg-blue-500',
  purple: 'bg-purple-500',
  brand: 'bg-brand-500',
}

const sizeStyles: Record<NonNullable<BadgeProps['size']>, string> = {
  sm: 'px-2 py-0.5 text-[11px]',
  md: 'px-2.5 py-1 text-xs',
}

export function Badge({
  variant = 'default',
  size = 'sm',
  dot = false,
  children,
  className,
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full-pill font-medium leading-none',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
    >
      {dot && (
        <span className="relative flex h-2 w-2 shrink-0">
          <span
            className={cn(
              'absolute inline-flex h-full w-full animate-pulse-dot rounded-full opacity-75',
              dotColorMap[variant],
            )}
          />
          <span
            className={cn(
              'relative inline-flex h-2 w-2 rounded-full',
              dotColorMap[variant],
            )}
          />
        </span>
      )}
      {children}
    </span>
  )
}
