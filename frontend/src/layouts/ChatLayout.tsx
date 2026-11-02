// ============================================================
// Nexus - Chat Layout (Simplified)
// Sidebar + TabBar + Chat (no right-side activity panel)
// FIX-6/7: Removed HermesActivityPanel and UnifiedProcessPanel.
// Process/execution events are shown inline in the chat via ProcessStream.
// ============================================================

import { useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ConversationSidebar } from '../components/chat/ConversationSidebar'
import { ChatPanel } from '../components/chat/ChatPanel'
// import { TabBar } from '../components/chat/TabBar'  // Hidden — sidebar is sufficient
import { BuildProgress, ProjectFileBrowser } from '../components/project'
import { useSessionStore } from '../stores/sessionStore'
import { useProcessStore } from '../stores/processStore'
import { useProjectStore } from '../stores/projectStore'
import { useTabStore } from '../stores/tabStore'

export function ChatLayout() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const activeSessionId = useSessionStore((s) => s.activeSessionId)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const fetchSessions = useSessionStore((s) => s.fetchSessions)
  const setActiveSession = useSessionStore((s) => s.setActiveSession)
  const initialized = useSessionStore((s) => s.initialized)

  // Tab store
  const addTab = useTabStore((s) => s.addTab)

  // Project builder state
  const isBuilding = useProjectStore((s) => s.isBuilding)
  const projectPhases = useProjectStore((s) => s.phases)
  const showProjectPanel = isBuilding || projectPhases.some((p) => p.status !== 'pending')

  // Process store — only used for bottom bar indicator
  const processStage = useProcessStore((s) => s.stage)
  const hasActiveProcess = processStage !== 'idle'

  // Always fetch sessions when entering chat layout
  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  // Handle URL session routing
  useEffect(() => {
    if (!initialized) return

    if (sessionId) {
      setActiveSession(sessionId)
      // Set processStore.currentSessionId EARLY (before ChatPanel mounts)
      // so Monitor WS tab indicators route correctly.
      useProcessStore.getState().setSession(sessionId)
      // Ensure tab exists for this session
      const existingTab = useTabStore.getState().getTabBySessionId(sessionId)
      if (!existingTab) {
        const sessions = useSessionStore.getState().sessions
        const session = sessions.find(s => s.session_id === sessionId)
        addTab(sessionId, session?.title || 'Chat')
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, initialized])

  // TAB-TITLE-SYNC: Update tab titles when sessions are fetched from backend
  const sessions = useSessionStore((s) => s.sessions)
  useEffect(() => {
    if (!sessions.length) return
    try {
      const tabState = useTabStore.getState()
      for (const tab of tabState.tabs) {
        const session = sessions.find(s => s.session_id === tab.sessionId)
        if (!session?.title || session.title === tab.title) continue
        const isDefaultTitle = tab.title === 'New Chat' || tab.title === 'Loading...'
        const isBackendGenerated = session.title !== 'New Chat' && session.title.length > 0
        if (isDefaultTitle || isBackendGenerated) {
          tabState.updateTabTitle(tab.id, session.title)
        }
      }
    } catch (err) {
      console.warn('[ChatLayout] Tab title sync error (non-fatal):', err)
    }
  }, [sessions])

  // Handle no-session case: /chat → reuse empty session or create new
  useEffect(() => {
    if (!initialized || sessionId) return

    const { sessions, messagesBySession } = useSessionStore.getState()
    const lastSession = sessions[0]
    if (lastSession) {
      const msgs = messagesBySession[lastSession.session_id]
      const hasMessages = (msgs && msgs.length > 0) || (lastSession.message_count > 0)
      if (!hasMessages) {
        navigate(`/chat/${lastSession.session_id}`, { replace: true })
        return
      }
    }

    const newId = createNewSession()
    navigate(`/chat/${newId}`, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialized])

  const currentSessionId = sessionId || activeSessionId


  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Tab Bar - hidden (sidebar is sufficient for session navigation) */}
      {/* <TabBar /> */}

      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        {/* Left: Conversation Sidebar */}
        <ConversationSidebar />

        {/* Center: Chat Panel (full width — no right sidebar) */}
        <div
          className={`flex-1 flex flex-col min-w-0 h-full overflow-hidden transition-[padding] duration-300 ${hasActiveProcess ? 'pb-12' : ''}`}
        >
          {currentSessionId ? (
            <ChatPanel key={currentSessionId} sessionId={currentSessionId} />
          ) : (
            <div className="flex items-center justify-center h-full text-text-muted">
              <p className="text-[14px]">Select or create a conversation to start</p>
            </div>
          )}
        </div>

        {/* Right: Project Builder Panel (only when building) */}
        {showProjectPanel && (
          <div className="hidden lg:block w-72 shrink-0 h-full overflow-y-auto border-l border-zinc-200 dark:border-zinc-700/50 bg-zinc-50 dark:bg-zinc-900/50 p-3 space-y-3">
            <BuildProgress />
            <ProjectFileBrowser />
          </div>
        )}
      </div>
    </div>
  )
}
