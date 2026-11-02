// ============================================================
// Nexus - StreamingMessage
// Real-time streaming response display with animated cursor
// Extracted from ChatPanel.tsx lines 1089-1110
// ============================================================

import { Sparkles, Copy, Check } from 'lucide-react'
import { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'

// ── Props ─────────────────────────────────────────────────────

export interface StreamingMessageProps {
  /** Currently accumulated streaming content */
  content: string
  /** Whether streaming is actively receiving tokens */
  isActive: boolean
}

// ── Copy Button ───────────────────────────────────────────────

function StreamingCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }, [text])

  return (
    <button
      onClick={handleCopy}
      className={cn(
        'absolute top-2 right-2 p-1.5 rounded-md transition-all duration-200',
        'opacity-0 group-hover:opacity-100',
        copied
          ? 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-400'
          : 'bg-white/80 text-gray-500 hover:text-gray-700 hover:bg-white dark:bg-[#2a2a45]/80 dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-[#2a2a45]',
      )}
      title={copied ? 'Đã copy!' : 'Copy nội dung'}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

// ── Component ─────────────────────────────────────────────────

export function StreamingMessage({ content, isActive }: StreamingMessageProps) {
  if (!isActive || !content) return null

  return (
    <div className="flex justify-start animate-slide-up">
      <div className="max-w-[85%] sm:max-w-[75%]">
        {/* Label row */}
        <div className="flex items-center gap-2 mb-1.5">
          <div className="w-6 h-6 rounded-[7px] bg-gradient-to-br from-[#3b82f6] to-[#818cf8] flex items-center justify-center">
            <Sparkles className="w-3 h-3 text-white" />
          </div>
          <span className="text-[11px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider">
            Nexus
          </span>
          {/* Streaming badge */}
          <span className="ml-auto flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            streaming
          </span>
        </div>

        {/* Streaming content bubble */}
        <div
          className={cn(
            'relative group rounded-[14px] rounded-bl-[4px] px-4 py-3',
            'bg-[#f4f5f7] dark:bg-[#1e1e35]',
          )}
        >
          {/* Copy button */}
          <StreamingCopyButton text={content} />

          <div className="text-[14px] leading-[1.65] prose-nexus">
            <MarkdownRenderer content={content} />
            {/* Animated cursor */}
            <span className="inline-block w-[2px] h-[1em] bg-[#3b82f6] animate-pulse ml-0.5 align-middle" />
          </div>
        </div>
      </div>
    </div>
  )
}
