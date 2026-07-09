// ============================================================
// MekongAI Nexus - Card UI Primitive
// Container card with variants, padding options, and click support
// ============================================================

import { type ReactNode, type HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Visual variant */
  variant?: 'default' | 'elevated' | 'brand' | 'interactive'
  /** Inner padding */
  padding?: 'none' | 'sm' | 'md' | 'lg'
  /** Card content */
  children: ReactNode
}

const variantStyles: Record<NonNullable<CardProps['variant']>, string> = {
  default: [
    'bg-surface-base border border-border',
    'dark:bg-surface-raised dark:border-border-subtle',
  ].join(' '),
  elevated: [
    'bg-surface-base border border-border shadow-card',
    'dark:bg-surface-raised dark:border-border-subtle dark:shadow-none',
  ].join(' '),
  brand: [
    'bg-surface-base border border-brand-200 shadow-brand',
    'dark:bg-surface-raised dark:border-brand-800',
  ].join(' '),
  interactive: [
    'bg-surface-base border border-border shadow-subtle',
    'hover:shadow-card hover:border-border-dark',
    'dark:bg-surface-raised dark:border-border-subtle',
    'dark:hover:border-border-hover dark:hover:shadow-none',
  ].join(' '),
}

const paddingStyles: Record<NonNullable<CardProps['padding']>, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-5',
  lg: 'p-7',
}

export function Card({
  variant = 'default',
  padding = 'md',
  className,
  children,
  onClick,
  ...props
}: CardProps) {
  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick(e as unknown as React.MouseEvent<HTMLDivElement>)
              }
            }
          : undefined
      }
      className={cn(
        'rounded-lg transition-all duration-150',
        variantStyles[variant],
        paddingStyles[padding],
        onClick && 'cursor-pointer',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}
