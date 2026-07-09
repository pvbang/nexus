// ============================================================
// MekongAI Nexus - Select UI Primitive
// Native dropdown select styled consistently with design system
// ============================================================

import { cn } from '../../lib/cn'

export interface SelectOption {
  /** Option value */
  value: string
  /** Display label */
  label: string
  /** Optional icon emoji or string */
  icon?: string
}

export interface SelectProps {
  /** Available options */
  options: SelectOption[]
  /** Currently selected value */
  value: string
  /** Change handler */
  onChange: (value: string) => void
  /** Placeholder text when no value selected */
  placeholder?: string
  /** Label text above select */
  label?: string
  /** Error message (shows red border + error text) */
  error?: string
  /** Select size */
  size?: 'sm' | 'md'
  /** Disabled state */
  disabled?: boolean
  /** Additional class names */
  className?: string
  /** HTML id */
  id?: string
}

/** Chevron down SVG */
function ChevronDownIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
  )
}

const sizeStyles: Record<NonNullable<SelectProps['size']>, string> = {
  sm: 'h-8 text-xs px-2.5 pr-8',
  md: 'h-10 text-sm px-3 pr-9',
}

export function Select({
  options,
  value,
  onChange,
  placeholder,
  label,
  error,
  size = 'md',
  disabled = false,
  className,
  id,
}: SelectProps) {
  const selectId = id || (label ? `select-${label.toLowerCase().replace(/\s+/g, '-')}` : undefined)

  return (
    <div className="w-full">
      {/* Label */}
      {label && (
        <label
          htmlFor={selectId}
          className={cn(
            'mb-1.5 block text-sm font-medium',
            'text-text-heading dark:text-gray-200',
            disabled && 'opacity-50',
          )}
        >
          {label}
        </label>
      )}

      {/* Select wrapper */}
      <div className="relative">
        <select
          id={selectId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          aria-invalid={!!error}
          aria-describedby={error ? `${selectId}-error` : undefined}
          className={cn(
            // Base
            'w-full appearance-none rounded-comfortable border bg-white text-left transition-all duration-150',
            'dark:bg-surface-raised dark:text-gray-100',
            // Size
            sizeStyles[size],
            // Default border
            !error && [
              'border-border dark:border-border-subtle',
              'hover:border-border-hover dark:hover:border-[#353560]',
            ],
            // Focus
            !error && [
              'focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20',
              'dark:focus:border-brand-400 dark:focus:ring-brand-400/20',
            ],
            // Error
            error && [
              'border-red-400 dark:border-red-500',
              'focus:border-red-500 focus:ring-2 focus:ring-red-500/20',
              'dark:focus:border-red-400 dark:focus:ring-red-400/20',
            ],
            // Disabled
            disabled && 'cursor-not-allowed opacity-50',
            // Placeholder color when no value
            !value && 'text-text-muted dark:text-gray-500',
            // Focus visible
            'focus:outline-none',
            className,
          )}
        >
          {placeholder && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.icon ? `${opt.icon} ${opt.label}` : opt.label}
            </option>
          ))}
        </select>

        {/* Chevron icon */}
        <span
          className={cn(
            'pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2',
            'text-text-muted dark:text-gray-500',
          )}
          aria-hidden="true"
        >
          <ChevronDownIcon className={cn(size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4')} />
        </span>
      </div>

      {/* Error message */}
      {error && (
        <p
          id={`${selectId}-error`}
          role="alert"
          className="mt-1.5 text-xs text-red-500 dark:text-red-400"
        >
          {error}
        </p>
      )}
    </div>
  )
}
