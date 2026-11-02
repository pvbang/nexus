// ============================================================
// Nexus - ToolOutputRenderer
// Smart output renderer for tool execution results.
// Auto-detects format (JSON, Markdown, HTML, plain text) and renders
// appropriately with syntax highlighting, copy button, and fullscreen modal.
// ============================================================

import { useState, useCallback, useEffect, useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Copy,
  Check,
  Maximize2,
  X,
  ChevronRight,
  ChevronDown,
  FileJson2,
  FileText,
  Code2,
  Globe,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from './MarkdownRenderer'

// ── Types ─────────────────────────────────────────────────────

export type OutputFormat = 'json' | 'markdown' | 'html' | 'plain'

export interface ToolOutputRendererProps {
  /** The raw output value — string or object */
  value: unknown
  /** Max height in pixels before scroll (default: 300) */
  maxHeight?: number
  /** Label above the output block */
  label?: string
  /** Show fullscreen button (default: true) */
  showFullscreen?: boolean
  /** Compact mode — smaller text, less padding (default: false) */
  compact?: boolean
  /** Error mode — red styling */
  isError?: boolean
  /** Called when fullscreen is requested (external modal control) */
  onFullscreen?: () => void
}

// ── Format Detection ──────────────────────────────────────────

/** Detect the format of a string value */
function detectFormat(value: string): OutputFormat {
  const trimmed = value.trim()

  // JSON: starts with { or [ and is valid JSON
  if ((trimmed.startsWith('{') || trimmed.startsWith('[')) && trimmed.length > 1) {
    try {
      JSON.parse(trimmed)
      return 'json'
    } catch {
      // Not valid JSON, continue checking
    }
  }

  // HTML: contains HTML tags
  if (/<[a-z][\s\S]*>/i.test(trimmed) && (
    trimmed.includes('</') || trimmed.includes('/>') ||
    trimmed.startsWith('<!DOCTYPE') || trimmed.startsWith('<html')
  )) {
    return 'html'
  }

  // Markdown: has common markdown indicators
  const mdIndicators = [
    /^#{1,6}\s/m,          // headings
    /\*\*[^*]+\*\*/,       // bold
    /\[.+\]\(.+\)/,        // links
    /^[-*+]\s/m,            // unordered list
    /^\d+\.\s/m,            // ordered list
    /^```/m,                // code blocks
    /^\|.+\|/m,             // tables
    /^>\s/m,                // blockquotes
  ]
  const mdMatchCount = mdIndicators.filter((re) => re.test(trimmed)).length
  if (mdMatchCount >= 2) {
    return 'markdown'
  }

  return 'plain'
}

// ── Copy Button ───────────────────────────────────────────────

function CopyButton({ text, size = 'sm' }: { text: string; size?: 'sm' | 'xs' }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }, [text])

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={cn(
        'shrink-0 rounded transition-colors flex items-center gap-1',
        size === 'sm'
          ? 'p-1.5 hover:bg-gray-100 dark:hover:bg-[#22223a]'
          : 'p-1 hover:bg-gray-100 dark:hover:bg-[#22223a]',
        'text-gray-400 hover:text-gray-700 dark:hover:text-gray-200',
      )}
      title={copied ? 'Copied!' : 'Copy output'}
    >
      {copied ? (
        <Check className={cn(size === 'sm' ? 'h-3.5 w-3.5' : 'h-3 w-3', 'text-emerald-500')} />
      ) : (
        <Copy className={cn(size === 'sm' ? 'h-3.5 w-3.5' : 'h-3 w-3')} />
      )}
      {size === 'sm' && (
        <span className="text-[10px] font-medium">{copied ? 'Copied' : 'Copy'}</span>
      )}
    </button>
  )
}

// ── JSON Collapsible Tree ─────────────────────────────────────

interface JsonNodeProps {
  keyName?: string
  value: unknown
  depth: number
  defaultExpanded?: boolean
  /** When true, string values are never truncated (used in fullscreen modal) */
  allowFullStrings?: boolean
}

function JsonNode({ keyName, value, depth, defaultExpanded = true, allowFullStrings = false }: JsonNodeProps) {
  const [expanded, setExpanded] = useState(defaultExpanded && depth < 2)
  const [showFullString, setShowFullString] = useState(false)

  if (value === null) {
    return (
      <span className="inline">
        {keyName !== undefined && (
          <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
        )}
        {keyName !== undefined && <span className="text-gray-500">: </span>}
        <span className="text-gray-400 italic">null</span>
      </span>
    )
  }

  if (typeof value === 'boolean') {
    return (
      <span className="inline">
        {keyName !== undefined && (
          <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
        )}
        {keyName !== undefined && <span className="text-gray-500">: </span>}
        <span className="text-orange-500 dark:text-orange-400">{String(value)}</span>
      </span>
    )
  }

  if (typeof value === 'number') {
    return (
      <span className="inline">
        {keyName !== undefined && (
          <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
        )}
        {keyName !== undefined && <span className="text-gray-500">: </span>}
        <span className="text-blue-600 dark:text-blue-400">{value}</span>
      </span>
    )
  }

  if (typeof value === 'string') {
    const shouldTruncate = !allowFullStrings && !showFullString && value.length > 200
    const displayStr = shouldTruncate ? value.slice(0, 200) + '…' : value
    return (
      <span className="inline">
        {keyName !== undefined && (
          <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
        )}
        {keyName !== undefined && <span className="text-gray-500">: </span>}
        <span className="text-emerald-600 dark:text-emerald-400 whitespace-pre-wrap break-words">"{displayStr}"</span>
        {value.length > 200 && !allowFullStrings && (
          <button
            type="button"
            onClick={() => setShowFullString((p) => !p)}
            className="text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 text-[9px] ml-1 underline"
          >
            {showFullString ? 'collapse' : `show all (${value.length.toLocaleString()} chars)`}
          </button>
        )}
      </span>
    )
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return (
        <span className="inline">
          {keyName !== undefined && (
            <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
          )}
          {keyName !== undefined && <span className="text-gray-500">: </span>}
          <span className="text-gray-500">[]</span>
        </span>
      )
    }

    return (
      <div className="inline-block align-top">
        <span
          className="cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded px-0.5 -mx-0.5 inline-flex items-center gap-0.5"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded
            ? <ChevronDown className="h-3 w-3 text-gray-400 inline" />
            : <ChevronRight className="h-3 w-3 text-gray-400 inline" />}
          {keyName !== undefined && (
            <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
          )}
          {keyName !== undefined && <span className="text-gray-500">: </span>}
          <span className="text-gray-500">[</span>
          {!expanded && (
            <span className="text-gray-400 text-[10px]"> {value.length} items </span>
          )}
          {!expanded && <span className="text-gray-500">]</span>}
        </span>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 dark:border-gray-700 pl-2">
            {value.map((item, idx) => (
              <div key={idx} className="py-0.5">
                <span className="text-gray-400 text-[10px] mr-1">{idx}</span>
                <JsonNode value={item} depth={depth + 1} defaultExpanded={depth < 1} allowFullStrings={allowFullStrings} />
                {idx < value.length - 1 && <span className="text-gray-400">,</span>}
              </div>
            ))}
          </div>
        )}
        {expanded && <span className="text-gray-500">]</span>}
      </div>
    )
  }

  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) {
      return (
        <span className="inline">
          {keyName !== undefined && (
            <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
          )}
          {keyName !== undefined && <span className="text-gray-500">: </span>}
          <span className="text-gray-500">{'{}'}</span>
        </span>
      )
    }

    return (
      <div className="inline-block align-top">
        <span
          className="cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded px-0.5 -mx-0.5 inline-flex items-center gap-0.5"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded
            ? <ChevronDown className="h-3 w-3 text-gray-400 inline" />
            : <ChevronRight className="h-3 w-3 text-gray-400 inline" />}
          {keyName !== undefined && (
            <span className="text-purple-600 dark:text-purple-400">"{keyName}"</span>
          )}
          {keyName !== undefined && <span className="text-gray-500">: </span>}
          <span className="text-gray-500">{'{'}</span>
          {!expanded && (
            <span className="text-gray-400 text-[10px]"> {entries.length} keys </span>
          )}
          {!expanded && <span className="text-gray-500">{'}'}</span>}
        </span>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 dark:border-gray-700 pl-2">
            {entries.map(([k, v], idx) => (
              <div key={k} className="py-0.5">
                <JsonNode keyName={k} value={v} depth={depth + 1} defaultExpanded={depth < 1} allowFullStrings={allowFullStrings} />
                {idx < entries.length - 1 && <span className="text-gray-400">,</span>}
              </div>
            ))}
          </div>
        )}
        {expanded && <span className="text-gray-500">{'}'}</span>}
      </div>
    )
  }

  return <span className="text-gray-500">{String(value)}</span>
}

// ── HTML Renderer (strip dangerous tags, show preview) ────────

function HtmlPreview({ html }: { html: string }) {
  // Strip script/style, keep safe tags for readability
  const cleaned = html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<link[^>]*>/gi, '')
    .replace(/<meta[^>]*>/gi, '')

  // Simple text extraction for preview
  const textOnly = cleaned
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  const [showRaw, setShowRaw] = useState(false)

  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1">
        <button
          type="button"
          onClick={() => setShowRaw(false)}
          className={cn(
            'px-1.5 py-0.5 rounded text-[9px] font-bold transition-colors',
            !showRaw
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
              : 'bg-gray-100 text-gray-500 dark:bg-[#1a1a2e] dark:text-gray-500',
          )}
        >
          Preview
        </button>
        <button
          type="button"
          onClick={() => setShowRaw(true)}
          className={cn(
            'px-1.5 py-0.5 rounded text-[9px] font-bold transition-colors',
            showRaw
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
              : 'bg-gray-100 text-gray-500 dark:bg-[#1a1a2e] dark:text-gray-500',
          )}
        >
          Raw HTML
        </button>
      </div>
      {showRaw ? (
        <pre className={cn(
          'text-[11px] leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
          'bg-gray-50 text-gray-800 border border-gray-100',
          'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
        )}>
          {html}
        </pre>
      ) : (
        <div className={cn(
          'text-[12px] leading-relaxed p-3 rounded-lg overflow-auto',
          'bg-gray-50 text-gray-800 border border-gray-100',
          'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
        )}>
          {textOnly || <span className="text-gray-400 italic">Empty HTML</span>}
        </div>
      )}
    </div>
  )
}

// ── Format Badge ──────────────────────────────────────────────

function FormatBadge({ format }: { format: OutputFormat }) {
  const config = {
    json: { icon: FileJson2, label: 'JSON', color: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20' },
    markdown: { icon: FileText, label: 'Markdown', color: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20' },
    html: { icon: Globe, label: 'HTML', color: 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20' },
    plain: { icon: Code2, label: 'Text', color: 'text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800/50' },
  }
  const cfg = config[format]
  const Icon = cfg.icon

  return (
    <span className={cn('inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold', cfg.color)}>
      <Icon className="h-2.5 w-2.5" />
      {cfg.label}
    </span>
  )
}

// ── Fullscreen Modal ──────────────────────────────────────────

function FullscreenModal({
  value,
  rawStr,
  format,
  onClose,
  isError,
}: {
  value: unknown
  rawStr: string
  format: OutputFormat
  onClose: () => void
  isError?: boolean
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      {/* Modal */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 8 }}
        transition={{ duration: 0.15 }}
        className={cn(
          'relative z-10 w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden',
          'bg-white dark:bg-[#0e0e20] border border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 dark:border-[#1a1a2e] shrink-0">
          <FormatBadge format={format} />
          <span className="text-[11px] text-gray-400 font-mono">
            {(rawStr.length / 1024).toFixed(1)}KB • {rawStr.split('\n').length} lines
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            <CopyButton text={rawStr} size="sm" />
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          <OutputContent
            value={value}
            rawStr={rawStr}
            format={format}
            maxHeight={undefined}
            isError={isError}
            compact={false}
            allowFullStrings
          />
        </div>
      </motion.div>
    </div>
  )
}

// ── Output Content (inner renderer) ───────────────────────────

function OutputContent({
  value,
  rawStr,
  format,
  maxHeight,
  isError,
  compact,
  allowFullStrings = false,
}: {
  value: unknown
  rawStr: string
  format: OutputFormat
  maxHeight?: number
  isError?: boolean
  compact?: boolean
  /** When true, JsonNode string values are never truncated */
  allowFullStrings?: boolean
}) {
  const [viewMode, setViewMode] = useState<'formatted' | 'raw'>('formatted')
  const textSize = compact ? 'text-[10px]' : 'text-[11px]'

  if (isError) {
    return (
      <pre
        className={cn(
          textSize, 'leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
          'bg-red-50 text-red-700 border border-red-100',
          'dark:bg-red-900/20 dark:text-red-400 dark:border-red-800',
        )}
        style={maxHeight ? { maxHeight } : undefined}
      >
        {rawStr}
      </pre>
    )
  }

  // JSON format
  if (format === 'json') {
    let parsed: unknown
    try {
      parsed = typeof value === 'object' && value !== null ? value : JSON.parse(rawStr)
    } catch {
      parsed = null
    }

    return (
      <div>
        <div className="flex items-center gap-1 mb-1.5">
          {(['formatted', 'raw'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setViewMode(m)}
              className={cn(
                'px-1.5 py-0.5 rounded text-[9px] font-bold transition-colors',
                viewMode === m
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                  : 'bg-gray-100 text-gray-500 dark:bg-[#1a1a2e] dark:text-gray-500',
              )}
            >
              {m === 'formatted' ? 'Tree' : 'Raw JSON'}
            </button>
          ))}
        </div>
        {viewMode === 'formatted' && parsed ? (
          <div
            className={cn(
              textSize, 'leading-relaxed font-mono p-3 rounded-lg overflow-auto',
              'bg-gray-50 border border-gray-100',
              'dark:bg-[#0d0d1a] dark:border-[#1a1a2e]',
            )}
            style={maxHeight ? { maxHeight } : undefined}
          >
            <JsonNode value={parsed} depth={0} defaultExpanded allowFullStrings={allowFullStrings} />
          </div>
        ) : (
          <pre
            className={cn(
              textSize, 'leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
              'bg-gray-50 text-gray-800 border border-gray-100',
              'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
            )}
            style={maxHeight ? { maxHeight } : undefined}
          >
            {typeof value === 'object' && value !== null
              ? JSON.stringify(value, null, 2)
              : rawStr}
          </pre>
        )}
      </div>
    )
  }

  // Markdown format
  if (format === 'markdown') {
    return (
      <div>
        <div className="flex items-center gap-1 mb-1.5">
          {(['formatted', 'raw'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setViewMode(m)}
              className={cn(
                'px-1.5 py-0.5 rounded text-[9px] font-bold transition-colors',
                viewMode === m
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                  : 'bg-gray-100 text-gray-500 dark:bg-[#1a1a2e] dark:text-gray-500',
              )}
            >
              {m === 'formatted' ? 'Rendered' : 'Raw'}
            </button>
          ))}
        </div>
        {viewMode === 'formatted' ? (
          <div
            className={cn(
              'p-3 rounded-lg overflow-auto',
              'bg-gray-50 border border-gray-100',
              'dark:bg-[#0d0d1a] dark:border-[#1a1a2e]',
              compact ? 'text-[11px]' : 'text-[12px]',
            )}
            style={maxHeight ? { maxHeight } : undefined}
          >
            <MarkdownRenderer content={rawStr} className={compact ? 'text-[11px]' : 'text-[12px]'} />
          </div>
        ) : (
          <pre
            className={cn(
              textSize, 'leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
              'bg-gray-50 text-gray-800 border border-gray-100',
              'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
            )}
            style={maxHeight ? { maxHeight } : undefined}
          >
            {rawStr}
          </pre>
        )}
      </div>
    )
  }

  // HTML format
  if (format === 'html') {
    return <HtmlPreview html={rawStr} />
  }

  // Plain text
  return (
    <pre
      className={cn(
        textSize, 'leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
        'bg-gray-50 text-gray-800 border border-gray-100',
        'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
      )}
      style={maxHeight ? { maxHeight } : undefined}
    >
      {rawStr}
    </pre>
  )
}

// ── Main Component ────────────────────────────────────────────

export function ToolOutputRenderer({
  value,
  maxHeight = 300,
  label,
  showFullscreen = true,
  compact = false,
  isError = false,
  onFullscreen,
}: ToolOutputRendererProps) {
  const [showModal, setShowModal] = useState(false)

  // Normalize value to string
  const rawStr = useMemo(() => {
    if (value === null || value === undefined) return ''
    if (typeof value === 'string') return value
    if (typeof value === 'object') {
      try {
        return JSON.stringify(value, null, 2)
      } catch {
        return String(value)
      }
    }
    return String(value)
  }, [value])

  // Detect format
  const format = useMemo<OutputFormat>(() => {
    if (isError) return 'plain'
    if (typeof value === 'object' && value !== null) return 'json'
    if (typeof value === 'string') return detectFormat(value)
    return 'plain'
  }, [value, isError])

  if (!rawStr) {
    return <span className="text-gray-400 italic text-[11px]">—</span>
  }

  const isLong = rawStr.length > 500

  return (
    <div>
      {/* Header bar */}
      <div className="flex items-center gap-1.5 mb-1.5">
        {label && (
          <span className={cn('font-bold text-gray-400 uppercase', compact ? 'text-[8px]' : 'text-[9px]')}>
            {label}
          </span>
        )}
        <FormatBadge format={format} />
        {isLong && (
          <span className="text-[9px] text-gray-400 font-mono">
            {(rawStr.length / 1024).toFixed(1)}KB
          </span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <CopyButton text={rawStr} size="xs" />
          {showFullscreen && isLong && (
            <button
              type="button"
              onClick={() => {
                if (onFullscreen) {
                  onFullscreen()
                } else {
                  setShowModal(true)
                }
              }}
              className="p-1 rounded hover:bg-gray-100 dark:hover:bg-[#22223a] transition-colors text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              title="Fullscreen"
            >
              <Maximize2 className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <OutputContent
        value={value}
        rawStr={rawStr}
        format={format}
        maxHeight={maxHeight}
        isError={isError}
        compact={compact}
      />

      {/* Fullscreen modal */}
      <AnimatePresence>
        {showModal && (
          <FullscreenModal
            value={value}
            rawStr={rawStr}
            format={format}
            onClose={() => setShowModal(false)}
            isError={isError}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
