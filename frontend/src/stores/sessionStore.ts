// ============================================================
// Nexus - Session Store (Zustand)
//
// Architecture (May 2026):
// - Messages loaded from DB (authoritative source)
// - Execution events loaded from /sessions/{id}/events API
// - processStore populated from events for ProcessStream rendering
// - Works identically for: live chat, new tab, reload, history view
// ============================================================

import { create } from 'zustand'
import type { ChatMessage, Session } from '../api'
import {
  listSessions,
  getSessionMessages,
  deleteSession as apiDeleteSession,
  updateSessionTitle as apiUpdateTitle,
} from '../api'
import { useExecutionStore } from './executionStore'
import { API_URL } from '../lib/constants'
import type { ExecutionEvent, ToolEvent, ThinkingEvent, AgentSpawnEvent } from '../types/execution'

/**
 * Merge DB messages with local-only messages, deduping the latter.
 *
 * DB messages are authoritative and always kept (in order). A local-only
 * message is dropped when either (a) its id matches a DB message id, or
 * (b) its (role, content) content key matches a DB message — timestamp is
 * intentionally excluded so a just-sent local echo dedupes against its own
 * DB copy even though their timestamps differ. Two genuinely distinct DB
 * messages with identical content both survive because they are matched by id.
 */
export function mergeMessages(dbMsgs: ChatMessage[], localMsgs: ChatMessage[]): ChatMessage[] {
  const dbIdSet = new Set(dbMsgs.map((m) => m.id).filter(Boolean))
  const dbContentSet = new Set(dbMsgs.map((m) => `${m.role}:${m.content.slice(0, 200)}`))
  const localOnly = localMsgs.filter((m) => {
    if (m.id && dbIdSet.has(m.id)) return false
    return !dbContentSet.has(`${m.role}:${m.content.slice(0, 200)}`)
  })
  return [...dbMsgs, ...localOnly]
}

/** Streaming state preserved across tab switches */
export interface SessionStreamingState {
  content: string
  isProcessing: boolean
  isStreaming: boolean
  statusText: string
}

export interface SessionState {
  /** List of all sessions */
  sessions: Session[]
  /** Currently active session ID */
  activeSessionId: string | null
  /** Messages per session: sessionId → messages */
  messagesBySession: Record<string, ChatMessage[]>
  /** Whether more messages exist per session (pagination) */
  hasMoreBySession: Record<string, boolean>
  /** Streaming state per session (survives tab switch) */
  streamingBySession: Record<string, SessionStreamingState>
  /** Loading state */
  loading: boolean
  /** Sessions loaded at least once */
  initialized: boolean
  /** Session ID currently loading messages */
  loadingSessionId: string | null

  // Actions
  fetchSessions: () => Promise<void>
  createNewSession: () => string
  setActiveSession: (sessionId: string) => void
  loadSessionMessages: (sessionId: string) => Promise<void>
  addMessage: (sessionId: string, message: ChatMessage) => void
  updateLastMessage: (sessionId: string, updater: (msg: ChatMessage) => ChatMessage) => void
  deleteSession: (sessionId: string) => Promise<void>
  renameSession: (sessionId: string, title: string) => Promise<void>
  updateSessionInList: (sessionId: string, updates: Partial<Session>) => void
  prependMessages: (sessionId: string, messages: ChatMessage[]) => void
  setHasMore: (sessionId: string, hasMore: boolean) => void
  loadOlderMessages: (sessionId: string) => Promise<void>
  /** Save streaming state for a session (called before tab switch) */
  saveStreamingState: (sessionId: string, state: SessionStreamingState) => void
  /** Clear streaming state for a session (called when task completes) */
  clearStreamingState: (sessionId: string) => void
  /** Get streaming state for a session */
  getStreamingState: (sessionId: string) => SessionStreamingState | undefined
}

/**
 * FIX H4: Load execution events from task_events table for a session.
 * Populates executionStore so ProcessPanel/ExecutionTrace shows history
 * when viewing past conversations (not just live events).
 * Fire-and-forget — errors are silently logged.
 */
async function _loadExecutionEventsForSession(sessionId: string): Promise<void> {
  try {
    const es = useExecutionStore.getState()
    const existing = es.eventsBySession[sessionId]
    const isActive = es.activeBySession[sessionId]

    // If session is NOT active and already has events, skip reload.
    // DB is authoritative for completed tasks — no need to refetch.
    if (existing && existing.length > 0 && !isActive) {
      return
    }

    // Dedup in executionStore handles any overlap between DB and live events.
    const AUTH_TOKEN_KEY = 'nexus_auth_token'
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    const headers: Record<string, string> = {}
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    const res = await fetch(`${API_URL}/api/v1/sessions/${sessionId}/events?after_seq=0&limit=200`, { headers })
    if (useSessionStore.getState().activeSessionId !== sessionId) return // session changed, abort
    if (!res.ok) return

    const data = await res.json()
    if (useSessionStore.getState().activeSessionId !== sessionId) return // session changed, abort
    const rawEvents: Array<{
      event_type: string
      data: Record<string, unknown>
      sequence: number
      task_id: string
    }> = data.events || []

    if (rawEvents.length === 0) return

    // Convert raw DB events to ExecutionEvent format for executionStore
    const execEvents: ExecutionEvent[] = []
    for (const raw of rawEvents) {
      const ts = Date.now() // Approximate; actual timestamps in data
      const rnd = Math.random().toString(36).slice(2, 6)
      const evtData = raw.data || {}

      switch (raw.event_type) {
        case 'tool_start':
          execEvents.push({
            id: `replay-ts-${raw.sequence}-${rnd}`,
            type: 'tool_start',
            agent_name: (evtData.agent_name as string) || 'Agent',
            tool: (evtData.tool as string) || '',
            params: evtData.params,
            timestamp: ts,
            sequence: raw.sequence,
          } as ToolEvent)
          break
        case 'tool_done':
          execEvents.push({
            id: `replay-td-${raw.sequence}-${rnd}`,
            type: 'tool_done',
            agent_name: (evtData.agent_name as string) || 'Agent',
            tool: (evtData.tool as string) || '',
            output: evtData.output,
            success: evtData.success as boolean | undefined,
            execution_time_ms: evtData.execution_time_ms as number | undefined,
            params: evtData.params,
            timestamp: ts,
            sequence: raw.sequence,
          } as ToolEvent)
          break
        case 'agent_spawn':
          execEvents.push({
            id: `replay-sp-${raw.sequence}-${rnd}`,
            type: 'agent_spawn',
            agent_name: (evtData.agent_name as string) || 'Agent',
            agent_role: (evtData.agent_role as string) || '',
            timestamp: ts,
            sequence: raw.sequence,
          } as AgentSpawnEvent)
          break
        case 'agent_thinking':
        case 'agent_response':
          execEvents.push({
            id: `replay-th-${raw.sequence}-${rnd}`,
            type: 'thinking',
            agent_name: (evtData.agent_name as string) || 'Agent',
            agent_role: (evtData.agent_role as string) || '',
            content: (evtData.content as string) || (evtData.preview as string) || '',
            timestamp: ts,
            sequence: raw.sequence,
          } as ThinkingEvent)
          break
        case 'deep_analysis_complete':
        case 'deep_analysis':
        case 'self_reflection':
        case 'auto_verify':
        case 'completion_check':
        case 'mid_analysis':
        case 'debate_critique':
        case 'debate_revision':
        case 'step':
          execEvents.push({
            id: `replay-${raw.event_type}-${raw.sequence}-${rnd}`,
            type: raw.event_type as ExecutionEvent['type'],
            data: evtData,
            timestamp: ts,
            sequence: raw.sequence,
          } as ExecutionEvent)
          break
        // REPLAY-UX: Skip ReAct Loop indicator events during history replay.
        case 'planning_start':
        case 'planning_done':
        case 'react_phase':
          // Intentionally not pushed to execEvents — skip silently
          break
        case 'debate_proposal':
        case 'debate_rebuttal':
        case 'debate_verdict':
        case 'swarm_round':
        case 'swarm_round_start':
          execEvents.push({
            id: `replay-${raw.event_type}-${raw.sequence}-${rnd}`,
            type: raw.event_type as ExecutionEvent['type'],
            data: evtData,
            timestamp: ts,
            sequence: raw.sequence,
            ...(evtData as Record<string, unknown>),
          } as ExecutionEvent)
          break
        case 'debate_started':
        case 'debate_message':
        case 'debate_concluded':
        case 'moderator_intervention':
          execEvents.push({
            id: `replay-${raw.event_type}-${raw.sequence}-${rnd}`,
            type: raw.event_type as ExecutionEvent['type'],
            timestamp: ts,
            sequence: raw.sequence,
            ...(evtData as Record<string, unknown>),
          } as unknown as ExecutionEvent)
          break
        default:
          // Persist all other event types as generic events (never skip)
          execEvents.push({
            id: `replay-${raw.event_type}-${raw.sequence}-${rnd}`,
            type: (raw.event_type || 'step') as ExecutionEvent['type'],
            data: evtData,
            timestamp: ts,
            sequence: raw.sequence,
          } as ExecutionEvent)
          break
      }
    }

    if (execEvents.length > 0) {
      // For ACTIVE sessions (task running), use batchAddEvents() to MERGE in 1 state update.
      // For completed: setEvents (DB is authoritative).
      if (isActive) {
        es.batchAddEvents(sessionId, execEvents)
      } else {
        es.setEvents(sessionId, execEvents)
      }

      //
      // ChatPanel's populateProcessStore is now the SINGLE AUTHORITY for processStore.
      // It runs via useEffect[sessionId] after messages load and handles:
      // - metadata-based replay (primary source — always available)
      // - task_events API fallback (for older sessions without metadata)
      //
      // This eliminates the debate duplicate/flicker issue on F5 reload.
    }
  } catch (err) {
    // Fire-and-forget — execution events are nice-to-have in history
    if (import.meta.env.DEV) {
      console.debug('[SessionStore] Execution events load failed:', err)
    }
  }
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messagesBySession: {},
  hasMoreBySession: {},
  streamingBySession: {},
  loading: false,
  initialized: false,
  loadingSessionId: null,

  fetchSessions: async () => {
    set({ loading: true })
    try {
      const apiSessions = await listSessions(100)

      set((state) => {
        // Merge: keep client-side sessions that have messages but aren't in API yet
        // (newly created sessions that haven't been persisted to DB)
        const apiSessionIds = new Set(apiSessions.map((s) => s.session_id))
        const localOnlySessions = state.sessions.filter(
          (s) =>
            !apiSessionIds.has(s.session_id) &&
            (state.messagesBySession[s.session_id]?.length ?? 0) > 0,
        )

        // Combine: API sessions (authoritative) + local-only sessions with messages
        const merged = [...apiSessions, ...localOnlySessions]
        // Sort by most recent first
        merged.sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        )

        return { sessions: merged, initialized: true }
      })
    } catch (err) {
      console.error('Failed to fetch sessions:', err)
      // On error, keep existing sessions instead of wiping them
      set((state) => ({
        initialized: true,
        // If we had no sessions before, keep whatever was there
        sessions: state.sessions,
      }))
    } finally {
      set({ loading: false })
    }
  },

  createNewSession: () => {
    // Reuse current session if it has no messages (avoid spam empty tabs)
    const state = get()
    const currentId = state.activeSessionId
    if (currentId) {
      const currentMessages = state.messagesBySession[currentId]
      const currentSession = state.sessions.find(s => s.session_id === currentId)
      const hasMessages = (currentMessages && currentMessages.length > 0) || (currentSession?.message_count && currentSession.message_count > 0)
      if (!hasMessages) {
        // Current session is empty — reuse it, don't create a new one
        return currentId
      }
    }

    const sessionId = crypto.randomUUID()
    const newSession: Session = {
      session_id: sessionId,
      title: 'New Chat',
      created_at: new Date().toISOString(),
      message_count: 0,
    }
    set((state) => ({
      sessions: [newSession, ...state.sessions],
      activeSessionId: sessionId,
      messagesBySession: {
        ...state.messagesBySession,
        [sessionId]: [],
      },
    }))
    // HINT-ONLY: nexus_last_session is a startup hint for a freshly-opened tab
    // that lands on /chat with no sessionId in the URL. It is NOT a source of
    // truth — when the URL carries a sessionId, the URL always wins. This key
    // is currently never read back; kept for potential "resume last session" UX.
    localStorage.setItem('nexus_last_session', sessionId)
    return sessionId
  },

  setActiveSession: (sessionId: string) => {
    set((state) => {
      // Ensure the session exists in the sessions list
      // (e.g., navigating to a URL with a session not yet fetched from API)
      const exists = state.sessions.some((s) => s.session_id === sessionId)
      const sessions = exists
        ? state.sessions
        : [
            {
              session_id: sessionId,
              title: 'Loading...',
              created_at: new Date().toISOString(),
              message_count: 0,
            },
            ...state.sessions,
          ]
      return { activeSessionId: sessionId, sessions }
    })
    // HINT-ONLY (see createNewSession): startup hint for a no-session /chat load.
    // The URL sessionId is the source of truth; this never overrides it.
    localStorage.setItem('nexus_last_session', sessionId)
    // Load messages if not already loaded
    const { messagesBySession, loadSessionMessages } = get()
    if (!messagesBySession[sessionId]) {
      loadSessionMessages(sessionId)
    } else {
      try {
        const needsRefresh = sessionStorage.getItem(`nexus_tab_needs_refresh_${sessionId}`)
        if (needsRefresh) {
          sessionStorage.removeItem(`nexus_tab_needs_refresh_${sessionId}`)
          loadSessionMessages(sessionId)
        }
      } catch { /* sessionStorage unavailable */ }
    }
  },

  loadSessionMessages: async (sessionId: string) => {
    // Prevents unbounded memory growth from messagesBySession Map.
    const MAX_CACHED_SESSIONS = 20
    const { messagesBySession: currentCache, activeSessionId } = get()
    const cachedIds = Object.keys(currentCache)
    if (cachedIds.length > MAX_CACHED_SESSIONS) {
      // Evict oldest sessions (first keys added) that aren't the active one
      const toEvict = cachedIds
        .filter((id) => id !== activeSessionId && id !== sessionId)
        .slice(0, cachedIds.length - MAX_CACHED_SESSIONS)
      if (toEvict.length > 0) {
        const cleaned = { ...currentCache }
        for (const id of toEvict) {
          delete cleaned[id]
        }
        set({ messagesBySession: cleaned })
      }
    }

    set({ loadingSessionId: sessionId })
    try {
      const messages = await getSessionMessages(sessionId)
      const chatMessages: ChatMessage[] = messages
        .filter((m) => m.role !== 'title') // Skip title markers
        .map((m) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant' | 'system',
          content: m.content,
          timestamp: m.created_at,
          // Preserve ALL metadata (model, strategy, tools_used, task_id,
          // execution_events, etc.) so badges AND process logs survive
          // F5 page reload and session history navigation.
          ...(m.metadata ? { metadata: m.metadata as ChatMessage['metadata'] } : {}),
        }))
      set((state) => {
        // This caused duplicate user + assistant messages on task completion.
        // — identified by having no matching role+content in DB results.
        const existing = state.messagesBySession[sessionId] || []
        const merged = mergeMessages(chatMessages, existing)
        return {
          messagesBySession: {
            ...state.messagesBySession,
            [sessionId]: merged,
          },
          hasMoreBySession: {
            ...state.hasMoreBySession,
            [sessionId]: chatMessages.length >= 200,
          },
          loadingSessionId: null,
        }
      })

      // This populates executionStore so ProcessPanel shows historical execution
      // traces when viewing past conversations (not just live events).
      _loadExecutionEventsForSession(sessionId)
    } catch (err) {
      // Session might be new (no messages yet) — that's fine.
      // Log for debugging but don't crash — empty messages is a valid fallback.
      if (err instanceof Error && !err.message.includes('404')) {
        console.warn('[SessionStore] loadSessionMessages failed for', sessionId, ':', err.message)
      }
      set((state) => ({
        messagesBySession: {
          ...state.messagesBySession,
          [sessionId]: state.messagesBySession[sessionId] || [],
        },
        loadingSessionId: null,
      }))
    }
  },

  addMessage: (sessionId: string, message: ChatMessage) => {
    set((state) => {
      const existing = state.messagesBySession[sessionId] || []
      // Dedup: skip if message with same id already exists
      if (message.id && existing.some((m) => m.id === message.id)) {
        return state
      }
      const updated = {
        ...state.messagesBySession,
        [sessionId]: [...existing, message],
      }

      // Update message count in session list
      const sessions = state.sessions.map((s) =>
        s.session_id === sessionId
          ? { ...s, message_count: (s.message_count || 0) + 1 }
          : s,
      )

      // Auto-title from first user message
      if (message.role === 'user' && existing.filter((m) => m.role === 'user').length === 0) {
        const title = message.content.slice(0, 80) + (message.content.length > 80 ? '...' : '')
        const updatedSessions = sessions.map((s) =>
          s.session_id === sessionId ? { ...s, title } : s,
        )
        return { messagesBySession: updated, sessions: updatedSessions }
      }

      return { messagesBySession: updated, sessions }
    })
  },

  updateLastMessage: (sessionId: string, updater: (msg: ChatMessage) => ChatMessage) => {
    set((state) => {
      const msgs = state.messagesBySession[sessionId]
      if (!msgs || msgs.length === 0) return state
      const last = msgs[msgs.length - 1]
      const updated = [...msgs.slice(0, -1), updater(last)]
      return {
        messagesBySession: {
          ...state.messagesBySession,
          [sessionId]: updated,
        },
      }
    })
  },

  deleteSession: async (sessionId: string) => {
    try {
      await apiDeleteSession(sessionId)
    } catch (err) {
      console.error('[sessionStore] deleteSession failed:', err)
      // Session might not exist in backend yet — continue with local removal
    }
    set((state) => {
      const sessions = state.sessions.filter((s) => s.session_id !== sessionId)
      const { [sessionId]: _, ...remaining } = state.messagesBySession
      const { [sessionId]: _s, ...remainingStreaming } = state.streamingBySession
      const activeSessionId =
        state.activeSessionId === sessionId
          ? sessions[0]?.session_id || null
          : state.activeSessionId
      return { sessions, messagesBySession: remaining, streamingBySession: remainingStreaming, activeSessionId }
    })
    try {
      localStorage.removeItem(`nexus_last_seq_${sessionId}`)
      const taskMapRaw = localStorage.getItem('mekongai_session_task_map')
      if (taskMapRaw) {
        const taskMap = JSON.parse(taskMapRaw)
        if (taskMap && typeof taskMap === 'object') {
          delete taskMap[sessionId]
          localStorage.setItem('mekongai_session_task_map', JSON.stringify(taskMap))
        }
      }
      const stageMapRaw = localStorage.getItem('mekongai_session_stage_map')
      if (stageMapRaw) {
        const stageMap = JSON.parse(stageMapRaw)
        if (stageMap && typeof stageMap === 'object') {
          delete stageMap[sessionId]
          localStorage.setItem('mekongai_session_stage_map', JSON.stringify(stageMap))
        }
      }
    } catch { /* best-effort cleanup */ }
  },

  renameSession: async (sessionId: string, title: string) => {
    // Save old title for revert on failure
    const oldTitle = get().sessions.find((s) => s.session_id === sessionId)?.title || ''
    // Optimistic update
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, title } : s,
      ),
    }))
    try {
      await apiUpdateTitle(sessionId, title)
    } catch (err) {
      // Revert optimistic update
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.session_id === sessionId ? { ...s, title: oldTitle } : s,
        ),
      }))
      console.error('[sessionStore] renameSession failed:', err)
    }
  },

  updateSessionInList: (sessionId: string, updates: Partial<Session>) => {
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, ...updates } : s,
      ),
    }))
  },

  prependMessages: (sessionId: string, newMessages: ChatMessage[]) => {
    set((state) => {
      const existing = state.messagesBySession[sessionId] || []
      return {
        messagesBySession: {
          ...state.messagesBySession,
          [sessionId]: [...newMessages, ...existing],
        },
      }
    })
  },

  setHasMore: (sessionId: string, hasMore: boolean) => {
    set((state) => ({
      hasMoreBySession: { ...state.hasMoreBySession, [sessionId]: hasMore },
    }))
  },

  loadOlderMessages: async (sessionId: string) => {
    const { messagesBySession } = get()
    const existing = messagesBySession[sessionId] || []
    // Optimistic client-side messages (added before DB persist) have short IDs
    // like "msg-xxx" while DB messages have UUID format (36 chars with dashes).
    const dbMessageCount = existing.filter(m =>
      m.id && m.id.length >= 32 // UUIDs are 36 chars (with dashes) or 32 (without)
    ).length
    const offset = dbMessageCount
    try {
      const older = await getSessionMessages(sessionId, offset, 200)
      const chatMessages: ChatMessage[] = older
        .filter((m) => m.role !== 'title')
        .map((m) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant' | 'system',
          content: m.content,
          timestamp: m.created_at,
          ...(m.metadata ? { metadata: m.metadata as ChatMessage['metadata'] } : {}),
        }))

      if (chatMessages.length === 0) {
        set((state) => ({
          hasMoreBySession: { ...state.hasMoreBySession, [sessionId]: false },
        }))
        return
      }

      // Prepend older messages BEFORE existing ones (chronological order)
      set((state) => ({
        messagesBySession: {
          ...state.messagesBySession,
          [sessionId]: [...chatMessages, ...(state.messagesBySession[sessionId] || [])],
        },
        hasMoreBySession: {
          ...state.hasMoreBySession,
          [sessionId]: chatMessages.length >= 200,
        },
      }))
    } catch {
      set((state) => ({
        hasMoreBySession: { ...state.hasMoreBySession, [sessionId]: false },
      }))
    }
  },

  saveStreamingState: (sessionId: string, streamState: SessionStreamingState) => {
    set((state) => ({
      streamingBySession: {
        ...state.streamingBySession,
        [sessionId]: streamState,
      },
    }))
  },

  clearStreamingState: (sessionId: string) => {
    set((state) => {
      const { [sessionId]: _, ...rest } = state.streamingBySession
      return { streamingBySession: rest }
    })
  },

  getStreamingState: (sessionId: string) => {
    return get().streamingBySession[sessionId]
  },
}))
