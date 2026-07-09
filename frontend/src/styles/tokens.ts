// ============================================================
// Design Tokens — Programmatic access to CSS variables
// Use these when you need colors in JS (e.g., canvas, charts)
// For JSX markup, prefer Tailwind classes instead.
// ============================================================

export const tokens = {
  colors: {
    brand: {
      primary: 'var(--color-accent)',
      secondary: 'var(--color-accent-secondary)',
      hover: 'var(--color-accent-hover)',
      light: 'var(--color-accent-light)',
      ring: 'var(--color-accent-ring)',
    },
    surface: {
      base: 'var(--color-bg-primary)',
      raised: 'var(--color-bg-raised)',
      secondary: 'var(--color-bg-secondary)',
      tertiary: 'var(--color-bg-tertiary)',
      inset: 'var(--color-bg-inset)',
      overlay: 'var(--color-bg-overlay)',
    },
    text: {
      heading: 'var(--color-text-heading)',
      primary: 'var(--color-text-primary)',
      secondary: 'var(--color-text-secondary)',
      muted: 'var(--color-text-muted)',
    },
    border: {
      subtle: 'var(--color-border-light)',
      default: 'var(--color-border-default)',
      hover: 'var(--color-border-hover)',
    },
    status: {
      success: 'var(--color-status-success)',
      warning: 'var(--color-status-warning)',
      error: 'var(--color-status-error)',
      info: 'var(--color-status-info)',
    },
  },
  spacing: {
    xs: '0.25rem',
    sm: '0.5rem',
    md: '1rem',
    lg: '1.5rem',
    xl: '2rem',
    '2xl': '3rem',
  },
  radius: {
    sm: '4px',
    md: '8px',
    comfortable: '11px',
    lg: '13px',
    generous: '16px',
    xl: '20px',
    hero: '24px',
    pill: '9999px',
  },
} as const

export type TokenColors = typeof tokens.colors
export type TokenSpacing = typeof tokens.spacing
export type TokenRadius = typeof tokens.radius
