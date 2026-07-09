// ============================================================
// MekongAI Nexus - Accordion UI Primitive
// Expandable content sections with framer-motion height animation
// ============================================================

import { useState, useCallback, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { cn } from '../../lib/cn'

export interface AccordionItem {
  /** Unique identifier */
  id: string
  /** Title - string or custom ReactNode */
  title: string | ReactNode
  /** Expandable content */
  content: ReactNode
  /** Start expanded */
  defaultOpen?: boolean
}

export interface AccordionProps {
  /** Accordion items */
  items: AccordionItem[]
  /** Allow multiple items open simultaneously */
  allowMultiple?: boolean
  /** Additional class names */
  className?: string
}

/** Chevron SVG that rotates */
function ChevronIcon({ open }: { open: boolean }) {
  return (
    <motion.svg
      className="h-4 w-4 shrink-0 text-text-muted dark:text-gray-500"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
      animate={{ rotate: open ? 180 : 0 }}
      transition={{ duration: 0.2, ease: 'easeInOut' }}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </motion.svg>
  )
}

export function Accordion({
  items,
  allowMultiple = false,
  className,
}: AccordionProps) {
  // Initialize open state from defaultOpen
  const [openIds, setOpenIds] = useState<Set<string>>(() => {
    const defaults = new Set<string>()
    items.forEach((item) => {
      if (item.defaultOpen) defaults.add(item.id)
    })
    return defaults
  })

  const toggle = useCallback(
    (id: string) => {
      setOpenIds((prev) => {
        const next = new Set(prev)
        if (next.has(id)) {
          next.delete(id)
        } else {
          if (!allowMultiple) {
            next.clear()
          }
          next.add(id)
        }
        return next
      })
    },
    [allowMultiple],
  )

  return (
    <div
      className={cn(
        'divide-y divide-border rounded-comfortable border border-border',
        'dark:divide-border-subtle dark:border-border-subtle',
        className,
      )}
      role="region"
    >
      {items.map((item) => {
        const isOpen = openIds.has(item.id)

        return (
          <div key={item.id} className="overflow-hidden">
            {/* Trigger */}
            <button
              type="button"
              onClick={() => toggle(item.id)}
              aria-expanded={isOpen}
              aria-controls={`accordion-content-${item.id}`}
              id={`accordion-trigger-${item.id}`}
              className={cn(
                'flex w-full items-center justify-between gap-3 px-4 py-3 text-left',
                'transition-colors duration-150',
                'hover:bg-black/[0.02] dark:hover:bg-white/[0.02]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500',
              )}
            >
              <span
                className={cn(
                  'text-sm font-medium',
                  'text-text-heading dark:text-gray-100',
                )}
              >
                {item.title}
              </span>
              <ChevronIcon open={isOpen} />
            </button>

            {/* Content with height animation */}
            <AnimatePresence initial={false}>
              {isOpen && (
                <motion.div
                  id={`accordion-content-${item.id}`}
                  role="region"
                  aria-labelledby={`accordion-trigger-${item.id}`}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.25, ease: 'easeInOut' }}
                >
                  <div className="px-4 pb-4 pt-0 text-sm text-text-secondary leading-relaxed dark:text-gray-400">
                    {item.content}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )
      })}
    </div>
  )
}
