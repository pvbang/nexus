// ============================================================
// Nexus - Theme Provider (Feature #7)
// Applies CSS variables to document.documentElement
// ============================================================

import type { ThemePreset, ThemeColors } from './presets'
import { getPresetConfig } from './presets'

/**
 * Convert a hex color to an rgba string with given alpha.
 * Handles 3, 6, and 8-digit hex.
 */
function hexToRgba(hex: string, alpha: number): string {
  const clean = hex.replace('#', '')
  const r = parseInt(clean.substring(0, 2), 16)
  const g = parseInt(clean.substring(2, 4), 16)
  const b = parseInt(clean.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/**
 * Lighten a hex color by mixing with white.
 * factor: 0 = original, 1 = white
 */
function lightenHex(hex: string, factor: number): string {
  const clean = hex.replace('#', '')
  const r = parseInt(clean.substring(0, 2), 16)
  const g = parseInt(clean.substring(2, 4), 16)
  const b = parseInt(clean.substring(4, 6), 16)
  const lr = Math.round(r + (255 - r) * factor)
  const lg = Math.round(g + (255 - g) * factor)
  const lb = Math.round(b + (255 - b) * factor)
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`
}

/**
 * Darken a hex color by mixing with black.
 * factor: 0 = original, 1 = black
 */
function darkenHex(hex: string, factor: number): string {
  const clean = hex.replace('#', '')
  const r = parseInt(clean.substring(0, 2), 16)
  const g = parseInt(clean.substring(2, 4), 16)
  const b = parseInt(clean.substring(4, 6), 16)
  const dr = Math.round(r * (1 - factor))
  const dg = Math.round(g * (1 - factor))
  const db = Math.round(b * (1 - factor))
  return `#${dr.toString(16).padStart(2, '0')}${dg.toString(16).padStart(2, '0')}${db.toString(16).padStart(2, '0')}`
}

/** Map ThemeColors to CSS custom properties */
function colorsToCSSVars(colors: ThemeColors): Record<string, string> {
  return {
    '--color-bg-primary': colors.bgPrimary,
    '--color-bg-secondary': colors.bgSecondary,
    '--color-bg-tertiary': colors.bgTertiary,
    '--color-text-primary': colors.textPrimary,
    '--color-text-secondary': colors.textSecondary,
    '--color-text-muted': colors.textMuted,
    '--color-border-light': colors.borderLight,
    '--color-border-default': colors.borderDefault,
    '--color-accent': colors.accentPrimary,
    '--color-accent-secondary': colors.accentSecondary,
    '--color-accent-hover': colors.accentHover,
    '--color-accent-light': colors.accentLight,
    '--color-accent-ring': colors.accentRing,
    '--color-status-success': colors.statusSuccess,
    '--color-status-warning': colors.statusWarning,
    '--color-status-error': colors.statusError,
    '--color-status-info': colors.statusInfo,
  }
}

/** Generate accent CSS vars from a custom color */
function accentColorToCSSVars(color: string, isDark: boolean): Record<string, string> {
  return {
    '--color-accent': color,
    '--color-accent-secondary': isDark ? lightenHex(color, 0.25) : darkenHex(color, 0.15),
    '--color-accent-hover': isDark ? lightenHex(color, 0.25) : darkenHex(color, 0.15),
    '--color-accent-light': isDark ? hexToRgba(color, 0.15) : lightenHex(color, 0.85),
    '--color-accent-ring': hexToRgba(color, isDark ? 0.25 : 0.3),
  }
}

/**
 * Apply a theme preset to the document.
 * Sets CSS variables on `<html>` and manages dark/light class.
 *
 * @param preset - Theme preset ID
 * @param mode - Resolved mode ('light' | 'dark') — caller resolves 'system'
 * @param accentColor - Optional custom accent hex color (used when preset is 'custom')
 */
export function applyThemePreset(
  preset: ThemePreset,
  mode: 'light' | 'dark',
  accentColor?: string,
): void {
  const root = document.documentElement

  // 1. Toggle dark class
  if (mode === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }

  // 2. Remove old theme preset classes
  root.classList.remove('theme-hermes-gold', 'theme-github-dark', 'theme-solarized', 'theme-custom')

  // 3. Add theme preset class (except default)
  if (preset !== 'default') {
    root.classList.add(`theme-${preset}`)
  }

  // 4. Get colors for current mode
  const config = getPresetConfig(preset)
  const colors = mode === 'dark' ? config.dark : config.light
  const vars = colorsToCSSVars(colors)

  // 5. Apply CSS variables
  for (const [prop, value] of Object.entries(vars)) {
    root.style.setProperty(prop, value)
  }

  // 6. Override accent if custom color provided
  if (accentColor && (preset === 'custom' || accentColor !== colors.accentPrimary)) {
    const accentVars = accentColorToCSSVars(accentColor, mode === 'dark')
    for (const [prop, value] of Object.entries(accentVars)) {
      root.style.setProperty(prop, value)
    }
  }
}

/**
 * Resolve 'system' theme to actual 'light' or 'dark'.
 */
export function resolveThemeMode(theme: 'light' | 'dark' | 'system'): 'light' | 'dark' {
  if (theme === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return theme
}
