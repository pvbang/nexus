// ============================================================
// MekongAI Nexus - Lightweight ANSI Escape Code Parser & Renderer
// Parses basic ANSI codes and renders as styled HTML spans.
// No external dependencies — custom parser for terminal output.
// ============================================================

interface AnsiSegment {
  text: string
  bold: boolean
  dim: boolean
  italic: boolean
  underline: boolean
  fgColor: string | null
  bgColor: string | null
}

// Standard 4-bit ANSI colors (30-37 / 90-97)
const FG_COLORS: Record<number, string> = {
  30: '#1a1a2e', 31: '#ef4444', 32: '#22c55e', 33: '#eab308',
  34: '#3b82f6', 35: '#a855f7', 36: '#06b6d4', 37: '#d1d5db',
  90: '#6b7280', 91: '#f87171', 92: '#4ade80', 93: '#facc15',
  94: '#60a5fa', 95: '#c084fc', 96: '#22d3ee', 97: '#f9fafb',
}

const BG_COLORS: Record<number, string> = {
  40: '#1a1a2e', 41: '#ef4444', 42: '#22c55e', 43: '#eab308',
  44: '#3b82f6', 45: '#a855f7', 46: '#06b6d4', 47: '#d1d5db',
  100: '#6b7280', 101: '#f87171', 102: '#4ade80', 103: '#facc15',
  104: '#60a5fa', 105: '#c084fc', 106: '#22d3ee', 107: '#f9fafb',
}

// 256-color palette (first 16 = standard, 16-231 = 6×6×6 cube, 232-255 = grayscale)
function get256Color(n: number): string {
  if (n < 16) {
    const palette16 = [
      '#1a1a2e', '#ef4444', '#22c55e', '#eab308',
      '#3b82f6', '#a855f7', '#06b6d4', '#d1d5db',
      '#6b7280', '#f87171', '#4ade80', '#facc15',
      '#60a5fa', '#c084fc', '#22d3ee', '#f9fafb',
    ]
    return palette16[n] ?? '#d1d5db'
  }
  if (n < 232) {
    const idx = n - 16
    const r = Math.floor(idx / 36)
    const g = Math.floor((idx % 36) / 6)
    const b = idx % 6
    const toHex = (v: number) => (v === 0 ? 0 : 55 + v * 40)
    return `rgb(${toHex(r)},${toHex(g)},${toHex(b)})`
  }
  // Grayscale: 232-255 → 8 to 238
  const gray = 8 + (n - 232) * 10
  return `rgb(${gray},${gray},${gray})`
}

/** Parse ANSI escape codes from text into styled segments. */
export function parseAnsi(text: string): AnsiSegment[] {
  const segments: AnsiSegment[] = []
  // Match ESC[ ... m sequences
  const ansiRegex = /\x1b\[([0-9;]*)m/g

  let bold = false
  let dim = false
  let italic = false
  let underline = false
  let fgColor: string | null = null
  let bgColor: string | null = null

  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = ansiRegex.exec(text)) !== null) {
    // Push text before this escape sequence
    if (match.index > lastIndex) {
      const chunk = text.slice(lastIndex, match.index)
      if (chunk) {
        segments.push({ text: chunk, bold, dim, italic, underline, fgColor, bgColor })
      }
    }
    lastIndex = ansiRegex.lastIndex

    // Parse SGR parameters
    const params = match[1] ? match[1].split(';').map(Number) : [0]
    let i = 0
    while (i < params.length) {
      const code = params[i]
      if (code === 0) {
        // Reset all
        bold = false; dim = false; italic = false; underline = false
        fgColor = null; bgColor = null
      } else if (code === 1) {
        bold = true
      } else if (code === 2) {
        dim = true
      } else if (code === 3) {
        italic = true
      } else if (code === 4) {
        underline = true
      } else if (code === 22) {
        bold = false; dim = false
      } else if (code === 23) {
        italic = false
      } else if (code === 24) {
        underline = false
      } else if (code === 39) {
        fgColor = null
      } else if (code === 49) {
        bgColor = null
      } else if (code >= 30 && code <= 37) {
        fgColor = FG_COLORS[code] ?? null
      } else if (code >= 90 && code <= 97) {
        fgColor = FG_COLORS[code] ?? null
      } else if (code >= 40 && code <= 47) {
        bgColor = BG_COLORS[code] ?? null
      } else if (code >= 100 && code <= 107) {
        bgColor = BG_COLORS[code] ?? null
      } else if (code === 38 && params[i + 1] === 5) {
        // 256-color foreground: ESC[38;5;{n}m
        fgColor = get256Color(params[i + 2] ?? 0)
        i += 2
      } else if (code === 48 && params[i + 1] === 5) {
        // 256-color background: ESC[48;5;{n}m
        bgColor = get256Color(params[i + 2] ?? 0)
        i += 2
      }
      i++
    }
  }

  // Push remaining text after last escape sequence
  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex)
    if (remaining) {
      segments.push({ text: remaining, bold, dim, italic, underline, fgColor, bgColor })
    }
  }

  // If no escapes found, return whole text as single segment
  if (segments.length === 0 && text) {
    segments.push({ text, bold: false, dim: false, italic: false, underline: false, fgColor: null, bgColor: null })
  }

  return segments
}

/** Strip all ANSI escape codes from text. */
export function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '')
}

/** Render ANSI-colored text as styled spans. */
export function AnsiText({ text }: { text: string }) {
  const segments = parseAnsi(text)

  return (
    <>
      {segments.map((seg, i) => {
        const style: React.CSSProperties = {}
        if (seg.fgColor) style.color = seg.fgColor
        if (seg.bgColor) style.backgroundColor = seg.bgColor
        if (seg.bold) style.fontWeight = 700
        if (seg.dim) style.opacity = 0.6
        if (seg.italic) style.fontStyle = 'italic'
        if (seg.underline) style.textDecoration = 'underline'

        const hasStyle = Object.keys(style).length > 0
        return hasStyle
          ? <span key={i} style={style}>{seg.text}</span>
          : <span key={i}>{seg.text}</span>
      })}
    </>
  )
}
