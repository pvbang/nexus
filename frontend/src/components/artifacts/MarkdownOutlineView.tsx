import { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Minus } from 'lucide-react'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { cn } from '../../lib/cn'

function AnimatedCollapse({ open, children }: { open: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number | 'auto'>(open ? 'auto' : 0)

  useEffect(() => {
    if (!ref.current) return
    if (open) {
      const h = ref.current.scrollHeight
      setHeight(h)
      const timer = setTimeout(() => setHeight('auto'), 200)
      return () => clearTimeout(timer)
    } else {
      setHeight(ref.current.scrollHeight)
      requestAnimationFrame(() => setHeight(0))
    }
  }, [open])

  return (
    <div
      ref={ref}
      style={{ height: height === 'auto' ? 'auto' : `${height}px` }}
      className={cn(
        'overflow-hidden transition-[height] duration-200 ease-out',
        !open && 'pointer-events-none',
      )}
    >
      {children}
    </div>
  )
}

interface OutlineSection {
  id: string
  level: number // 1 = ##, 2 = ###, 0 = --- separator
  title: string
  content: string
  children: OutlineSection[]
}

function parseMarkdownOutline(content: string): OutlineSection[] {
  const lines = content.split('\n')
  const rootSections: OutlineSection[] = []
  const stack: OutlineSection[] = []
  let currentContent: string[] = []
  let sectionCounter = 0

  const flushContent = () => {
    const text = currentContent.join('\n').trim()
    if (stack.length > 0) {
      stack[stack.length - 1].content = text
    } else if (text && rootSections.length === 0) {
      rootSections.push({
        id: `section-${sectionCounter++}`,
        level: 0,
        title: 'Introduction',
        content: text,
        children: [],
      })
    } else if (text && rootSections.length > 0) {
      const last = rootSections[rootSections.length - 1]
      last.content = last.content ? last.content + '\n\n' + text : text
    }
    currentContent = []
  }

  let inFence = false
  let fenceLevel = 0

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Track code fence state to avoid parsing headings inside code blocks
    const fenceMatch = line.match(/^(`{3,})/)
    if (fenceMatch) {
      const ticks = fenceMatch[1].length
      if (!inFence) {
        inFence = true
        fenceLevel = ticks
      } else if (ticks >= fenceLevel && line.trim() === fenceMatch[1]) {
        inFence = false
        fenceLevel = 0
      }
      currentContent.push(line)
      continue
    }

    if (inFence) {
      currentContent.push(line)
      continue
    }

    const h2Match = line.match(/^##\s+(.+)/)
    const h3Match = line.match(/^###\s+(.+)/)
    const isSeparator = /^---+\s*$/.test(line) && i > 0

    if (h2Match) {
      flushContent()
      while (stack.length > 0) {
        stack.pop()
      }
      const section: OutlineSection = {
        id: `section-${sectionCounter++}`,
        level: 1,
        title: h2Match[1].trim(),
        content: '',
        children: [],
      }
      rootSections.push(section)
      stack.push(section)
    } else if (h3Match) {
      flushContent()
      while (stack.length > 1) {
        stack.pop()
      }
      const section: OutlineSection = {
        id: `section-${sectionCounter++}`,
        level: 2,
        title: h3Match[1].trim(),
        content: '',
        children: [],
      }
      if (stack.length > 0) {
        stack[stack.length - 1].children.push(section)
      } else {
        rootSections.push(section)
      }
      stack.push(section)
    } else if (isSeparator) {
      flushContent()
      while (stack.length > 0) {
        stack.pop()
      }
      const nextNonEmpty = lines.slice(i + 1).find(l => l.trim())
      const title = nextNonEmpty?.match(/^#{1,3}\s+/) ? '' : 'Section'
      if (title) {
        rootSections.push({
          id: `section-${sectionCounter++}`,
          level: 0,
          title,
          content: '',
          children: [],
        })
      }
    } else {
      currentContent.push(line)
    }
  }
  flushContent()

  return rootSections
}

interface SectionNodeProps {
  section: OutlineSection
  expanded: Set<string>
  onToggle: (id: string) => void
  depth?: number
}

function SectionNode({ section, expanded, onToggle, depth = 0 }: SectionNodeProps) {
  const isExpanded = expanded.has(section.id)
  const hasContent = section.content.trim().length > 0 || section.children.length > 0

  return (
    <div className={cn('group/section', depth > 0 && 'ml-3')}>
      <button
        onClick={() => onToggle(section.id)}
        className={cn(
          'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left transition-all duration-200',
          'hover:bg-gradient-to-r hover:from-brand-50/80 hover:to-transparent dark:hover:from-brand-900/20 dark:hover:to-transparent',
          isExpanded && 'bg-gradient-to-r from-brand-50/50 to-transparent dark:from-brand-900/10 dark:to-transparent',
        )}
      >
        <span className="shrink-0 w-5 h-5 flex items-center justify-center rounded-md bg-gray-100 dark:bg-[#1a1a2e] transition-colors group-hover/section:bg-brand-100 dark:group-hover/section:bg-brand-900/30">
          {section.level === 0 ? (
            <Minus className="w-3 h-3 text-gray-400 dark:text-gray-500" />
          ) : isExpanded ? (
            <ChevronDown className="w-3 h-3 text-brand-500" />
          ) : (
            <ChevronRight className="w-3 h-3 text-gray-400 dark:text-gray-500" />
          )}
        </span>

        <span className={cn(
          'flex-1 truncate font-medium transition-colors',
          section.level === 1 && 'text-[13px] text-text-dark dark:text-[#e4e4ed]',
          section.level === 2 && 'text-xs text-text-secondary dark:text-[#a0a0b8]',
          section.level === 0 && 'text-xs text-text-muted dark:text-[#6b6b85] italic',
        )}>
          {section.title}
        </span>

        {section.children.length > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-[#1a1a2e] text-text-muted dark:text-[#6b6b85] font-medium tabular-nums">
            {section.children.length}
          </span>
        )}
      </button>

      <AnimatedCollapse open={isExpanded && hasContent}>
        <div className="ml-5 pl-3 border-l-2 border-brand-200/50 dark:border-brand-800/30">
          {isExpanded && section.content && (
            <div className="py-2 px-2 text-sm prose prose-sm dark:prose-invert max-w-none [&_pre]:text-xs [&_p]:text-[13px] [&_li]:text-[13px]">
              <MarkdownRenderer content={section.content} />
            </div>
          )}
          {section.children.map(child => (
            <SectionNode
              key={child.id}
              section={child}
              expanded={expanded}
              onToggle={onToggle}
              depth={depth + 1}
            />
          ))}
        </div>
      </AnimatedCollapse>
    </div>
  )
}

interface MarkdownOutlineViewProps {
  content: string
}

export function MarkdownOutlineView({ content }: MarkdownOutlineViewProps) {
  const sections = useMemo(() => parseMarkdownOutline(content), [content])
  const allIds = useMemo(() => {
    const ids: string[] = []
    const collect = (s: OutlineSection) => {
      ids.push(s.id)
      s.children.forEach(collect)
    }
    sections.forEach(collect)
    return ids
  }, [sections])

  // For large content (>200KB), start collapsed to avoid perf issues
  const isLarge = content.length > 200 * 1024
  const [expanded, setExpanded] = useState<Set<string>>(() => isLarge ? new Set() : new Set(allIds))

  const onToggle = useCallback((id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const expandAll = useCallback(() => setExpanded(new Set(allIds)), [allIds])
  const collapseAll = useCallback(() => setExpanded(new Set()), [])

  if (sections.length === 0) {
    return (
      <div className="p-6">
        <MarkdownRenderer content={content} className="max-w-none" />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-light/50 dark:border-[#2a2a45]/50 bg-gray-50/50 dark:bg-[#0a0a14]/50">
        <span className="text-[11px] font-medium text-text-muted dark:text-[#6b6b85] uppercase tracking-wider">
          {sections.length} sections
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={expandAll}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium text-text-secondary dark:text-[#a0a0b8] hover:bg-gray-100 dark:hover:bg-[#1a1a2e] transition-colors"
            title="Expand All"
          >
            <ChevronsUpDown className="w-3 h-3" />
            Expand
          </button>
          <button
            onClick={collapseAll}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium text-text-secondary dark:text-[#a0a0b8] hover:bg-gray-100 dark:hover:bg-[#1a1a2e] transition-colors"
            title="Collapse All"
          >
            <ChevronsDownUp className="w-3 h-3" />
            Collapse
          </button>
        </div>
      </div>

      {/* Sections */}
      <div className="flex-1 overflow-y-auto p-3 space-y-0.5">
        {sections.map(section => (
          <SectionNode
            key={section.id}
            section={section}
            expanded={expanded}
            onToggle={onToggle}
          />
        ))}
      </div>
    </div>
  )
}
