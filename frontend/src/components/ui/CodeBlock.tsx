// ============================================================
// MekongAI Nexus - CodeBlock UI Primitive
// Standalone syntax-styled code block with copy button and line numbers
// ============================================================

import { useState, useCallback, useRef } from 'react'
import { cn } from '../../lib/cn'

export interface CodeBlockProps {
  /** Code string to display */
  code: string
  /** Language label (displayed in header) */
  language?: string
  /** Show line numbers */
  showLineNumbers?: boolean
  /** Max height with scroll (CSS value) */
  maxHeight?: string
  /** Show copy button */
  copyable?: boolean
  /** Additional class names */
  className?: string
}

/** Copy icon SVG */
function CopyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9.75a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"
      />
    </svg>
  )
}

/** Check icon SVG */
function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  )
}

export function CodeBlock({
  code,
  language,
  showLineNumbers = false,
  maxHeight,
  copyable = true,
  className,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    })
  }, [code])

  const lines = code.split('\n')

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border',
        'border-border bg-white',
        'dark:border-border-subtle dark:bg-surface-raised',
        className,
      )}
    >
      {/* Header bar */}
      {(language || copyable) && (
        <div
          className={cn(
            'flex items-center justify-between px-3 py-1.5',
            'border-b border-border bg-surface-secondary',
            'dark:border-border-subtle dark:bg-surface-tertiary',
          )}
        >
          <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted dark:text-gray-500">
            {language || 'code'}
          </span>
          {copyable && (
            <button
              type="button"
              onClick={handleCopy}
              className={cn(
                'flex items-center gap-1 text-[11px] transition-colors',
                copied
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-text-muted hover:text-text-secondary dark:text-gray-500 dark:hover:text-gray-300',
              )}
              aria-label={copied ? 'Copied to clipboard' : 'Copy code'}
            >
              {copied ? (
                <>
                  <CheckIcon className="h-3 w-3" />
                  <span>Copied!</span>
                </>
              ) : (
                <>
                  <CopyIcon className="h-3 w-3" />
                  <span>Copy</span>
                </>
              )}
            </button>
          )}
        </div>
      )}

      {/* Code content */}
      <div
        className="overflow-x-auto"
        style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
      >
        <pre className="m-0 p-3">
          <code
            className={cn(
              'block text-[13px] leading-relaxed',
              'font-mono text-text-heading dark:text-gray-200',
              // Font family preference for JetBrains Mono
              '[font-family:\'JetBrains_Mono\',\'Fira_Code\',\'Consolas\',monospace]',
            )}
          >
            {showLineNumbers
              ? lines.map((line, i) => (
                  <span key={i} className="flex">
                    <span
                      className={cn(
                        'mr-4 inline-block w-8 shrink-0 select-none text-right',
                        'text-text-muted/50 dark:text-gray-600',
                      )}
                      aria-hidden="true"
                    >
                      {i + 1}
                    </span>
                    <span className="flex-1">{line || '\n'}</span>
                  </span>
                ))
              : code}
          </code>
        </pre>
      </div>
    </div>
  )
}
