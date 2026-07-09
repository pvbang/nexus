// ============================================================
// MekongAI Nexus - MarkdownRenderer UI Primitive
// Shared markdown rendering with react-markdown, remarkGfm, rehypeHighlight
// Extracted from ChatPanel.tsx for reuse across chat, tasks, artifacts, etc.
// ============================================================

import { useState, useCallback, useRef } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import { cn } from '../../lib/cn'
import { useArtifactPreviewStore } from '../../stores/artifactPreviewStore'

export interface MarkdownRendererProps {
  /** Markdown content string */
  content: string
  /** Additional class names */
  className?: string
  /** Transform image src URLs (e.g. resolve relative paths to API stream URLs) */
  transformImageUri?: (src: string) => string
  /**
   * Treat single newlines as hard line breaks (remark-breaks). OFF by default so
   * document/artifact rendering keeps standard Markdown semantics (a single `\n`
   * is a soft wrap). Turn ON for chat/reasoning text, where models write one
   * thought per line without the trailing-double-space Markdown requires.
   */
  breaks?: boolean
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

/**
 * Build the react-markdown component overrides.
 * Uses a ref + forceRender pattern for copy button state (same as ChatPanel).
 */
function useMdComponents(transformImageUri?: (src: string) => string): Components {
  const copiedBlockRef = useRef<string | null>(null)
  const [, forceRender] = useState(0)

  const copyCode = useCallback((code: string) => {
    navigator.clipboard.writeText(code).then(() => {
      copiedBlockRef.current = code
      forceRender((n) => n + 1)
      setTimeout(() => {
        copiedBlockRef.current = null
        forceRender((n) => n + 1)
      }, 2000)
    })
  }, [])

  const components: Components = {
    // ---- Code (inline + blocks) ----
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || '')
      const codeStr = String(children).replace(/\n$/, '')

      // Inline code: no language class, single line
      if (!match && !codeStr.includes('\n')) {
        // Detect file references (e.g. security_audit.md, report.py, data.csv)
        const filePattern = /^[\w\-./]+\.(md|txt|py|js|ts|json|csv|yaml|yml|html|css|sh|bash|xml|sql|docx|xlsx|pdf)$/i
        const isFileRef = filePattern.test(codeStr.trim())

        if (isFileRef) {
          return (
            <code
              className={cn(
                'px-1.5 py-0.5 rounded-[5px] text-[13px] font-mono cursor-pointer transition-all',
                'bg-blue-50 text-blue-700 hover:bg-blue-100 hover:text-blue-900',
                'dark:bg-blue-900/20 dark:text-blue-300 dark:hover:bg-blue-900/40 dark:hover:text-blue-200',
                'border border-blue-200/50 dark:border-blue-700/50',
                'hover:border-blue-300 dark:hover:border-blue-600',
              )}
              onClick={() => {
                useArtifactPreviewStore.getState().requestPreview(codeStr.trim())
              }}
              title={`📄 Click to preview: ${codeStr.trim()}`}
              role="button"
              {...props}
            >
              📄 {children}
            </code>
          )
        }

        return (
          <code
            className={cn(
              'px-1.5 py-0.5 rounded-[5px] text-[13px] font-mono',
              'bg-[#1a1a2e]/8 text-[#d63384]',
              'dark:bg-[#d63384]/10 dark:text-[#f0a0c8]',
            )}
            {...props}
          >
            {children}
          </code>
        )
      }

      // Code block
      return (
        <div
          className={cn(
            'relative group my-3 rounded-[10px] overflow-hidden border',
            'border-[#e5e7eb] dark:border-[#2a2a45]',
          )}
        >
          {/* Header with language + copy */}
          <div
            className={cn(
              'flex items-center justify-between px-3 py-1.5 border-b',
              'bg-[#f8f9fa] border-[#e5e7eb]',
              'dark:bg-[#151525] dark:border-[#2a2a45]',
            )}
          >
            <span className="text-[11px] font-mono uppercase tracking-wider text-[#8e8e93] dark:text-gray-500">
              {match?.[1] || 'code'}
            </span>
            <button
              type="button"
              onClick={() => copyCode(codeStr)}
              className={cn(
                'flex items-center gap-1 text-[11px] transition-colors',
                copiedBlockRef.current === codeStr
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-[#8e8e93] hover:text-[#45515e] dark:text-gray-500 dark:hover:text-gray-300',
              )}
              aria-label={copiedBlockRef.current === codeStr ? 'Copied' : 'Copy code'}
            >
              {copiedBlockRef.current === codeStr ? (
                <>
                  <CheckIcon className="w-3 h-3" />
                  <span>Copied</span>
                </>
              ) : (
                <>
                  <CopyIcon className="w-3 h-3" />
                  <span>Copy</span>
                </>
              )}
            </button>
          </div>
          <pre className="!m-0 !rounded-none overflow-x-auto">
            <code className={className} {...props}>
              {children}
            </code>
          </pre>
        </div>
      )
    },

    // ---- Headings ----
    h1: ({ children }) => (
      <h1 className="text-[20px] font-bold mt-4 mb-2 border-b pb-1 text-[#222] border-[#e5e7eb] dark:text-gray-100 dark:border-[#2a2a45]">
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 className="text-[17px] font-bold mt-3.5 mb-1.5 text-[#222] dark:text-gray-100">
        {children}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 className="text-[15px] font-semibold mt-3 mb-1 text-[#222] dark:text-gray-100">
        {children}
      </h3>
    ),
    h4: ({ children }) => (
      <h4 className="text-[14px] font-semibold mt-2.5 mb-1 text-[#222] dark:text-gray-100">
        {children}
      </h4>
    ),

    // ---- Block elements ----
    p: ({ children }) => (
      <p className="mb-2 last:mb-0 leading-[1.7]">{children}</p>
    ),
    ul: ({ children }) => (
      <ul className="list-disc pl-5 mb-2 space-y-0.5">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="list-decimal pl-5 mb-2 space-y-0.5">{children}</ol>
    ),
    li: ({ children }) => (
      <li className="leading-[1.6]">{children}</li>
    ),

    // ---- Links ----
    a: ({ href, children }) => {
      const API_BASE = import.meta.env.VITE_API_URL || ''
      let resolvedHref = href || ''
      const isArtifactLink = resolvedHref.startsWith('/api/v1/artifacts/')

      if (isArtifactLink) {
        resolvedHref = `${API_BASE}${resolvedHref}`
      }

      // Artifact download links get special styling
      if (isArtifactLink && resolvedHref.includes('/download/')) {
        return (
          <a
            href={resolvedHref}
            download
            className={cn(
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium no-underline transition-colors border',
              'bg-blue-50 hover:bg-blue-100 text-blue-700 border-blue-200',
              'dark:bg-blue-900/30 dark:hover:bg-blue-900/50 dark:text-blue-300 dark:border-blue-800',
            )}
            title="Download file"
          >
            <span>📥</span> {children}
          </a>
        )
      }

      return (
        <a
          href={resolvedHref}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            'underline transition-colors',
            'text-[#2563eb] decoration-[#2563eb]/30 hover:text-[#1d4ed8] hover:decoration-[#1d4ed8]',
            'dark:text-[#60a5fa] dark:decoration-[#60a5fa]/30 dark:hover:text-[#93bbfd] dark:hover:decoration-[#93bbfd]',
          )}
        >
          {children}
        </a>
      )
    },

    // ---- Blockquote ----
    blockquote: ({ children }) => (
      <blockquote
        className={cn(
          'border-l-[3px] px-3 py-2 my-2 rounded-r-[6px] text-[13px] italic',
          'border-[#3b82f6] bg-[#eff6ff] text-[#1e40af]',
          'dark:border-[#60a5fa] dark:bg-[#1e3a5f]/30 dark:text-[#93bbfd]',
        )}
      >
        {children}
      </blockquote>
    ),

    // ---- Table ----
    table: ({ children }) => (
      <div
        className={cn(
          'overflow-x-auto my-3 rounded-[8px] border',
          'border-[#e5e7eb] dark:border-[#2a2a45]',
        )}
      >
        <table className="w-full text-[13px] border-collapse">{children}</table>
      </div>
    ),
    thead: ({ children }) => (
      <thead className="bg-[#f8f9fa] dark:bg-[#151525]">{children}</thead>
    ),
    th: ({ children }) => (
      <th
        className={cn(
          'px-3 py-2 text-left font-semibold border-b',
          'text-[#222] border-[#e5e7eb]',
          'dark:text-gray-200 dark:border-[#2a2a45]',
        )}
      >
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td
        className={cn(
          'px-3 py-2 border-b',
          'border-[#f0f1f3] dark:border-[#2a2a45]/50',
        )}
      >
        {children}
      </td>
    ),

    // ---- Horizontal rule ----
    hr: () => (
      <hr className="border-t my-3 border-[#e5e7eb] dark:border-[#2a2a45]" />
    ),

    // ---- Image ----
    img: ({ src, alt }) => {
      const resolvedSrc = src && transformImageUri ? transformImageUri(src) : src
      return (
        <span className="block my-3">
          <img
            src={resolvedSrc}
            alt={alt || ''}
            className={cn(
              'max-w-full rounded-[8px] border shadow-sm',
              'border-[#e5e7eb] dark:border-[#2a2a45]',
            )}
            loading="lazy"
          />
          {alt && (
            <span className="block text-[11px] mt-1 text-center italic text-[#8e8e93] dark:text-gray-500">
              {alt}
            </span>
          )}
        </span>
      )
    },

    // ---- Inline styles ----
    strong: ({ children }) => (
      <strong className="font-semibold text-[#222] dark:text-gray-100">{children}</strong>
    ),
    em: ({ children }) => <em className="italic">{children}</em>,
  }

  return components
}

/**
 * Strip raw LLM tool/function call XML tags from content.
 * These tags leak from agent responses and should not be rendered to users.
 */
const TOOL_CALL_PATTERNS = [
  /<tool_call>[\s\S]*?<\/tool_call>/gi,
  /<tool_result>[\s\S]*?<\/tool_result>/gi,
  /<function_call>[\s\S]*?<\/function_call>/gi,
  /<function_result>[\s\S]*?<\/function_result>/gi,
  /<tool_use>[\s\S]*?<\/tool_use>/gi,
  /<invoke>[\s\S]*?<\/invoke>/gi,
  // LLM-leaked XML: <function>...</function>, <thinking>...</thinking>
  /<function>\s*\{[\s\S]*?\}\s*<\/function>/gi,
  /<thinking>[\s\S]*?<\/thinking>/gi,
  // Inline <function> tags without closing (partial leaks): <function> {"name":...} </function>
  /<function>[^<]*<\/function>/gi,
  // Stray self-contained function call blocks (e.g. agent chain-of-thought tool calls)
  /(?:<function>\s*\{[^}]*\}\s*<\/function>\s*)+/gi,
]

export function stripToolCallTags(text: string): string {
  let cleaned = text
  for (const pattern of TOOL_CALL_PATTERNS) {
    cleaned = cleaned.replace(pattern, '')
  }
  // Pipe-wrapped chat-template tokens (DeepSeek/Qwen-style): the model serializes
  // a tool call starting at `<|…|>` (ASCII bar) or `<｜…｜>` (fullwidth bar U+FF5C),
  // e.g. `<|tool_call|>`, `<｜tool▁calls▁begin｜>`, `<｜DSML｜function_calls`. These
  // are never valid prose, so reasoning ends at the first one — truncate there to
  // hide the whole serialized tail. The backend strips this too; this is a
  // defense-in-depth net that also cleans event history persisted before the fix.
  const pipeToken = cleaned.search(/<[|｜]/)
  if (pipeToken !== -1) {
    cleaned = cleaned.slice(0, pipeToken)
  }
  // Remove excessive blank lines left after stripping
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n')
  return cleaned.trim()
}

export function MarkdownRenderer({ content, className, transformImageUri, breaks }: MarkdownRendererProps) {
  const components = useMdComponents(transformImageUri)
  const cleanContent = stripToolCallTags(content)
  const remarkPlugins = breaks ? [remarkGfm, remarkBreaks] : [remarkGfm]

  return (
    <div
      className={cn(
        'doc-theme prose-nexus text-sm leading-relaxed',
        'text-[#222] dark:text-gray-300',
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {cleanContent}
      </ReactMarkdown>
    </div>
  )
}
