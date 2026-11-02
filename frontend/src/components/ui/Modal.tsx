// ============================================================
// Nexus - Modal UI Primitive
// Overlay modal with framer-motion animation, focus trap, and escape key
// ============================================================

import {
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { cn } from '../../lib/cn'

export interface ModalProps {
  /** Whether the modal is open */
  open: boolean
  /** Callback when the modal should close */
  onClose: () => void
  /** Optional modal title */
  title?: string
  /** Modal content */
  children: ReactNode
  /** Modal width size */
  size?: 'sm' | 'md' | 'lg' | 'full'
  /** Additional classes for the content container */
  className?: string
}

const sizeStyles: Record<NonNullable<ModalProps['size']>, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  full: 'max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)]',
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function Modal({
  open,
  onClose,
  title,
  children,
  size = 'md',
  className,
}: ModalProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  // Focus first focusable element on open
  useEffect(() => {
    if (!open) return

    const timer = setTimeout(() => {
      const el = contentRef.current
      if (!el) return
      const focusable = el.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
      focusable?.focus()
    }, 50)

    return () => clearTimeout(timer)
  }, [open])

  // Escape key handler
  useEffect(() => {
    if (!open) return

    const handleKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  // Lock body scroll when open
  useEffect(() => {
    if (!open) return

    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = original
    }
  }, [open])

  // Basic focus trap
  const handleContentKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key !== 'Tab') return

      const el = contentRef.current
      if (!el) return

      const focusableEls = el.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (focusableEls.length === 0) return

      const first = focusableEls[0]
      const last = focusableEls[focusableEls.length - 1]

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    },
    [],
  )

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 bg-black/50 dark:bg-black/70"
            onClick={onClose}
            aria-hidden="true"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />

          {/* Content */}
          <motion.div
            ref={contentRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={title ? 'modal-title' : undefined}
            aria-label={title ? undefined : 'Modal dialog'}
            onKeyDown={handleContentKeyDown}
            className={cn(
              'relative z-10 w-full overflow-y-auto',
              'rounded-xl bg-white shadow-elevated',
              'dark:bg-surface-raised dark:border dark:border-border-subtle',
              sizeStyles[size],
              className,
            )}
            initial={{ opacity: 0, scale: 0.95, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 8 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
          >
            {/* Header */}
            {title && (
              <div className="flex items-center justify-between border-b border-border px-6 py-4 dark:border-border-subtle">
                <h2 id="modal-title" className="text-base font-semibold text-text-primary dark:text-gray-100">
                  {title}
                </h2>
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close modal"
                  className={cn(
                    'inline-flex h-8 w-8 items-center justify-center rounded-comfortable',
                    'text-text-muted hover:bg-black/5 hover:text-text-primary',
                    'dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-gray-200',
                    'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                  )}
                >
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {/* Body */}
            <div className={cn('px-6 py-5', !title && 'pt-6')}>
              {children}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
