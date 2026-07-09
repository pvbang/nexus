// ============================================================
// MekongAI Nexus - Skeleton UI Primitive
// Animated loading placeholder with shape variants
// ============================================================

import { cn } from '../../lib/cn'

export interface SkeletonProps {
  /** Shape variant */
  variant?: 'text' | 'circular' | 'rectangular'
  /** Additional classes for width/height control */
  className?: string
}

const variantStyles: Record<NonNullable<SkeletonProps['variant']>, string> = {
  text: 'h-4 w-full rounded',
  circular: 'rounded-full aspect-square',
  rectangular: 'rounded-lg',
}

export function Skeleton({ variant = 'text', className }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'animate-pulse bg-gray-200',
        'dark:bg-surface-tertiary',
        variantStyles[variant],
        className,
      )}
    />
  )
}
