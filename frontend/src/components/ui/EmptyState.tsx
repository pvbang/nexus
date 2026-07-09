// ============================================================
// MekongAI Nexus - EmptyState UI Primitive
// Consistent empty state with icon, title, description, and CTA
// ============================================================

import { type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { Button } from './Button'

export interface EmptyStateProps {
  /** Icon element or emoji string */
  icon?: ReactNode | string
  /** Empty state title */
  title: string
  /** Descriptive text */
  description?: string
  /** Optional call-to-action button */
  action?: {
    label: string
    onClick: () => void
  }
  /** Additional classes */
  className?: string
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  const renderedIcon =
    typeof icon === 'string' ? (
      <span className="text-4xl" role="img" aria-hidden="true">
        {icon}
      </span>
    ) : (
      icon
    )

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center py-12 px-6 text-center animate-fade-in',
        className,
      )}
    >
      {renderedIcon && (
        <div className="mb-4 text-text-muted dark:text-gray-500">
          {renderedIcon}
        </div>
      )}

      <h3 className="text-base font-semibold text-text-primary dark:text-gray-100">
        {title}
      </h3>

      {description && (
        <p className="mt-1.5 max-w-sm text-sm text-text-muted dark:text-gray-400">
          {description}
        </p>
      )}

      {action && (
        <div className="mt-5">
          <Button variant="secondary" size="sm" onClick={action.onClick}>
            {action.label}
          </Button>
        </div>
      )}
    </div>
  )
}
