// ============================================================
// MekongAI Nexus - Button UI Primitive
// Reusable button with variants, sizes, loading, and icon support
// ============================================================

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { Spinner } from './Spinner'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual style variant */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'pill'
  /** Button size */
  size?: 'sm' | 'md' | 'lg'
  /** Show loading spinner and disable interaction */
  loading?: boolean
  /** Optional icon before text */
  icon?: ReactNode
  /** Square button for icon only (no text) */
  iconOnly?: boolean
}

const variantStyles: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: [
    'bg-surface-dark text-white',
    'hover:bg-[#2a3342]',
    'active:bg-[#1a2736]',
    'dark:bg-white dark:text-black',
    'dark:hover:bg-gray-200',
    'dark:active:bg-gray-300',
  ].join(' '),
  secondary: [
    'bg-surface-tertiary text-text-primary',
    'hover:bg-[#e5e5e5]',
    'active:bg-[#d9d9d9]',
    'dark:bg-surface-tertiary dark:text-white',
    'dark:hover:bg-[#353560]',
    'dark:active:bg-[#404070]',
  ].join(' '),
  ghost: [
    'bg-transparent text-text-secondary',
    'hover:bg-black/5',
    'active:bg-black/10',
    'dark:text-gray-300',
    'dark:hover:bg-white/10',
    'dark:active:bg-white/15',
  ].join(' '),
  danger: [
    'bg-red-500 text-white',
    'hover:bg-red-600',
    'active:bg-red-700',
    'dark:bg-red-600',
    'dark:hover:bg-red-700',
  ].join(' '),
  pill: [
    'rounded-full bg-black/5 text-text-dark',
    'hover:bg-black/10',
    'active:bg-black/15',
    'dark:bg-white/10 dark:text-white',
    'dark:hover:bg-white/15',
  ].join(' '),
}

const sizeStyles: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'h-8 px-3 text-sm gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
  lg: 'h-12 px-6 text-base gap-2.5',
}

const iconOnlySizeStyles: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'h-8 w-8',
  md: 'h-10 w-10',
  lg: 'h-12 w-12',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      icon,
      iconOnly = false,
      disabled,
      className,
      children,
      ...props
    },
    ref,
  ) => {
    const isDisabled = disabled || loading

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        aria-disabled={isDisabled}
        aria-busy={loading}
        className={cn(
          // Base styles
          'inline-flex items-center justify-center',
          'font-medium transition-all duration-150 ease-out',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2',
          'dark:focus-visible:ring-offset-surface-dark',
          // Rounded (pill variant overrides this)
          variant !== 'pill' && 'rounded-comfortable',
          // Variant
          variantStyles[variant],
          // Size
          iconOnly ? iconOnlySizeStyles[size] : sizeStyles[size],
          // Disabled
          isDisabled && 'opacity-50 cursor-not-allowed pointer-events-none',
          // Custom
          className,
        )}
        {...props}
      >
        {loading ? (
          <Spinner size={size === 'lg' ? 'sm' : 'xs'} className="shrink-0" />
        ) : icon ? (
          <span className="shrink-0 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
        ) : null}
        {!iconOnly && children}
      </button>
    )
  },
)

Button.displayName = 'Button'
