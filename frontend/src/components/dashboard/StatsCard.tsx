// ============================================================
// Nexus - StatsCard Dashboard Component
// Animated stat card with gradient background, count-up animation,
// trend indicator, and dark mode support
// ============================================================

import { useEffect, useRef, useState } from 'react'
import { cn } from '../../lib/cn'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StatsCardProps {
  /** Emoji icon displayed as overlay */
  icon: string
  /** Stat label */
  label: string
  /** Stat value (number for count-up animation, string for static display) */
  value: number | string
  /** Optional trend indicator */
  trend?: {
    value: number
    direction: 'up' | 'down'
  }
  /** Tailwind gradient classes (e.g. "from-blue-500 to-blue-600") */
  gradient: string
  /** Additional CSS classes */
  className?: string
}

// ---------------------------------------------------------------------------
// Count-up animation hook
// ---------------------------------------------------------------------------

function useCountUp(target: number, duration = 800): number {
  const [current, setCurrent] = useState(0)
  const rafRef = useRef<number | null>(null)
  const startTimeRef = useRef<number | null>(null)
  const prevTargetRef = useRef(0)

  useEffect(() => {
    const startValue = prevTargetRef.current
    prevTargetRef.current = target
    startTimeRef.current = null

    const animate = (timestamp: number) => {
      if (startTimeRef.current === null) {
        startTimeRef.current = timestamp
      }

      const elapsed = timestamp - startTimeRef.current
      const progress = Math.min(elapsed / duration, 1)

      // Ease-out cubic for smooth deceleration
      const eased = 1 - Math.pow(1 - progress, 3)
      const value = Math.round(startValue + (target - startValue) * eased)

      setCurrent(value)

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    rafRef.current = requestAnimationFrame(animate)

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
      }
    }
  }, [target, duration])

  return current
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StatsCard({
  icon,
  label,
  value,
  trend,
  gradient,
  className,
}: StatsCardProps) {
  const isNumeric = typeof value === 'number'
  const animatedValue = useCountUp(isNumeric ? value : 0)

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-xl p-5',
        'bg-gradient-to-br text-white shadow-lg',
        'dark:shadow-none dark:brightness-90',
        'transition-transform duration-200 hover:scale-[1.02]',
        gradient,
        className,
      )}
    >
      {/* Icon overlay */}
      <div
        className="absolute top-3 right-3 text-4xl opacity-20 select-none pointer-events-none"
        aria-hidden="true"
      >
        {icon}
      </div>

      {/* Value */}
      <div className="text-3xl font-bold font-display tabular-nums">
        {isNumeric ? animatedValue.toLocaleString() : value}
      </div>

      {/* Label */}
      <div className="text-sm font-medium opacity-90 mt-1">{label}</div>

      {/* Trend indicator */}
      {trend && (
        <div
          className={cn(
            'mt-2 inline-flex items-center gap-1 text-xs font-semibold rounded-full px-2 py-0.5',
            trend.direction === 'up'
              ? 'bg-white/20 text-white'
              : 'bg-white/20 text-white',
          )}
        >
          <span aria-hidden="true">
            {trend.direction === 'up' ? '↑' : '↓'}
          </span>
          <span>{trend.value}%</span>
        </div>
      )}
    </div>
  )
}
