// ============================================================
// MekongAI Nexus - Conversation Sidebar
// Batch 7: Added SessionInsights context menu option
// Phase 6: Added ExportDialog integration
// ============================================================

import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Plus, Search, MessageSquare, Trash2, Pencil, X, Check,
  ChevronLeft, MoreHorizontal, BarChart3, Download, Loader2,
} from 'lucide-react'
import { useSessionStore } from '../../stores/sessionStore'
import { useProcessStore } from '../../stores/processStore'
import { usePreferenceStore } from '../../stores/preferenceStore'
import { searchSessions, getSessionMessages, getSessionTimeline } from '../../api'
import type { SessionMessage, ActionEntry } from '../../api'
import { SessionInsights } from '../insights'
import { ExportDialog } from '../export/ExportDialog'

interface GroupedSessions {
  today: typeof sessions
  yesterday: typeof sessions
  thisWeek: typeof sessions
  older: typeof sessions
}

const sessions: ReturnType<typeof useSessionStore.getState>['sessions'] = []

function groupSessionsByDate(
  items: typeof sessions,
): GroupedSessions {
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterdayStart = new Date(todayStart.getTime() - 86400000)
  const weekStart = new Date(todayStart.getTime() - 6 * 86400000)

  const groups: GroupedSessions = { today: [], yesterday: [], thisWeek: [], older: [] }

  for (const s of items) {
    const d = new Date(s.created_at)
    if (d >= todayStart) groups.today.push(s)
    else if (d >= yesterdayStart) groups.yesterday.push(s)
    else if (d >= weekStart) groups.thisWeek.push(s)
    else groups.older.push(s)
  }

  return groups
}

export function ConversationSidebar() {
  const navigate = useNavigate()
  const { sessionId: activeId } = useParams()
  const processStage = useProcessStore((s) => s.stage)
  const sessions = useSessionStore((s) => s.sessions)
  const fetchSessions = useSessionStore((s) => s.fetchSessions)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const setActiveSession = useSessionStore((s) => s.setActiveSession)
  const deleteSession = useSessionStore((s) => s.deleteSession)
  const renameSession = useSessionStore((s) => s.renameSession)
  const initialized = useSessionStore((s) => s.initialized)
  const collapsed = usePreferenceStore((s) => s.sidebarCollapsed)
  const toggleSidebar = usePreferenceStore((s) => s.toggleSidebar)

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<string[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [insightsSessionId, setInsightsSessionId] = useState<string | null>(null)
  const [exportSessionId, setExportSessionId] = useState<string | null>(null)
  const [exportMessages, setExportMessages] = useState<SessionMessage[]>([])
  const [exportEvents, setExportEvents] = useState<ActionEntry[]>([])
  const [exportLoading, setExportLoading] = useState(false)
  const editInputRef = useRef<HTMLInputElement>(null)
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load sessions on mount
  useEffect(() => {
    if (!initialized) {
      fetchSessions()
    }
  }, [initialized, fetchSessions])

  // Listen for focus-search event from App keyboard shortcuts (Ctrl+K)
  useEffect(() => {
    const handler = () => {
      // Expand sidebar if collapsed
      const { sidebarCollapsed, toggleSidebar: toggle } = usePreferenceStore.getState()
      if (sidebarCollapsed) {
        toggle()
      }
      // Focus search input after a short delay (allow sidebar to expand)
      const searchInput = document.querySelector<HTMLInputElement>('[data-search-input]')
      if (searchInput) {
        setTimeout(() => searchInput.focus(), 100)
      }
    }
    window.addEventListener('nexus:focus-search', handler)
    return () => window.removeEventListener('nexus:focus-search', handler)
  }, [])

  // Set active session from URL
  useEffect(() => {
    if (activeId) {
      setActiveSession(activeId)
    }
  }, [activeId, setActiveSession])

  // Search with debounce
  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      setIsSearching(false)
      return
    }

    setIsSearching(true)
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)

    searchTimeoutRef.current = setTimeout(async () => {
      try {
        const result = await searchSessions(searchQuery, 20)
        const sessionIds = [...new Set(result.results.map((r) => r.session_id))]
        setSearchResults(sessionIds)
      } catch {
        setSearchResults([])
      } finally {
        setIsSearching(false)
      }
    }, 300)

    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)
    }
  }, [searchQuery])

  const handleNewChat = useCallback(() => {
    const id = createNewSession()
    navigate(`/chat/${id}`)
  }, [createNewSession, navigate])

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      navigate(`/chat/${sessionId}`)
      setMenuOpenId(null)
    },
    [navigate],
  )

  const handleDelete = useCallback(
    async (sessionId: string) => {
      await deleteSession(sessionId)
      setMenuOpenId(null)
      if (activeId === sessionId) {
        const remaining = sessions.filter((s) => s.session_id !== sessionId)
        if (remaining.length > 0) {
          navigate(`/chat/${remaining[0].session_id}`)
        } else {
          navigate('/chat')
        }
      }
    },
    [deleteSession, activeId, sessions, navigate],
  )

  const handleStartRename = useCallback(
    (sessionId: string, currentTitle: string) => {
      setEditingId(sessionId)
      setEditTitle(currentTitle)
      setMenuOpenId(null)
      setTimeout(() => editInputRef.current?.focus(), 50)
    },
    [],
  )

  const handleFinishRename = useCallback(async () => {
    if (editingId && editTitle.trim()) {
      await renameSession(editingId, editTitle.trim())
    }
    setEditingId(null)
    setEditTitle('')
  }, [editingId, editTitle, renameSession])

  const handleExport = useCallback(async (sessionId: string) => {
    setExportLoading(true)
    setMenuOpenId(null)
    try {
      const [msgs, events] = await Promise.all([
        getSessionMessages(sessionId),
        getSessionTimeline(sessionId).catch(() => [] as ActionEntry[]),
      ])
      setExportMessages(msgs)
      setExportEvents(events)
      setExportSessionId(sessionId)
    } catch {
      // Silent fail — don't open dialog
    } finally {
      setExportLoading(false)
    }
  }, [])

  // Filter sessions by search
  const displaySessions =
    searchQuery.trim() && searchResults.length > 0
      ? sessions.filter((s) => searchResults.includes(s.session_id))
      : sessions

  const groups = groupSessionsByDate(displaySessions)

  // Collapsed mode
  if (collapsed) {
    return (
      <div className="hidden sm:flex w-[52px] border-r border-border-light bg-white flex-col items-center py-3 gap-2 shrink-0">
        <button
          onClick={toggleSidebar}
          className="w-9 h-9 rounded-[10px] hover:bg-[#f4f5f7] flex items-center justify-center transition-colors"
          title="Expand sidebar"
        >
          <MessageSquare className="w-[18px] h-[18px] text-text-secondary" />
        </button>
        <button
          onClick={handleNewChat}
          className="w-9 h-9 rounded-[10px] bg-[#1a1a2e] text-white flex items-center justify-center hover:bg-[#2a2a3e] transition-colors"
          title="New chat (Ctrl+N)"
        >
          <Plus className="w-[18px] h-[18px]" />
        </button>
      </div>
    )
  }

  const renderSessionItem = (s: typeof sessions[0]) => {
    const isActive = s.session_id === activeId
    const isEditing = editingId === s.session_id
    const isActiveProcessing = isActive && processStage !== 'idle' && processStage !== 'completed' && processStage !== 'failed'

    return (
      <div
        key={s.session_id}
        className={`group relative flex items-center gap-2 px-2.5 py-2 rounded-[10px] cursor-pointer transition-all duration-150 ${
          isActive
            ? 'bg-[#eff6ff] text-[#1d4ed8]'
            : 'text-text-secondary hover:bg-[#f4f5f7] hover:text-text-dark'
        }`}
        onClick={() => !isEditing && handleSelectSession(s.session_id)}
      >
        {/* UI-04: Show spinner when active session is processing */}
        {isActiveProcessing ? (
          <Loader2 className="w-[15px] h-[15px] shrink-0 animate-spin text-blue-500" />
        ) : (
          <MessageSquare
            className={`w-[15px] h-[15px] shrink-0 ${isActive ? 'text-[#3b82f6]' : 'text-text-muted'}`}
            strokeWidth={isActive ? 2 : 1.5}
          />
        )}

        {isEditing ? (
          <div className="flex-1 flex items-center gap-1">
            <input
              ref={editInputRef}
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleFinishRename()
                if (e.key === 'Escape') { setEditingId(null); setEditTitle('') }
              }}
              className="flex-1 text-[13px] bg-white border border-[#3b82f6] rounded-[6px] px-1.5 py-0.5 outline-none"
              onClick={(e) => e.stopPropagation()}
            />
            <button
              onClick={(e) => { e.stopPropagation(); handleFinishRename() }}
              className="w-5 h-5 flex items-center justify-center text-emerald-600 hover:bg-emerald-50 rounded"
            >
              <Check className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <>
            <div className="flex-1 min-w-0">
              <p className={`text-[13px] font-medium truncate ${isActive ? 'text-[#1d4ed8]' : ''}`}>
                {s.title}
              </p>
              {s.message_count > 0 && (
                <p className="text-[10px] text-text-muted mt-0.5">
                  {s.message_count} messages
                </p>
              )}
            </div>

            {/* Context menu button */}
            <button
              onClick={(e) => {
                e.stopPropagation()
                setMenuOpenId(menuOpenId === s.session_id ? null : s.session_id)
              }}
              className={`shrink-0 w-6 h-6 rounded-[6px] flex items-center justify-center transition-opacity ${
                isActive || menuOpenId === s.session_id
                  ? 'opacity-100'
                  : 'opacity-0 group-hover:opacity-100'
              } hover:bg-black/5`}
            >
              <MoreHorizontal className="w-3.5 h-3.5" />
            </button>
          </>
        )}

        {/* Context menu dropdown */}
        {menuOpenId === s.session_id && !isEditing && (
          <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-[#e5e7eb] rounded-[10px] shadow-elevated py-1 min-w-[140px]">
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleStartRename(s.session_id, s.title)
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-text-secondary hover:bg-[#f4f5f7] hover:text-text-dark transition-colors"
            >
              <Pencil className="w-3.5 h-3.5" />
              Rename
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setInsightsSessionId(s.session_id)
                setMenuOpenId(null)
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-text-secondary hover:bg-[#f4f5f7] hover:text-text-dark transition-colors"
            >
              <BarChart3 className="w-3.5 h-3.5" />
              Insights
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleExport(s.session_id)
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-text-secondary hover:bg-[#f4f5f7] hover:text-text-dark transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              {exportLoading ? 'Loading…' : 'Export'}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleDelete(s.session_id)
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-red-600 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Delete
            </button>
          </div>
        )}
      </div>
    )
  }

  const renderGroup = (label: string, items: typeof sessions) => {
    if (items.length === 0) return null
    return (
      <div className="mb-3">
        <p className="text-[10px] font-semibold text-text-muted uppercase tracking-wider px-2.5 mb-1">
          {label}
        </p>
        <div className="space-y-0.5">{items.map(renderSessionItem)}</div>
      </div>
    )
  }

  return (
    <div className={`
      w-[280px] border-r border-border-light bg-white dark:bg-[#0f0f17] dark:border-[#2a2a45] flex flex-col shrink-0
      fixed sm:relative inset-y-0 left-0 z-30
      sm:translate-x-0
      transition-transform duration-200
    `}>
      {/* Mobile backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-[-1] sm:hidden"
        onClick={toggleSidebar}
      />
      {/* Header */}
      <div className="px-3 py-3 border-b border-border-light">
        <div className="flex items-center gap-2 mb-2.5">
          <button
            onClick={toggleSidebar}
            className="w-8 h-8 rounded-[8px] hover:bg-[#f4f5f7] flex items-center justify-center transition-colors"
            title="Collapse sidebar (Ctrl+/)"
          >
            <ChevronLeft className="w-4 h-4 text-text-secondary" />
          </button>
          <span className="text-[14px] font-semibold text-text-dark flex-1">Chats</span>
          <button
            onClick={handleNewChat}
            className="w-8 h-8 rounded-[8px] bg-[#1a1a2e] text-white flex items-center justify-center hover:bg-[#2a2a3e] active:scale-[0.97] transition-all"
            title="New chat (Ctrl+N)"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            type="text"
            data-search-input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chats... (Ctrl+K)"
            className="w-full pl-8 pr-8 py-1.5 text-[12px] border border-[#e5e7eb] rounded-[8px] bg-[#fafbfc] focus:outline-none focus:border-[#3b82f6] focus:ring-[2px] focus:ring-[#3b82f6]/10 transition-all"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 flex items-center justify-center"
            >
              <X className="w-3 h-3 text-text-muted" />
            </button>
          )}
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {/* Close menu on outside click */}
        {menuOpenId && (
          <div className="fixed inset-0 z-40" onClick={() => setMenuOpenId(null)} />
        )}

        {isSearching && (
          <div className="flex items-center justify-center py-8">
            <svg className="w-5 h-5 animate-spin text-[#3b82f6]" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" opacity="0.2" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </div>
        )}

        {!isSearching && displaySessions.length === 0 && (
          <div className="text-center py-12 animate-fade-in">
            <MessageSquare className="w-8 h-8 text-text-muted mx-auto mb-2" strokeWidth={1.2} />
            <p className="text-[13px] text-text-secondary font-medium">
              {searchQuery ? 'No results found' : 'No conversations yet'}
            </p>
            {!searchQuery && (
              <button
                onClick={handleNewChat}
                className="mt-3 text-[12px] text-[#3b82f6] hover:text-[#1d4ed8] font-medium transition-colors"
              >
                Start a new chat →
              </button>
            )}
          </div>
        )}

        {!isSearching && displaySessions.length > 0 && (
          <>
            {searchQuery ? (
              // Flat list for search results
              <div className="space-y-0.5">{displaySessions.map(renderSessionItem)}</div>
            ) : (
              // Grouped by date
              <>
                {renderGroup('Today', groups.today)}
                {renderGroup('Yesterday', groups.yesterday)}
                {renderGroup('This Week', groups.thisWeek)}
                {renderGroup('Older', groups.older)}
              </>
            )}
          </>
        )}
      </div>

      {/* Session Insights Modal */}
      {insightsSessionId && (
        <SessionInsights
          sessionId={insightsSessionId}
          onClose={() => setInsightsSessionId(null)}
        />
      )}

      {/* Export Dialog */}
      {exportSessionId && (
        <ExportDialog
          open
          onClose={() => {
            setExportSessionId(null)
            setExportMessages([])
            setExportEvents([])
          }}
          sessionId={exportSessionId}
          sessionTitle={
            sessions.find((s) => s.session_id === exportSessionId)?.title ?? 'Untitled'
          }
          messages={exportMessages}
          events={exportEvents}
        />
      )}
    </div>
  )
}
