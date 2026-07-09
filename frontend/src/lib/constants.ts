// ============================================================
// MekongAI Nexus - Application constants
// ============================================================

export const API_URL = import.meta.env.VITE_API_URL || ''
export const WS_PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
export const WS_URL = import.meta.env.VITE_WS_URL || `${WS_PROTOCOL}//${window.location.host}`

// ── Agent color system ────────────────────────────────────────
// Unique colors per agent role for visual distinction in debate,
// tool attribution, and timeline views.

export interface AgentColorSet {
  /** Tailwind border-left class */
  border: string
  /** Tailwind background (light + dark) */
  bg: string
  /** Tailwind text color (light + dark) */
  text: string
  /** Dot / indicator color */
  dot: string
  /** Hex color for inline styles (accent) */
  hex: string
}

/**
 * Lookup table: normalized role → color set.
 * Keys are lowercase, underscored role identifiers.
 */
const AGENT_COLOR_MAP: Record<string, AgentColorSet> = {
  geopolitician: {
    border: 'border-l-blue-500',
    bg: 'bg-blue-50 dark:bg-blue-900/20',
    text: 'text-blue-700 dark:text-blue-400',
    dot: 'bg-blue-500',
    hex: '#3b82f6',
  },
  historian: {
    border: 'border-l-amber-600',
    bg: 'bg-amber-50 dark:bg-amber-900/20',
    text: 'text-amber-700 dark:text-amber-400',
    dot: 'bg-amber-600',
    hex: '#d97706',
  },
  macro_economist: {
    border: 'border-l-emerald-500',
    bg: 'bg-emerald-50 dark:bg-emerald-900/20',
    text: 'text-emerald-700 dark:text-emerald-400',
    dot: 'bg-emerald-500',
    hex: '#10b981',
  },
  economist: {
    border: 'border-l-emerald-500',
    bg: 'bg-emerald-50 dark:bg-emerald-900/20',
    text: 'text-emerald-700 dark:text-emerald-400',
    dot: 'bg-emerald-500',
    hex: '#10b981',
  },
  analyst: {
    border: 'border-l-purple-500',
    bg: 'bg-purple-50 dark:bg-purple-900/20',
    text: 'text-purple-700 dark:text-purple-400',
    dot: 'bg-purple-500',
    hex: '#a855f7',
  },
  data_analyst: {
    border: 'border-l-purple-500',
    bg: 'bg-purple-50 dark:bg-purple-900/20',
    text: 'text-purple-700 dark:text-purple-400',
    dot: 'bg-purple-500',
    hex: '#a855f7',
  },
  legal_advisor: {
    border: 'border-l-red-500',
    bg: 'bg-red-50 dark:bg-red-900/20',
    text: 'text-red-700 dark:text-red-400',
    dot: 'bg-red-500',
    hex: '#ef4444',
  },
  lawyer: {
    border: 'border-l-red-500',
    bg: 'bg-red-50 dark:bg-red-900/20',
    text: 'text-red-700 dark:text-red-400',
    dot: 'bg-red-500',
    hex: '#ef4444',
  },
  researcher: {
    border: 'border-l-cyan-500',
    bg: 'bg-cyan-50 dark:bg-cyan-900/20',
    text: 'text-cyan-700 dark:text-cyan-400',
    dot: 'bg-cyan-500',
    hex: '#06b6d4',
  },
  developer: {
    border: 'border-l-green-500',
    bg: 'bg-green-50 dark:bg-green-900/20',
    text: 'text-green-700 dark:text-green-400',
    dot: 'bg-green-500',
    hex: '#22c55e',
  },
  engineer: {
    border: 'border-l-green-500',
    bg: 'bg-green-50 dark:bg-green-900/20',
    text: 'text-green-700 dark:text-green-400',
    dot: 'bg-green-500',
    hex: '#22c55e',
  },
  writer: {
    border: 'border-l-pink-500',
    bg: 'bg-pink-50 dark:bg-pink-900/20',
    text: 'text-pink-700 dark:text-pink-400',
    dot: 'bg-pink-500',
    hex: '#ec4899',
  },
  advocate: {
    border: 'border-l-blue-500',
    bg: 'bg-blue-50 dark:bg-blue-900/20',
    text: 'text-blue-700 dark:text-blue-400',
    dot: 'bg-blue-500',
    hex: '#3b82f6',
  },
  critic: {
    border: 'border-l-amber-500',
    bg: 'bg-amber-50 dark:bg-amber-900/20',
    text: 'text-amber-700 dark:text-amber-400',
    dot: 'bg-amber-500',
    hex: '#f59e0b',
  },
  skeptic: {
    border: 'border-l-amber-500',
    bg: 'bg-amber-50 dark:bg-amber-900/20',
    text: 'text-amber-700 dark:text-amber-400',
    dot: 'bg-amber-500',
    hex: '#f59e0b',
  },
  judge: {
    border: 'border-l-indigo-500',
    bg: 'bg-indigo-50 dark:bg-indigo-900/20',
    text: 'text-indigo-700 dark:text-indigo-400',
    dot: 'bg-indigo-500',
    hex: '#6366f1',
  },
  strategist: {
    border: 'border-l-teal-500',
    bg: 'bg-teal-50 dark:bg-teal-900/20',
    text: 'text-teal-700 dark:text-teal-400',
    dot: 'bg-teal-500',
    hex: '#14b8a6',
  },
  planner: {
    border: 'border-l-teal-500',
    bg: 'bg-teal-50 dark:bg-teal-900/20',
    text: 'text-teal-700 dark:text-teal-400',
    dot: 'bg-teal-500',
    hex: '#14b8a6',
  },
}

/**
 * Fallback palette for roles not in the explicit map.
 * Cycles deterministically based on role string hash.
 */
const FALLBACK_PALETTE: AgentColorSet[] = [
  { border: 'border-l-violet-500', bg: 'bg-violet-50 dark:bg-violet-900/20', text: 'text-violet-700 dark:text-violet-400', dot: 'bg-violet-500', hex: '#8b5cf6' },
  { border: 'border-l-orange-500', bg: 'bg-orange-50 dark:bg-orange-900/20', text: 'text-orange-700 dark:text-orange-400', dot: 'bg-orange-500', hex: '#f97316' },
  { border: 'border-l-sky-500', bg: 'bg-sky-50 dark:bg-sky-900/20', text: 'text-sky-700 dark:text-sky-400', dot: 'bg-sky-500', hex: '#0ea5e9' },
  { border: 'border-l-lime-500', bg: 'bg-lime-50 dark:bg-lime-900/20', text: 'text-lime-700 dark:text-lime-400', dot: 'bg-lime-500', hex: '#84cc16' },
  { border: 'border-l-fuchsia-500', bg: 'bg-fuchsia-50 dark:bg-fuchsia-900/20', text: 'text-fuchsia-700 dark:text-fuchsia-400', dot: 'bg-fuchsia-500', hex: '#d946ef' },
  { border: 'border-l-rose-500', bg: 'bg-rose-50 dark:bg-rose-900/20', text: 'text-rose-700 dark:text-rose-400', dot: 'bg-rose-500', hex: '#f43f5e' },
]

/** Simple string hash for deterministic fallback color selection */
function hashString(s: string): number {
  let hash = 0
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0
  }
  return Math.abs(hash)
}

/**
 * Get the color set for an agent role.
 * Accepts any combination of role/name string, normalizes and
 * returns a consistent color set.
 *
 * @param role - Agent role string (e.g. "Legal Advisor", "legal_advisor", "researcher")
 * @param agentName - Optional agent name used as fallback key for hashing
 */
export function getAgentColor(role?: unknown, agentName?: unknown): AgentColorSet {
  // DEFENSIVE: Handle any input type gracefully — never crash on unexpected data
  try {
    const key = String(role || agentName || '').toLowerCase().replace(/[\s-]+/g, '_').trim()
    if (!key) return FALLBACK_PALETTE[0]

    // Direct match
    if (AGENT_COLOR_MAP[key]) return AGENT_COLOR_MAP[key]

    // Partial match — check if key contains a known role
    for (const [knownRole, colors] of Object.entries(AGENT_COLOR_MAP)) {
      if (key.includes(knownRole) || knownRole.includes(key)) {
        return colors
      }
    }

    // Deterministic fallback based on hash
    const idx = hashString(key) % FALLBACK_PALETTE.length
    return FALLBACK_PALETTE[idx]
  } catch {
    // DEFENSIVE: Any failure → return first fallback color (never crash)
    return FALLBACK_PALETTE[0]
  }
}

/** Agent emoji based on role — DEFENSIVE: accepts any input type */
export function getAgentEmoji(role?: unknown): string {
  try {
    const r = String(role || '').toLowerCase()
    if (r.includes('legal') || r.includes('lawyer')) return '⚖️'
    if (r.includes('research')) return '🔬'
    if (r.includes('analyst') || r.includes('data')) return '📊'
    if (r.includes('develop') || r.includes('engineer')) return '💻'
    if (r.includes('writer')) return '✍️'
    if (r.includes('economist')) return '📈'
    if (r.includes('historian')) return '📚'
    if (r.includes('geopolit') || r.includes('strategist')) return '🌍'
    if (r.includes('advocate') || r.includes('support')) return '🛡️'
    if (r.includes('critic') || r.includes('skeptic')) return '🔍'
    if (r.includes('judge')) return '⚖️'
    if (r.includes('planner')) return '🗺️'
    return '🤖'
  } catch {
    return '🤖'
  }
}

export const POLLING_INTERVALS = {
  /** FIX-P2.3: Added 'live' interval (1s) for active task event polling */
  live: 1000,
  fast: 3000,
  normal: 5000,
  slow: 10000,
  lazy: 30000,
} as const

export const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50MB
export const MAX_FILES_PER_UPLOAD = 10

export const SUPPORTED_FILE_TYPES = '.pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.txt,.md,.csv,.json,.xml,.yaml,.yml,.html,.htm,.png,.jpg,.jpeg,.gif,.bmp,.webp,.wav,.mp3,.zip,.py,.js,.ts,.jsx,.tsx,.java,.c,.cpp,.go,.rs,.rb,.php,.sql,.sh'

// ── Long message character limits ─────────────────────────────
export const CHAR_LIMITS = {
  /** Show counter (subtle gray) */
  INFO: 50_000,
  /** Counter turns yellow + hint text */
  WARNING: 80_000,
  /** Counter turns red */
  DANGER: 400_000,
  /** Block send + error text */
  LIMIT: 500_000,
} as const

export const MAX_MESSAGE_CHARS = CHAR_LIMITS.LIMIT
