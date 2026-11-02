// ============================================================
// Nexus - Theme Presets (Feature #7)
// 4 preset themes + custom accent color support
// ============================================================

export type ThemePreset = 'default' | 'hermes-gold' | 'github-dark' | 'solarized' | 'custom'

export interface ThemeColors {
  // Backgrounds
  bgPrimary: string
  bgSecondary: string
  bgTertiary: string
  // Text
  textPrimary: string
  textSecondary: string
  textMuted: string
  // Borders
  borderLight: string
  borderDefault: string
  // Accent/Brand
  accentPrimary: string
  accentSecondary: string
  accentHover: string
  accentLight: string
  accentRing: string
  // Status (same across themes)
  statusSuccess: string
  statusWarning: string
  statusError: string
  statusInfo: string
}

export interface ThemePresetConfig {
  id: ThemePreset
  name: string
  description: string
  light: ThemeColors
  dark: ThemeColors
}

// ── Preset: Default (Blue) ──────────────────────────────────
const defaultPreset: ThemePresetConfig = {
  id: 'default',
  name: 'Default Blue',
  description: 'Clean blue accent, professional look',
  light: {
    bgPrimary: '#ffffff',
    bgSecondary: '#f8f9fb',
    bgTertiary: '#f0f1f3',
    textPrimary: '#222222',
    textSecondary: '#45515e',
    textMuted: '#8e8e93',
    borderLight: '#f2f3f5',
    borderDefault: '#e5e7eb',
    accentPrimary: '#3B82F6',
    accentSecondary: '#2563EB',
    accentHover: '#2563EB',
    accentLight: '#DBEAFE',
    accentRing: 'rgba(59, 130, 246, 0.3)',
    statusSuccess: '#22c55e',
    statusWarning: '#f59e0b',
    statusError: '#ef4444',
    statusInfo: '#3b82f6',
  },
  dark: {
    bgPrimary: '#0f0f17',
    bgSecondary: '#1a1a2e',
    bgTertiary: '#232340',
    textPrimary: '#e4e4ed',
    textSecondary: '#a0a0b8',
    textMuted: '#6b6b85',
    borderLight: '#2a2a45',
    borderDefault: '#3a3a55',
    accentPrimary: '#3B82F6',
    accentSecondary: '#60A5FA',
    accentHover: '#60A5FA',
    accentLight: 'rgba(59, 130, 246, 0.15)',
    accentRing: 'rgba(59, 130, 246, 0.25)',
    statusSuccess: '#22c55e',
    statusWarning: '#f59e0b',
    statusError: '#ef4444',
    statusInfo: '#60a5fa',
  },
}

// ── Preset: Hermes Gold ─────────────────────────────────────
const hermesGoldPreset: ThemePresetConfig = {
  id: 'hermes-gold',
  name: 'Hermes Gold',
  description: 'Warm gold accent, elegant charcoal',
  light: {
    bgPrimary: '#fffdf7',
    bgSecondary: '#faf6ee',
    bgTertiary: '#f3efe5',
    textPrimary: '#2d2518',
    textSecondary: '#5c5040',
    textMuted: '#9a8e78',
    borderLight: '#ede8dc',
    borderDefault: '#ddd6c5',
    accentPrimary: '#D97706',
    accentSecondary: '#B45309',
    accentHover: '#B45309',
    accentLight: '#FEF3C7',
    accentRing: 'rgba(217, 119, 6, 0.3)',
    statusSuccess: '#22c55e',
    statusWarning: '#f59e0b',
    statusError: '#ef4444',
    statusInfo: '#D97706',
  },
  dark: {
    bgPrimary: '#121008',
    bgSecondary: '#1c1912',
    bgTertiary: '#28241a',
    textPrimary: '#f0e8d8',
    textSecondary: '#b8a890',
    textMuted: '#7a6e5c',
    borderLight: '#302a1e',
    borderDefault: '#403828',
    accentPrimary: '#F59E0B',
    accentSecondary: '#FBBF24',
    accentHover: '#FBBF24',
    accentLight: 'rgba(245, 158, 11, 0.15)',
    accentRing: 'rgba(245, 158, 11, 0.25)',
    statusSuccess: '#22c55e',
    statusWarning: '#f59e0b',
    statusError: '#ef4444',
    statusInfo: '#FBBF24',
  },
}

// ── Preset: GitHub Dark ─────────────────────────────────────
const githubDarkPreset: ThemePresetConfig = {
  id: 'github-dark',
  name: 'GitHub Dark',
  description: 'GitHub-inspired, green accent',
  light: {
    bgPrimary: '#ffffff',
    bgSecondary: '#f6f8fa',
    bgTertiary: '#eef1f5',
    textPrimary: '#1f2328',
    textSecondary: '#656d76',
    textMuted: '#8b949e',
    borderLight: '#d8dee4',
    borderDefault: '#d0d7de',
    accentPrimary: '#238636',
    accentSecondary: '#1a7f37',
    accentHover: '#1a7f37',
    accentLight: '#dafbe1',
    accentRing: 'rgba(35, 134, 54, 0.3)',
    statusSuccess: '#22c55e',
    statusWarning: '#f59e0b',
    statusError: '#ef4444',
    statusInfo: '#238636',
  },
  dark: {
    bgPrimary: '#0d1117',
    bgSecondary: '#161b22',
    bgTertiary: '#21262d',
    textPrimary: '#e6edf3',
    textSecondary: '#8b949e',
    textMuted: '#6e7681',
    borderLight: '#21262d',
    borderDefault: '#30363d',
    accentPrimary: '#238636',
    accentSecondary: '#3fb950',
    accentHover: '#3fb950',
    accentLight: 'rgba(35, 134, 54, 0.15)',
    accentRing: 'rgba(35, 134, 54, 0.25)',
    statusSuccess: '#3fb950',
    statusWarning: '#d29922',
    statusError: '#f85149',
    statusInfo: '#58a6ff',
  },
}

// ── Preset: Solarized ───────────────────────────────────────
const solarizedPreset: ThemePresetConfig = {
  id: 'solarized',
  name: 'Solarized',
  description: 'Ethan Schoonover\'s classic, cyan accent',
  light: {
    bgPrimary: '#fdf6e3',
    bgSecondary: '#eee8d5',
    bgTertiary: '#e6dfc8',
    textPrimary: '#073642',
    textSecondary: '#586e75',
    textMuted: '#93a1a1',
    borderLight: '#e6dfc8',
    borderDefault: '#d3cbb8',
    accentPrimary: '#2AA198',
    accentSecondary: '#268BD2',
    accentHover: '#268BD2',
    accentLight: '#d5f0ee',
    accentRing: 'rgba(42, 161, 152, 0.3)',
    statusSuccess: '#859900',
    statusWarning: '#b58900',
    statusError: '#dc322f',
    statusInfo: '#268bd2',
  },
  dark: {
    bgPrimary: '#002b36',
    bgSecondary: '#073642',
    bgTertiary: '#0a4050',
    textPrimary: '#fdf6e3',
    textSecondary: '#93a1a1',
    textMuted: '#657b83',
    borderLight: '#0a4050',
    borderDefault: '#11535e',
    accentPrimary: '#2AA198',
    accentSecondary: '#36c2b8',
    accentHover: '#36c2b8',
    accentLight: 'rgba(42, 161, 152, 0.15)',
    accentRing: 'rgba(42, 161, 152, 0.25)',
    statusSuccess: '#859900',
    statusWarning: '#b58900',
    statusError: '#dc322f',
    statusInfo: '#268bd2',
  },
}

// ── All Presets Map ─────────────────────────────────────────

export const THEME_PRESETS: Record<Exclude<ThemePreset, 'custom'>, ThemePresetConfig> = {
  'default': defaultPreset,
  'hermes-gold': hermesGoldPreset,
  'github-dark': githubDarkPreset,
  'solarized': solarizedPreset,
}

/** Get theme preset config; for 'custom' returns default as base */
export function getPresetConfig(preset: ThemePreset): ThemePresetConfig {
  if (preset === 'custom') return { ...defaultPreset, id: 'custom', name: 'Custom', description: 'Your custom accent color' }
  return THEME_PRESETS[preset]
}

// ── Preset Accent Swatches (for UI picker) ──────────────────

export interface AccentSwatch {
  name: string
  color: string
}

export const ACCENT_SWATCHES: AccentSwatch[] = [
  { name: 'Blue', color: '#3B82F6' },
  { name: 'Indigo', color: '#6366F1' },
  { name: 'Purple', color: '#8B5CF6' },
  { name: 'Pink', color: '#EC4899' },
  { name: 'Red', color: '#EF4444' },
  { name: 'Orange', color: '#F97316' },
  { name: 'Amber', color: '#F59E0B' },
  { name: 'Teal', color: '#14B8A6' },
]
