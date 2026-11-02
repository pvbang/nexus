// ============================================================
// Nexus - Input UI Primitive
// Reusable input with variants, label, error, icon, and clearable support
// ============================================================

import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Visual variant */
  variant?: 'default' | 'search'
  /** Label text above input */
  label?: string
  /** Error message (shows red border + error text) */
  error?: string
  /** Helper text below input */
  helperText?: string
  /** Left icon element (used automatically for search variant) */
  icon?: ReactNode
  /** Show clear (X) button when input has value */
  clearable?: boolean
  /** Callback when clear button is clicked */
  onClear?: () => void
}

/** Search icon SVG for search variant */
function SearchIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
      />
    </svg>
  )
}

/** Clear (X) button SVG */
function ClearIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      variant = 'default',
      label,
      error,
      helperText,
      icon,
      clearable = false,
      onClear,
      disabled,
      value,
      className,
      id,
      ...props
    },
    ref,
  ) => {
    const inputId = id || (label ? `input-${label.toLowerCase().replace(/\s+/g, '-')}` : undefined)
    const hasLeftIcon = variant === 'search' || !!icon
    const showClear = clearable && !!value && !disabled
    const isSearch = variant === 'search'

    return (
      <div className="w-full">
        {/* Label */}
        {label && (
          <label
            htmlFor={inputId}
            className={cn(
              'mb-1.5 block text-sm font-medium',
              'text-text-heading dark:text-gray-200',
              disabled && 'opacity-50',
            )}
          >
            {label}
          </label>
        )}

        {/* Input wrapper */}
        <div className="relative">
          {/* Left icon */}
          {hasLeftIcon && (
            <span
              className={cn(
                'pointer-events-none absolute left-3 top-1/2 -translate-y-1/2',
                'text-text-muted dark:text-gray-500',
                '[&>svg]:h-4 [&>svg]:w-4',
              )}
              aria-hidden="true"
            >
              {isSearch ? <SearchIcon className="h-4 w-4" /> : icon}
            </span>
          )}

          <input
            ref={ref}
            id={inputId}
            disabled={disabled}
            value={value}
            aria-invalid={!!error}
            aria-describedby={
              error
                ? `${inputId}-error`
                : helperText
                  ? `${inputId}-helper`
                  : undefined
            }
            className={cn(
              // Base
              'w-full rounded-comfortable border bg-white text-sm transition-all duration-150',
              'placeholder:text-text-muted',
              'dark:bg-surface-raised dark:text-gray-100 dark:placeholder:text-gray-500',
              // Height
              'h-10 px-3',
              // Left padding for icon
              hasLeftIcon && 'pl-10',
              // Right padding for clear button
              showClear && 'pr-9',
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
              // Focus visible
              'focus:outline-none',
              className,
            )}
            {...props}
          />

          {/* Clear button */}
          {showClear && (
            <button
              type="button"
              onClick={onClear}
              tabIndex={-1}
              aria-label="Clear input"
              className={cn(
                'absolute right-2.5 top-1/2 -translate-y-1/2',
                'inline-flex h-5 w-5 items-center justify-center rounded-full',
                'text-text-muted hover:text-text-secondary hover:bg-black/5',
                'dark:text-gray-500 dark:hover:text-gray-300 dark:hover:bg-white/10',
                'transition-colors',
              )}
            >
              <ClearIcon className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Error message */}
        {error && (
          <p
            id={`${inputId}-error`}
            role="alert"
            className="mt-1.5 text-xs text-red-500 dark:text-red-400"
          >
            {error}
          </p>
        )}

        {/* Helper text */}
        {!error && helperText && (
          <p
            id={`${inputId}-helper`}
            className="mt-1.5 text-xs text-text-muted dark:text-gray-500"
          >
            {helperText}
          </p>
        )}
      </div>
    )
  },
)

Input.displayName = 'Input'
