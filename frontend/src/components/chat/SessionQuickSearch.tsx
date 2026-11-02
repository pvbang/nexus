// ============================================================
// Nexus - Session Quick Search (Ctrl+K Overlay)
// Full-screen search overlay with fuzzy search, keyboard navigation,
// message preview, and portal rendering.
// ============================================================

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Search, FileText, MessageSquare, X, Clock } from 'lucide-react'
import { useSessionStore } from '../../stores/sessionStore'
import { searchSessions } from '../../api'
import type { Session, SessionSearchResult } from '../../api'
import { cn } from '../../lib/cn'
import { formatRelativeTime } from '../../lib/utils'

// ── Types ──────────────────────────────────────────────────

interface QuickSearchItem {
  id: string
  sessionId: string
  title: string
  subtitle: string
  snippet?: string
  relevanceScore?: number
  messageCount?: number
  time: string
  category: 'recent' | 'search'
}

type Category = 'recent' | 'search'

// ── Constants ──────────────────────────────────────────────

const DEBOUNCE_MS = 300
const MAX_RECENT = 10
const MAX_VISIBLE_ITEMS = 50

// ── Helpers ────────────────────────────────────────────────

/** Simple substring-based local fuzzy filter on session titles */
function filterSessionsLocally(sessions: Session[], query: string): Session[] {
  const lower = query.toLowerCase()
  return sessions.filter((s) => s.title.toLowerCase().includes(lower))
}

/** Convert Session to QuickSearchItem */
function sessionToItem(s: Session): QuickSearchItem {
  return {
    id: `recent-${s.session_id}`,
    sessionId: s.session_id,
    title: s.title || 'Untitled',
    subtitle: `${s.message_count} msgs`,
    messageCount: s.message_count,
    time: formatRelativeTime(s.created_at),
    category: 'recent',
  }
}

/** Convert SessionSearchResult to QuickSearchItem */
function searchResultToItem(r: SessionSearchResult, sessions: Session[]): QuickSearchItem {
  const session = sessions.find((s) => s.session_id === r.session_id)
  return {
    id: `search-${r.session_id}-${r.timestamp}`,
    sessionId: r.session_id,
    title: session?.title || `Session ${r.session_id.slice(0, 8)}`,
    subtitle: r.role === 'user' ? 'You' : 'Assistant',
    snippet: r.content_snippet,
    relevanceScore: r.relevance_score,
    messageCount: session?.message_count,
    time: formatRelativeTime(r.timestamp),
    category: 'search',
  }
}

/** Highlight matching substring in text */
function HighlightMatch({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>

  const lower = text.toLowerCase()
  const qLower = query.toLowerCase()
  const idx = lower.indexOf(qLower)

  if (idx === -1) return <>{text}</>

  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-yellow-200/60 dark:bg-yellow-500/30 text-inherit rounded-sm px-0.5">
        {text.slice(idx, idx + query.length)}
      </mark>
      {text.slice(idx + query.length)}
    </>
  )
}

// ── Simple Virtualized List ────────────────────────────────

const ITEM_HEIGHT = 64
const CATEGORY_HEADER_HEIGHT = 36

interface VirtualizedListProps {
  items: QuickSearchItem[]
  categories: Category[]
  selectedIndex: number
  query: string
  onSelectItem: (item: QuickSearchItem) => void
  onHoverItem: (index: number) => void
  listRef: React.RefObject<HTMLDivElement | null>
}

function VirtualizedList({
  items,
  categories,
  selectedIndex,
  query,
  onSelectItem,
  onHoverItem,
  listRef,
}: VirtualizedListProps) {
  // Group items by category
  const grouped = useMemo(() => {
    const map: Record<Category, QuickSearchItem[]> = { recent: [], search: [] }
    for (const item of items) {
      map[item.category].push(item)
    }
    return map
  }, [items])

  // Build flat render list with category headers
  const renderList: Array<{ type: 'header'; label: string; category: Category } | { type: 'item'; item: QuickSearchItem; flatIndex: number }> = []
  let flatIdx = 0
  for (const cat of categories) {
    const catItems = grouped[cat]
    if (catItems.length === 0) continue

    const label = cat === 'recent' ? 'Recent Sessions' : 'Search Results'
    renderList.push({ type: 'header', label, category: cat })

    for (const item of catItems) {
      renderList.push({ type: 'item', item, flatIndex: flatIdx })
      flatIdx++
    }
  }

  // Simple windowing: only render visible items if > MAX_VISIBLE_ITEMS
  const shouldVirtualize = items.length > MAX_VISIBLE_ITEMS
  const visibleItems = shouldVirtualize ? renderList.slice(0, MAX_VISIBLE_ITEMS + categories.length) : renderList

  return (
    <div
      ref={listRef}
      className="max-h-[400px] overflow-y-auto scroll-smooth"
      role="listbox"
      aria-label="Search results"
    >
      {visibleItems.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-text-muted dark:text-[#6b6b85]">
          <Search className="w-8 h-8 mb-3 opacity-40" />
          <p className="text-sm">No sessions found</p>
          <p className="text-xs mt-1 opacity-70">Try a different search term</p>
        </div>
      )}

      {visibleItems.map((entry) => {
        if (entry.type === 'header') {
          return (
            <div
              key={`header-${entry.category}`}
              className="px-3 pt-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-muted dark:text-[#6b6b85] select-none"
              style={{ height: CATEGORY_HEADER_HEIGHT }}
            >
              {entry.label}
            </div>
          )
        }

        const { item, flatIndex } = entry
        const isSelected = flatIndex === selectedIndex

        return (
          <button
            key={item.id}
            type="button"
            role="option"
            aria-selected={isSelected}
            onClick={() => onSelectItem(item)}
            onMouseEnter={() => onHoverItem(flatIndex)}
            className={cn(
              'w-full flex items-start gap-3 px-3 py-2.5 text-left transition-colors duration-100',
              'rounded-comfortable mx-1',
              'focus:outline-none',
              isSelected
                ? 'bg-[#eff6ff] dark:bg-blue-900/25 ring-1 ring-[#3b82f6]/30'
                : 'hover:bg-[#f4f5f7] dark:hover:bg-[#1a1a2e]',
            )}
            style={{ minHeight: ITEM_HEIGHT }}
          >
            {/* Icon */}
            <div className={cn(
              'mt-0.5 w-8 h-8 rounded-[8px] flex items-center justify-center shrink-0',
              item.category === 'search'
                ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
                : 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400',
            )}>
              {item.category === 'search'
                ? <FileText className="w-4 h-4" />
                : <MessageSquare className="w-4 h-4" />
              }
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={cn(
                  'text-[13px] font-medium truncate',
                  isSelected
                    ? 'text-[#1d4ed8] dark:text-blue-300'
                    : 'text-text-dark dark:text-[#e4e4ed]',
                )}>
                  <HighlightMatch text={item.title} query={query} />
                </span>
                {item.relevanceScore !== undefined && item.relevanceScore > 0 && (
                  <div className="shrink-0 flex items-center gap-1">
                    <div className="h-1.5 w-8 rounded-full bg-[#e5e7eb] dark:bg-[#2a2a45] overflow-hidden">
                      <div
                        className="h-full rounded-full bg-[#3b82f6]"
                        style={{ width: `${Math.round(item.relevanceScore * 100)}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {item.snippet && (
                <p className="text-[12px] text-text-secondary dark:text-[#a0a0b8] truncate mt-0.5 leading-relaxed">
                  &ldquo;<HighlightMatch text={item.snippet} query={query} />&rdquo;
                </p>
              )}

              <div className="flex items-center gap-2 mt-1">
                {item.messageCount !== undefined && (
                  <span className="text-[11px] text-text-muted dark:text-[#6b6b85]">
                    {item.messageCount} msgs
                  </span>
                )}
                <span className="text-[11px] text-text-muted dark:text-[#6b6b85] flex items-center gap-0.5">
                  <Clock className="w-3 h-3" />
                  {item.time}
                </span>
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ── Preview Panel ──────────────────────────────────────────

interface PreviewPanelProps {
  sessionId: string | null
  sessions: Session[]
}

function PreviewPanel({ sessionId, sessions }: PreviewPanelProps) {
  const messagesBySession = useSessionStore((s) => s.messagesBySession)
  const session = sessions.find((s) => s.session_id === sessionId)
  const messages = sessionId ? messagesBySession[sessionId] : undefined

  if (!sessionId || !session) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted dark:text-[#6b6b85]">
        <div className="text-center">
          <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-40" />
          <p className="text-sm">Select a session to preview</p>
        </div>
      </div>
    )
  }

  const previewMessages = messages?.slice(-6) ?? []

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#e5e7eb] dark:border-[#2a2a45]">
        <h3 className="text-[13px] font-semibold text-text-dark dark:text-[#e4e4ed] truncate">
          {session.title}
        </h3>
        <p className="text-[11px] text-text-muted dark:text-[#6b6b85] mt-0.5">
          {session.message_count} messages · {formatRelativeTime(session.created_at)}
        </p>
      </div>

      {/* Messages preview */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2.5">
        {previewMessages.length === 0 ? (
          <p className="text-[12px] text-text-muted dark:text-[#6b6b85] italic">
            No messages cached. Open session to load.
          </p>
        ) : (
          previewMessages.map((msg) => (
            <div
              key={msg.id}
              className={cn(
                'rounded-[8px] px-3 py-2 text-[12px] leading-relaxed',
                msg.role === 'user'
                  ? 'bg-[#eff6ff] dark:bg-blue-900/20 text-[#1d4ed8] dark:text-blue-300 ml-4'
                  : 'bg-[#f4f5f7] dark:bg-[#1a1a2e] text-text-secondary dark:text-[#a0a0b8] mr-4',
              )}
            >
              <span className="font-medium text-[11px] uppercase tracking-wider opacity-60 block mb-0.5">
                {msg.role}
              </span>
              <span className="line-clamp-3">
                {msg.content.slice(0, 200)}
                {msg.content.length > 200 ? '...' : ''}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ── Skeleton Loading ───────────────────────────────────────

function SearchSkeleton() {
  return (
    <div className="space-y-1 px-1 py-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-start gap-3 px-3 py-2.5 animate-pulse">
          <div className="w-8 h-8 rounded-[8px] bg-[#e5e7eb] dark:bg-[#2a2a45] shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-3.5 bg-[#e5e7eb] dark:bg-[#2a2a45] rounded w-3/4" />
            <div className="h-3 bg-[#e5e7eb] dark:bg-[#2a2a45] rounded w-1/2" />
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────

export function SessionQuickSearch() {
  const navigate = useNavigate()
  const sessions = useSessionStore((s) => s.sessions)
  const setActiveSession = useSessionStore((s) => s.setActiveSession)

  // ── State ──
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [, setActiveCategory] = useState<Category>('recent')

  // ── Refs ──
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const backdropRef = useRef<HTMLDivElement>(null)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Open/Close via nexus:focus-search event ──
  useEffect(() => {
    const handleOpen = () => {
      setIsOpen((prev) => !prev)
    }

    window.addEventListener('nexus:focus-search', handleOpen)
    return () => window.removeEventListener('nexus:focus-search', handleOpen)
  }, [])

  // ── Auto-focus input on open ──
  useEffect(() => {
    if (isOpen) {
      // Small delay to ensure DOM is ready after portal render
      const timer = setTimeout(() => inputRef.current?.focus(), 50)
      // Lock body scroll
      const original = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      return () => {
        clearTimeout(timer)
        document.body.style.overflow = original
      }
    }
  }, [isOpen])

  // ── Reset state on close ──
  const closeOverlay = useCallback(() => {
    setIsOpen(false)
    setQuery('')
    setSelectedIndex(0)
    setSearchResults([])
    setIsSearching(false)
    setActiveCategory('recent')
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
  }, [])

  // ── Build items list ──
  const recentItems: QuickSearchItem[] = useMemo(() => {
    const filtered = query.trim()
      ? filterSessionsLocally(sessions, query)
      : sessions
    return filtered.slice(0, MAX_RECENT).map(sessionToItem)
  }, [sessions, query])

  const searchItems: QuickSearchItem[] = useMemo(() => {
    return searchResults.map((r) => searchResultToItem(r, sessions))
  }, [searchResults, sessions])

  const allItems: QuickSearchItem[] = useMemo(() => {
    const categories: Category[] = query.trim()
      ? ['search', 'recent']
      : ['recent']
    const result: QuickSearchItem[] = []
    for (const cat of categories) {
      if (cat === 'recent') result.push(...recentItems)
      if (cat === 'search') result.push(...searchItems)
    }
    return result
  }, [recentItems, searchItems, query])

  const visibleCategories: Category[] = useMemo(() => {
    const cats: Category[] = []
    if (query.trim() && searchItems.length > 0) cats.push('search')
    if (recentItems.length > 0) cats.push('recent')
    return cats
  }, [query, searchItems, recentItems])

  // ── Debounced API search ──
  useEffect(() => {
    if (!query.trim()) {
      setSearchResults([])
      setIsSearching(false)
      return
    }

    setIsSearching(true)

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }

    debounceTimerRef.current = setTimeout(async () => {
      try {
        const response = await searchSessions(query)
        setSearchResults(response.results)
      } catch {
        // API error — keep local results
        setSearchResults([])
      } finally {
        setIsSearching(false)
      }
    }, DEBOUNCE_MS)

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
    }
  }, [query])

  // ── Navigate to selected session ──
  const openSession = useCallback((item: QuickSearchItem) => {
    setActiveSession(item.sessionId)
    navigate(`/chat/${item.sessionId}`)
    closeOverlay()
  }, [setActiveSession, navigate, closeOverlay])

  // ── Selected item for preview ──
  const selectedItem = allItems[selectedIndex] ?? null

  // ── Keyboard navigation ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'Escape':
        e.preventDefault()
        closeOverlay()
        break

      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex((prev) => Math.min(prev + 1, allItems.length - 1))
        break

      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex((prev) => Math.max(prev - 1, 0))
        break

      case 'Enter':
        e.preventDefault()
        if (selectedItem) {
          openSession(selectedItem)
        }
        break

      case 'Tab': {
        e.preventDefault()
        // Cycle between categories
        if (visibleCategories.length <= 1) return
        setActiveCategory((prev) => {
          const currentIdx = visibleCategories.indexOf(prev)
          const nextIdx = (currentIdx + 1) % visibleCategories.length
          const nextCat = visibleCategories[nextIdx]

          // Move selection to first item of next category
          const firstItemIdx = allItems.findIndex((item) => item.category === nextCat)
          if (firstItemIdx >= 0) {
            setSelectedIndex(firstItemIdx)
          }

          return nextCat
        })
        break
      }
    }
  }, [closeOverlay, allItems, selectedItem, openSession, visibleCategories])

  // ── Scroll selected item into view ──
  useEffect(() => {
    if (!listRef.current) return
    const container = listRef.current
    const selected = container.querySelector('[aria-selected="true"]')
    if (selected) {
      selected.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [selectedIndex])

  // ── Reset selection when items change ──
  useEffect(() => {
    setSelectedIndex(0)
  }, [query, searchResults])

  // ── Click backdrop to close ──
  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === backdropRef.current) {
      closeOverlay()
    }
  }, [closeOverlay])

  // ── Detect desktop for preview panel ──
  const [isDesktop, setIsDesktop] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    setIsDesktop(mq.matches)

    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // ── Don't render if closed ──
  if (!isOpen) return null

  const overlay = (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[10vh] px-4 bg-black/50 backdrop-blur-sm animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="Quick search sessions"
    >
      {/* Search container */}
      <div
        className={cn(
          'w-full bg-white dark:bg-[#1a1a2e] rounded-hero shadow-2xl',
          'border border-[#e5e7eb] dark:border-[#2a2a45]',
          'animate-scale-in overflow-hidden',
          'flex flex-col',
          isDesktop ? 'max-w-[900px]' : 'max-w-[640px]',
        )}
        onKeyDown={handleKeyDown}
        style={{ maxHeight: '70vh' }}
      >
        {/* ── Search Input ── */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[#e5e7eb] dark:border-[#2a2a45]">
          <Search className="w-5 h-5 text-[#3b82f6] shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sessions, messages..."
            className={cn(
              'flex-1 bg-transparent border-none outline-none',
              'text-[18px] font-medium',
              'text-text-dark dark:text-[#e4e4ed]',
              'placeholder:text-[#8e8e93] dark:placeholder:text-[#6b6b85]',
            )}
            autoComplete="off"
            spellCheck={false}
            aria-label="Search sessions"
            aria-controls="quick-search-results"
          />

          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="p-1 rounded-[6px] hover:bg-[#f4f5f7] dark:hover:bg-[#232340] transition-colors"
              aria-label="Clear search"
            >
              <X className="w-4 h-4 text-text-muted dark:text-[#6b6b85]" />
            </button>
          )}

          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-2 py-1 text-[11px] font-mono font-medium text-text-muted dark:text-[#6b6b85] bg-[#f4f5f7] dark:bg-[#232340] rounded-[6px] border border-[#e5e7eb] dark:border-[#2a2a45]">
            Esc
          </kbd>
        </div>

        {/* ── Content Area ── */}
        <div
          id="quick-search-results"
          className={cn('flex-1 min-h-0 flex', isDesktop ? 'flex-row' : 'flex-col')}
        >
          {/* Results list */}
          <div className={cn(
            'flex-1 min-w-0',
            isDesktop && 'border-r border-[#e5e7eb] dark:border-[#2a2a45]',
          )}>
            {isSearching && query.trim() && recentItems.length === 0 ? (
              <SearchSkeleton />
            ) : (
              <>
                {isSearching && query.trim() && (
                  <div className="px-4 pt-2">
                    <div className="inline-flex items-center gap-2 text-[11px] text-text-muted dark:text-[#6b6b85]">
                      <div className="w-3 h-3 border-2 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
                      Searching...
                    </div>
                  </div>
                )}
                <VirtualizedList
                  items={allItems}
                  categories={visibleCategories}
                  selectedIndex={selectedIndex}
                  query={query}
                  onSelectItem={openSession}
                  onHoverItem={setSelectedIndex}
                  listRef={listRef}
                />
              </>
            )}
          </div>

          {/* Preview panel (desktop only) */}
          {isDesktop && (
            <div className="w-[320px] shrink-0">
              <PreviewPanel
                sessionId={selectedItem?.sessionId ?? null}
                sessions={sessions}
              />
            </div>
          )}
        </div>

        {/* ── Footer with keyboard hints ── */}
        <div className="flex items-center gap-4 px-4 py-2.5 border-t border-[#e5e7eb] dark:border-[#2a2a45] bg-[#f9fafb] dark:bg-[#0f0f17]/50">
          <FooterHint keys={['↑', '↓']} label="Navigate" />
          <FooterHint keys={['↵']} label="Open" />
          <FooterHint keys={['⎋']} label="Close" />
          {visibleCategories.length > 1 && (
            <FooterHint keys={['Tab']} label="Switch category" />
          )}
          <FooterHint keys={['⌘K']} label="Toggle" />
        </div>
      </div>
    </div>
  )

  return createPortal(overlay, document.body)
}

// ── Footer Hint ────────────────────────────────────────────

function FooterHint({ keys, label }: { keys: string[]; label: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-text-muted dark:text-[#6b6b85]">
      {keys.map((k) => (
        <kbd
          key={k}
          className="inline-flex items-center justify-center min-w-[20px] h-5 px-1 font-mono text-[10px] bg-white dark:bg-[#232340] border border-[#e5e7eb] dark:border-[#2a2a45] rounded-[4px] shadow-sm"
        >
          {k}
        </kbd>
      ))}
      <span>{label}</span>
    </div>
  )
}
