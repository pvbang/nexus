// ============================================================
// Nexus - Tooltip UI Primitive
// Pure CSS/state hover tooltip with positioning
// ============================================================

import { useState, useRef, useCallback, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface TooltipProps {
  /** Tooltip text content */
  content: string
  /** Trigger element */
  children: ReactNode
  /** Position relative to trigger */
  position?: 'top' | 'bottom' | 'left' | 'right'
  /** Delay in ms before showing tooltip */
  delay?: number
  /** Additional classes for the wrapper */
  className?: string
}

const positionStyles: Record<NonNullable<TooltipProps['position']>, string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  left: 'right-full top-1/2 -translate-y-1/2 mr-2',
  right: 'left-full top-1/2 -translate-y-1/2 ml-2',
}

const arrowStyles: Record<NonNullable<TooltipProps['position']>, string> = {
  top: 'top-full left-1/2 -translate-x-1/2 border-t-gray-900 border-x-transparent border-b-transparent dark:border-t-gray-200',
  bottom: 'bottom-full left-1/2 -translate-x-1/2 border-b-gray-900 border-x-transparent border-t-transparent dark:border-b-gray-200',
  left: 'left-full top-1/2 -translate-y-1/2 border-l-gray-900 border-y-transparent border-r-transparent dark:border-l-gray-200',
  right: 'right-full top-1/2 -translate-y-1/2 border-r-gray-900 border-y-transparent border-l-transparent dark:border-r-gray-200',
}

export function Tooltip({
  content,
  children,
  position = 'top',
  delay = 500,
  className,
}: TooltipProps) {
  const [visible, setVisible] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const show = useCallback(() => {
    timerRef.current = setTimeout(() => setVisible(true), delay)
  }, [delay])

  const hide = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setVisible(false)
  }, [])

  return (
    <span
      className={cn('relative inline-flex', className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}

      {visible && content && (
        <span
          role="tooltip"
          className={cn(
            'absolute z-50 whitespace-nowrap',
            'rounded-md px-2.5 py-1.5 text-xs font-medium',
            'bg-gray-900 text-white',
            'dark:bg-gray-200 dark:text-gray-900',
            'shadow-elevated pointer-events-none',
            'animate-fade-in',
            positionStyles[position],
          )}
        >
          {content}
          {/* Arrow */}
          <span
            className={cn(
              'absolute h-0 w-0 border-4',
              arrowStyles[position],
            )}
          />
        </span>
      )}
    </span>
  )
}
