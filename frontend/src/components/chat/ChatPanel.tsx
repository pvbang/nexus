// ============================================================
// Nexus - Chat Panel (Refactored with Sub-components)
// ARCHITECTURE: DB-first polling, no SSE/WS dependency.
// User sends message → POST /chat/send (fire-and-forget) → poll events from DB
// Orchestrates: polling events, state, scroll, execution trace
// ============================================================

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  Sparkles, Paperclip, Loader2, ExternalLink,
  ChevronUp, ChevronDown,
} from 'lucide-react'
import {
  uploadAttachments, apiGet, apiPost, getSessionMessages,
  sendChatMessage,
  type ChatMessage, type UploadResult,
  type PendingDecision,
} from '../../api'
// ARCHITECTURE: DB-first polling — SSE streaming removed, using POST + polling pattern
import { useSessionStore } from '../../stores/sessionStore'
import { useTabStore } from '../../stores/tabStore'
import { useNotificationStore } from '../../stores/notificationStore'
import { useExecutionStore } from '../../stores/executionStore'
import { useProcessStore, persistTaskMapping } from '../../stores/processStore'
import {
  type ExecutionEvent, type SwarmRoundEvent,
} from '../../types/execution'
import { cn } from '../../lib/cn'
// P0-FIX-2: Chat WS for multi-tab delivery, F5 recovery, event replay
import { useResilientChat, type ChatWsMessage } from '../../hooks/useResilientChat'
import { makeWsDoorbellHandler } from './wsDoorbell'
// ARCHITECTURE: DB-first polling, no SSE/WS dependency for event delivery
import { useTaskPolling, type TaskEvent } from '../../hooks/useTaskPolling'

// Sub-components (type-only imports for state declarations)
import { type AttachedFile } from './MessageInput'
import { type LiveDebateState } from './LiveDebateStream'
import { type StepInfo, type AgentActivity } from './ProcessingIndicator'

// PERF: Extracted sub-components with React.memo for render isolation
import { ChatMessageList } from './ChatMessageList'
import { ChatProcessingStatus } from './ChatProcessingStatus'
import { ChatStreamingContent } from './ChatStreamingContent'
import { ChatInputArea } from './ChatInputArea'

// ── Constants ─────────────────────────────────────────────────

const EMPTY_MESSAGES: ChatMessage[] = []

const EXAMPLE_PROMPTS = [
  { text: 'Phân tích đối thủ cạnh tranh', icon: '📊' },
  { text: 'Viết Python script xử lý CSV', icon: '🐍' },
  { text: 'Nghiên cứu xu hướng AI 2026', icon: '🔬' },
  { text: 'Debug lỗi CORS trong FastAPI', icon: '🐛' },
]

// ── Props ─────────────────────────────────────────────────────

interface ChatPanelProps {
  sessionId: string
}

// ── Component ─────────────────────────────────────────────────

export function ChatPanel({ sessionId }: ChatPanelProps) {
  // === Session store ===
  // PERF: Select only this session's messages to avoid re-render when other sessions update
  const rawMessages = useSessionStore((s) => s.messagesBySession[sessionId])
  const messages = useMemo(() => rawMessages ?? EMPTY_MESSAGES, [rawMessages])
  const addMessage = useSessionStore((s) => s.addMessage)
  const hasMore = useSessionStore((s) => s.hasMoreBySession[sessionId] ?? false)
  const setHasMore = useSessionStore((s) => s.setHasMore)
  const prependMessages = useSessionStore((s) => s.prependMessages)
  const saveStreamingState = useSessionStore((s) => s.saveStreamingState)
  const clearStreamingState = useSessionStore((s) => s.clearStreamingState)

  // === Local state ===
  const [input, setInput] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [statusText, setStatusText] = useState('')
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([])
  const [progressSteps, setProgressSteps] = useState<StepInfo[]>([])
  const [agentActivities, setAgentActivities] = useState<AgentActivity[]>([])
  const [streamingContent, setStreamingContent] = useState('')
  const [inlineDebateRounds, setInlineDebateRounds] = useState<SwarmRoundEvent[]>([])
  const [liveDebate, setLiveDebate] = useState<LiveDebateState | null>(null)
  const [pendingDecision, setPendingDecision] = useState<PendingDecision | null>(null)
  const [selectedModel, setSelectedModel] = useState<string>('')
  const [canRetry, setCanRetry] = useState(false) // UI-03: retry on error
  const [canResume, setCanResume] = useState(false)
  const [resumeInfo, setResumeInfo] = useState<{ turn: number; taskId: string } | null>(null)
  // ARCHITECTURE: DB-first polling — task ID for active polling (null = no active task)
  const [pollingTaskId, setPollingTaskId] = useState<string | null>(null)

  // === Refs ===
  const inlineDebateRoundsRef = useRef<SwarmRoundEvent[]>([])
  const processingStartTimeRef = useRef<number>(0)
  const toolsUsedRef = useRef<Set<string>>(new Set())
  // FIX-POLL-LEAK: Guard ref to prevent re-spawning background poll after intentional kill.
  // Set to true by auto-reset timeout or 404 handler. Cleared when a new task starts.
  const pollKilledRef = useRef(false)
  const lastSentMessageRef = useRef<string>('') // UI-03: store last sent message for retry
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const userScrolledUpRef = useRef(false)
  const scrollRafRef = useRef<number>(0)
  // Scroll button visibility state
  const [showScrollTop, setShowScrollTop] = useState(false)
  const [showScrollBottom, setShowScrollBottom] = useState(false)
  // Scroll progress (0-1) for progress ring on scroll-to-bottom button
  const [scrollProgress, setScrollProgress] = useState(0)
  // Unread message count (when user scrolled up and new messages arrive)
  const [unreadCount, setUnreadCount] = useState(0)
  // Ref to capture latest streamingContent for onDone closure (avoids stale closure crash)
  const streamingContentRef = useRef('')
  // Ref to share mapSseToExecEvent across useEffects (defined in history load, used by polling)
  const mapSseToExecEventRef = useRef<
    ((e: Record<string, unknown>, idx: number, sourceId: string, baseTimestamp?: number) => ExecutionEvent) | null
  >(null)
  // Ref to share populateProcessStore across useEffects (defined in history load, used by polling + tab switch)
  const populateProcessStoreRef = useRef<
    ((events: Array<Record<string, unknown>>, taskId: string, markCompleted?: boolean, baseTimestamp?: number) => void) | null
  >(null)
  // Track previous sessionId for saving streaming state on tab switch
  const prevSessionIdRef = useRef<string>(sessionId)

  // === PERF: Throttled state updaters for streaming events ===
  // Avoids per-token re-renders (streaming emits 1 event per token/chunk)
  const streamingBufferRef = useRef('')
  const streamingFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const statusTextTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const statusTextPendingRef = useRef<string>('')
  const progressStepsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const progressStepsPendingRef = useRef<StepInfo | null>(null)

  /** Throttled streaming content update — flushes at most every 50ms */
  const appendStreamingContent = useCallback((content: string) => {
    streamingBufferRef.current += content
    if (streamingFlushTimerRef.current === null) {
      streamingFlushTimerRef.current = setTimeout(() => {
        streamingFlushTimerRef.current = null
        const buffered = streamingBufferRef.current
        streamingBufferRef.current = ''
        if (buffered) {
          setStreamingContent(prev => prev + buffered)
          setIsStreaming(true)
        }
      }, 50)
    }
  }, [])

  /** Throttled status text update — at most every 200ms */
  const throttledSetStatusText = useCallback((text: string) => {
    statusTextPendingRef.current = text
    if (statusTextTimerRef.current === null) {
      statusTextTimerRef.current = setTimeout(() => {
        statusTextTimerRef.current = null
        setStatusText(statusTextPendingRef.current)
      }, 200)
    }
  }, [])

  /** Throttled progress steps update — at most every 500ms */
  const throttledSetProgressStep = useCallback((step: StepInfo) => {
    progressStepsPendingRef.current = step
    if (progressStepsTimerRef.current === null) {
      progressStepsTimerRef.current = setTimeout(() => {
        progressStepsTimerRef.current = null
        const pending = progressStepsPendingRef.current
        if (pending) {
          setProgressSteps(prev => {
            const idx = prev.findIndex(s => s.step === pending.step)
            if (idx >= 0) { const u = [...prev]; u[idx] = pending; return u }
            return [...prev, pending]
          })
          progressStepsPendingRef.current = null
        }
      }, 500)
    }
  }, [])

  // Cleanup throttle timers on unmount
  useEffect(() => {
    return () => {
      if (streamingFlushTimerRef.current) clearTimeout(streamingFlushTimerRef.current)
      if (statusTextTimerRef.current) clearTimeout(statusTextTimerRef.current)
      if (progressStepsTimerRef.current) clearTimeout(progressStepsTimerRef.current)
    }
  }, [])

  // === Notification store ===
  const addNotification = useNotificationStore((s) => s.add)

  // === Execution trace store ===
  const addExecutionEvent = useExecutionStore((s) => s.addEvent)
  const setExecutionEvents = useExecutionStore((s) => s.setEvents)
  const clearExecutionSession = useExecutionStore((s) => s.clearSession)
  const setExecutionActive = useExecutionStore((s) => s.setActive)
  const executionActive = useExecutionStore((s) => !!s.activeBySession[sessionId])

  // Process stage active: task is running on backend (for ProcessStream visibility)
  const processStageActive = useProcessStore((s) =>
    s.stage !== 'idle' && s.stage !== 'completed' && s.stage !== 'failed' &&
    (s.currentSessionId === sessionId || s.sessionId === sessionId)
  )

  // === Chat WS = DOORBELL ONLY ===
  // ARCHITECTURE: DB polling (useTaskPolling) is the SOLE source of execution
  // events. The Chat WS (useResilientChat) no longer pushes events into any
  // store — it only wakes polling so the UI fetches authoritative events from
  // the DB. This eliminates the WS/polling race that produced duplicate and
  // flickering events. See wsDoorbell.ts.
  //
  // pollNow is created later (by useTaskPolling, below), so the doorbell calls
  // it through a ref to avoid the hook-ordering constraint.
  const pollNowRef = useRef<() => void>(() => {})

  const handleWsChatEvent = useMemo(
    () =>
      makeWsDoorbellHandler({
        sessionId,
        onWake: () => pollNowRef.current(),
        onTaskStarted: (taskId) => {
          if (taskId && !taskId.startsWith('msg-')) {
            setPollingTaskId(taskId)
            setExecutionActive(sessionId, true)
            useProcessStore.getState().startOrResumeProcess(taskId, sessionId)
          }
        },
        onTerminalHint: () => pollNowRef.current(),
      }) as (msg: ChatWsMessage) => void,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId],
  )

  const { taskRunning: wsTaskRunning } = useResilientChat({
    sessionId,
    onEvent: handleWsChatEvent,
  })

  // ── ARCHITECTURE: DB-first polling for task events ───────────────
  // Primary event delivery channel. Replaces SSE streaming.
  // Events are fetched from DB every 2s and routed to UI stores.
  const handlePolledEvents = useCallback((newEvents: TaskEvent[]) => {
    for (const evt of newEvents) {
      const eventType = evt.event_type
      const eventData = evt.data || {}

      // Route token events → build streaming content (throttled 50ms)
      if (eventType === 'token') {
        const content = (eventData.content as string) || ''
        if (content) {
          appendStreamingContent(content)
          setStatusText('')
        }
        continue
      }

      // Route to executionStore for execution trace
      const execEvent: ExecutionEvent = {
        id: `poll-${evt.sequence}-${evt.id.slice(0, 8)}`,
        type: (eventType === 'agent_thinking' ? 'thinking' : eventType) as ExecutionEvent['type'],
        timestamp: evt.created_at ? new Date(evt.created_at).getTime() : Date.now(),
        sequence: evt.sequence,
        ...eventData,
      } as ExecutionEvent
      addExecutionEvent(sessionId, execEvent)

      // Route step events → progress UI (throttled 500ms)
      if (eventType === 'step') {
        const step: StepInfo = {
          step: (eventData.step as number) || 0,
          total_steps: (eventData.total_steps as number) || 0,
          icon: (eventData.icon as string) || '⏳',
          title: (eventData.title as string) || '',
          description: (eventData.description as string) || '',
          status: (eventData.status as StepInfo['status']) || 'active',
        }
        throttledSetProgressStep(step)
        throttledSetStatusText(`${step.icon} ${step.title}`)
      }

      // Route agent activity events → agent activities UI (status throttled 200ms)
      if (['agent_thinking', 'agent_response', 'tool_start', 'tool_done'].includes(eventType)) {
        const activity = {
          id: `${evt.sequence}-${Math.random().toString(36).slice(2, 6)}`,
          agent_name: (eventData.agent_name as string) || 'Agent',
          agent_role: (eventData.agent_role as string) || '',
          action: eventType,
          detail: (eventData.tool as string) || (eventData.preview as string) || (eventData.output_preview as string) || '',
          timestamp: evt.created_at ? new Date(evt.created_at).getTime() : Date.now(),
        }
        setAgentActivities(prev => [...prev.slice(-20), activity])
        const emoji = eventType === 'tool_start' ? '🔧' : eventType === 'tool_done' ? '✅' : eventType === 'agent_thinking' ? '💭' : '💬'
        throttledSetStatusText(`${emoji} ${activity.agent_name}: ${activity.detail.slice(0, 80)}`)

        // Process store updates
        if (eventType === 'agent_thinking') {
          useProcessStore.getState().addAgentThinking({
            agentName: (eventData.agent_name as string) || 'Agent',
            agentRole: (eventData.agent_role as string) || '',
            content: (eventData.content as string) || (eventData.preview as string) || '',
          })
        }
        if (eventType === 'tool_start') {
          useProcessStore.getState().addToolExecution({
            id: `tool-${evt.sequence}`,
            timestamp: Date.now(),
            agentId: (eventData.agent_name as string) || 'unknown',
            agentName: (eventData.agent_name as string) || 'Agent',
            toolName: (eventData.tool as string) || '',
            toolArgs: (eventData.params as Record<string, unknown>) || {},
            status: 'running',
          })
        }
      }

      // Report building → drive status text (final_report.py emits report_building,
      // not step, so the live status bar would otherwise go quiet during report gen).
      if (eventType === 'report_building') {
        const section = (eventData.section as string) || (eventData.status as string) || ''
        if (section) throttledSetStatusText(`📝 ${section.slice(0, 80)}`)
      }

      // Route process stage transitions
      if (eventType === 'planning_start') {
        useProcessStore.getState().setStage('planning')
      } else if (eventType === 'planning_done') {
        useProcessStore.getState().setStage('spawning')
      } else if (eventType === 'agent_spawn') {
        useProcessStore.getState().setStage('spawning')
      } else if (eventType === 'synthesis_start') {
        useProcessStore.getState().setStage('synthesizing')
      }
    }
  }, [sessionId, addExecutionEvent, appendStreamingContent, throttledSetStatusText, throttledSetProgressStep])

  const handlePollingComplete = useCallback((finalEvent: TaskEvent) => {
    const eventData = finalEvent.data || {}

    // SYNTHETIC completion comes from useTaskPolling's stale watchdog after /state
    // confirmed the task is no longer running (silent death — no terminal event ever
    // written). In that case wsTaskRunning can remain stuck true, so we must block the
    // line-582 effect from re-activating polling and reopening this finished turn into
    // a flip-flop loop. pollKilledRef is reset to false on the next sendMessage().
    if (eventData.synthetic === true) {
      pollKilledRef.current = true
    }

    // Task completed — load final message from DB
    setIsProcessing(false)
    setIsStreaming(false)
    setStatusText('')
    setExecutionActive(sessionId, false)
    setPollingTaskId(null)
    useProcessStore.getState().endProcess(finalEvent.event_type === 'task_completed')

    // Get any accumulated streaming content
    const finalContent = streamingContentRef.current
    setStreamingContent('')
    streamingContentRef.current = ''

    if (finalContent) {
      // Show locally immediately
      addMessage(sessionId, {
        id: `local-${crypto.randomUUID()}`,
        role: 'assistant',
        content: finalContent,
        timestamp: new Date().toISOString(),
      })
    }

    // Load authoritative version from DB (dedup handles overlap)
    const loadFromDb = async () => {
      await useSessionStore.getState().loadSessionMessages(sessionId)
      // Retry if no assistant message found yet (backend may still be persisting)
      if (!finalContent) {
        const msgs = useSessionStore.getState().messagesBySession[sessionId] || []
        const hasReply = msgs.some(m => m.role === 'assistant' && m.timestamp &&
          new Date(m.timestamp).getTime() > (processingStartTimeRef.current || 0))
        if (!hasReply) {
          for (const delay of [1000, 3000, 6000]) {
            await new Promise(r => setTimeout(r, delay))
            await useSessionStore.getState().loadSessionMessages(sessionId)
            const retry = useSessionStore.getState().messagesBySession[sessionId] || []
            if (retry.some(m => m.role === 'assistant' && m.timestamp &&
              new Date(m.timestamp).getTime() > (processingStartTimeRef.current || 0))) break
          }
        }
      }
    }
    void loadFromDb()

    // Handle terminal states. A task that did NOT complete successfully
    // (failed / crashed / cancelled) gets an explanatory message and, if a
    // checkpoint exists, a resume affordance. Previously task_cancelled fell
    // into the `else` branch and was wrongly reported as "Task completed".
    const evType = finalEvent.event_type
    const isCancelled = evType === 'task_cancelled'
    const isFailedOrCrashed = evType === 'task_failed' || evType === 'task_crashed'

    if (isCancelled || isFailedOrCrashed) {
      const errMsg = (eventData.error as string) || (isCancelled ? 'Đã hủy nhiệm vụ' : 'Task failed')
      // Cancelled tasks keep a checkpoint, so they are resumable even without
      // an explicit flag; failed/crashed rely on the backend `resumable` flag.
      const isResumable = isCancelled || eventData.resumable === true
      const endedTaskId = (eventData.task_id as string) || pollingTaskId

      addMessage(sessionId, {
        id: crypto.randomUUID(),
        role: 'system',
        content: isCancelled
          ? `⏹️ Đã hủy nhiệm vụ. Bạn có thể tiếp tục từ checkpoint gần nhất.`
          : (isResumable
              ? `⚠️ Task bị gián đoạn: ${errMsg}. Bạn có thể tiếp tục nhiệm vụ.`
              : `❌ Error: ${errMsg}`),
        timestamp: new Date().toISOString(),
      })
      if (isFailedOrCrashed) setCanRetry(!!lastSentMessageRef.current)
      addNotification(
        isCancelled ? 'info' : 'error',
        isCancelled ? 'Task Cancelled' : (isResumable ? 'Task Interrupted' : 'Task Failed'),
        errMsg,
      )

      // Show resume button if a checkpoint exists for this session/task.
      if (endedTaskId) {
        // Optimistically enable for resumable terminal states; confirm + get the
        // real turn number from the resumable list.
        if (isResumable) {
          setCanResume(true)
          setResumeInfo({ turn: 0, taskId: endedTaskId })
        }
        fetch(`/api/v1/tasks/resumable?session_id=${sessionId}`)
          .then(r => r.json())
          .then(data => {
            const match = data.tasks?.find((t: any) => t.task_id === endedTaskId || t.session_id === sessionId)
            if (match) {
              setCanResume(true)
              setResumeInfo({ turn: match.turn_number, taskId: match.task_id })
            }
          })
          .catch(() => {})
      }
    } else {
      addNotification('success', 'Task completed', 'Response ready')
    }

    // Re-fetch sessions for sidebar
    useSessionStore.getState().fetchSessions()
    clearStreamingState(sessionId)
  }, [sessionId, addMessage, addNotification, setExecutionActive, clearStreamingState])

  const handlePollingError = useCallback((errMsg: string) => {
    console.warn('[ChatPanel] Polling error:', errMsg)
  }, [])

  const { pollNow } = useTaskPolling({
    taskId: pollingTaskId,
    sessionId,
    intervalMs: 2000,
    onEvents: handlePolledEvents,
    onComplete: handlePollingComplete,
    onError: handlePollingError,
  })
  // Wire pollNow into the ref the WS doorbell uses to wake polling.
  // Done in an effect (not during render) so the ref is updated only after a
  // committed render — avoids capturing a stale callback under StrictMode /
  // concurrent rendering.
  useEffect(() => {
    pollNowRef.current = pollNow
  }, [pollNow])

  // CONSOLIDATED-POLLING: When WS reconnects and detects a running task that we're not
  // actively processing, activate useTaskPolling by setting pollingTaskId.
  // This replaces the old background polling effect with the single useTaskPolling hook.
  useEffect(() => {
    if (!(wsTaskRunning && !isProcessing && !pollingTaskId && !pollKilledRef.current)) return

    // MULTI-TAB FIX: WS only tells us A task is running for this session, NOT which one.
    // Inferring the id from processStore (focusedTaskId/taskId) is wrong on a PASSIVE tab
    // (one that did not send the message): it still holds a PREVIOUS, already-finished
    // turn, so polling it replays that turn's terminal event → false "❌ Task failed" +
    // "Tiếp tục nhiệm vụ" while the real task keeps running. Ask the backend (the single
    // source of truth) which task is running right now, and only activate when confirmed.
    let cancelled = false
    const activateFromState = async () => {
      try {
        const state = await apiGet<{ is_running: boolean; task_id: string | null }>(
          `/sessions/${sessionId}/state`,
        )
        if (cancelled || pollKilledRef.current) return
        const taskId = state?.task_id || ''
        if (state?.is_running === true && taskId && !taskId.startsWith('msg-')) {
          console.info('[ChatPanel] WS reports task running — backend task is:', taskId)
          // Focus processStore on the LIVE task so ProcessStream shows it (not the old turn).
          useProcessStore.getState().startOrResumeProcess(taskId, sessionId)
          setPollingTaskId(taskId)
          setStatusText('🔄 Task running — polling for updates...')
          setExecutionActive(sessionId, true)
        }
      } catch {
        // State check failed — leave polling inactive; the WS doorbell + watchdog retry.
      }
    }
    void activateFromState()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsTaskRunning, isProcessing, pollingTaskId, sessionId])

  // BUG-FIX: Detect task completion via WS when frontend missed the stream_end event.
  // When wsTaskRunning transitions true→false but UI still shows processing/streaming.
  //
  // ROOT-CAUSE FIX (false "completed"): wsTaskRunning flips to false for MANY reasons
  // that are NOT completion — WS disconnect, reconnect, backend evicting the task from
  // memory, an SSE stale timeout. Previously this effect unconditionally called
  // endProcess(true), declaring the turn "completed" while the task was still running.
  // Now we VERIFY against the authoritative session state before deciding: if the
  // backend still reports the task as running (it now has a DB fallback that survives
  // restart/eviction), we keep polling instead of falsely completing.
  const prevWsTaskRunningRef = useRef(wsTaskRunning)
  useEffect(() => {
    const wasRunning = prevWsTaskRunningRef.current
    prevWsTaskRunningRef.current = wsTaskRunning
    if (!(wasRunning && !wsTaskRunning && (isProcessing || isStreaming))) return

    let cancelled = false
    const verifyThenResolve = async () => {
      let stillRunning = false
      let stateTaskId: string | null = null
      try {
        const state = await apiGet<{ is_running: boolean; task_id: string | null }>(
          `/sessions/${sessionId}/state`,
        )
        stillRunning = state?.is_running === true
        stateTaskId = state?.task_id ?? null
      } catch (err) {
        // Couldn't verify — stay conservative and assume still running so we keep
        // polling rather than declaring a possibly-live task complete.
        console.warn('[ChatPanel] Could not verify session state on WS drop:', err)
        stillRunning = true
      }
      if (cancelled) return

      if (stillRunning) {
        // Task is alive on the backend — WS just dropped. Re-activate polling so the
        // UI keeps receiving events and reaches the real terminal event.
        console.info('[ChatPanel] WS dropped but task still running — resuming polling')
        const resumeTaskId =
          stateTaskId ||
          useProcessStore.getState().focusedTaskId ||
          useProcessStore.getState().taskId
        pollKilledRef.current = false
        if (resumeTaskId && !resumeTaskId.startsWith('msg-')) {
          setPollingTaskId(resumeTaskId)
          setExecutionActive(sessionId, true)
          setStatusText('🔄 Đang tiếp tục theo dõi tiến trình...')
        }
        return
      }

      // Backend confirms the task is no longer running — safe to complete.
      // Set pollKilledRef=true (not false) so the line-582 effect cannot immediately
      // re-activate polling while useResilientChat's wsTaskRunning is briefly still
      // true (it lags the backend /state answer) — that would reopen a finished turn.
      // It is reset to false on the next sendMessage().
      console.info('[ChatPanel] WS reports task no longer running — loading results from DB')
      pollKilledRef.current = true
      setIsProcessing(false)
      setIsStreaming(false)
      setPollingTaskId(null)
      setStatusText('')
      useProcessStore.getState().endProcess(true, 'Task completed (detected via WS reconnect)')
      useSessionStore.getState().loadSessionMessages(sessionId)
    }
    void verifyThenResolve()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsTaskRunning, sessionId])

  // ── Scroll management ───────────────────────────────────────

  const scrollToBottom = useCallback((instant = false, force = false) => {
    const el = scrollContainerRef.current
    if (!el) return
    // AUDIT-FIX: Allow forced scroll (e.g., when user sends a message)
    if (!force && userScrolledUpRef.current) return
    cancelAnimationFrame(scrollRafRef.current)
    scrollRafRef.current = requestAnimationFrame(() => {
      if (instant) {
        el.scrollTop = el.scrollHeight
      } else {
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
      }
    })
  }, [])

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    const distanceFromTop = el.scrollTop
    const wasScrolledUp = userScrolledUpRef.current
    // AUDIT-FIX BUG-3: Unified threshold — 150px for both auto-scroll AND button visibility.
    // Previously: auto-scroll disabled at 120px but button only showed at 300px = confusing gap.
    userScrolledUpRef.current = distanceFromBottom > 150
    setShowScrollTop(distanceFromTop > 200)
    setShowScrollBottom(distanceFromBottom > 150)
    // AUDIT-ENHANCE-2: Calculate scroll progress for progress ring (0 = top, 1 = bottom)
    const scrollableHeight = el.scrollHeight - el.clientHeight
    if (scrollableHeight > 0) {
      setScrollProgress(Math.min(1, Math.max(0, el.scrollTop / scrollableHeight)))
    }
    // Reset unread count when user scrolls to bottom
    if (wasScrolledUp && !userScrolledUpRef.current) {
      setUnreadCount(0)
    }
  }, [])

  // Track unread messages when user is scrolled up
  // AUDIT-FIX BUG-2: Only count NON-user messages (assistant/system) as unread.
  // Previously counted user's own sent messages → showed badge immediately after send.
  const prevMessageCountRef = useRef(messages.length)
  useEffect(() => {
    if (messages.length <= prevMessageCountRef.current) {
      prevMessageCountRef.current = messages.length
      return
    }
    // Check what new messages were added (only count non-user messages as unread)
    const newMessages = messages.slice(prevMessageCountRef.current)
    const newNonUserCount = newMessages.filter((m) => m.role !== 'user').length
    if (newNonUserCount > 0 && userScrolledUpRef.current) {
      setUnreadCount((prev) => prev + newNonUserCount)
    }
    prevMessageCountRef.current = messages.length
  }, [messages.length]) // eslint-disable-line react-hooks/exhaustive-deps
  // AUDIT-BUG-19 FIX: Removed `messages` reference from deps — messages.length
  // is sufficient. Including the full array triggers on every reference change
  // (zustand produces new array on each state update) even when length is unchanged.

  // AUDIT-BUG-17 FIX: Only scroll on new messages (length change).
  // Previously, progressSteps/agentActivities/statusText/liveDebate triggered scroll
  // which made it impossible for users to scroll up and read history while task runs.
  // Streaming content has its own dedicated scroll effect below.
  useEffect(() => {
    scrollToBottom(true)
  }, [messages.length, scrollToBottom])

  // Scroll instantly on streaming content (avoid jitter)
  useEffect(() => {
    if (streamingContent) {
      scrollToBottom(true)
    }
  }, [streamingContent, scrollToBottom])

  // Keep streamingContentRef in sync so onDone closure has latest value
  useEffect(() => {
    streamingContentRef.current = streamingContent
  }, [streamingContent])

  // ── Reset processStore when sessionId changes ──────────────
  // RT08-FIX: Also abort any active SSE stream from the previous session
  // to prevent cross-session content bleed.
  // TAB-SWITCH-FIX: Save streaming state of previous session before clearing,
  // so returning to that tab can restore/reconnect.
  useEffect(() => {
    const prevId = prevSessionIdRef.current

    // MULTI-TAB-FIX: Always sync processStore.currentSessionId on session change.
    // This enables Monitor WS events to route correctly for this tab.
    useProcessStore.getState().setSession(sessionId)

    // TAB-SWITCH-GUARD: Wrap entire tab-switch logic in try/catch to prevent
    // any error in state save/restore from breaking the session switch completely.
    try {
      // Save streaming state for the PREVIOUS session before switching away
      if (prevId && prevId !== sessionId) {
        try {
          const currentContent = streamingContentRef.current
          if (currentContent || isProcessing || isStreaming) {
            saveStreamingState(prevId, {
              content: currentContent,
              isProcessing,
              isStreaming,
              statusText,
            })
          }
        } catch (saveErr) {
          console.warn('[ChatPanel] Failed to save streaming state for previous session:', saveErr)
        }
      }

      // AUDIT-BUG-9 FIX: Don't reset processStore if target session has execution
      // events (even if task is completed). This preserves debate/tool history
      // when switching back to a completed task's tab.
      // Previously: only checked activeBySession (running tasks) → completed tasks got wiped.
      const targetExecutionActive = useExecutionStore.getState().activeBySession[sessionId]
      const targetHasEvents = (useExecutionStore.getState().eventsBySession[sessionId]?.length ?? 0) > 0
      if (!targetExecutionActive && !targetHasEvents) {
        useProcessStore.getState().resetProcess()
      }

      // Try to restore streaming state for the NEW session (tab we're switching TO)
      let restored = false
      try {
        const savedState = useSessionStore.getState().getStreamingState(sessionId)
        if (savedState && savedState.content) {
          // Restore saved streaming content — the F5 recovery polling will reconnect.
          // NOTE: Do NOT restore isProcessing=true — that flag means "active SSE stream"
          // and would block the polling effect (condition: `if (!executionActive || isProcessing) return`).
          // Instead, show the saved content via isStreaming=true, and let polling take over.
          setStreamingContent(savedState.content)
          streamingContentRef.current = savedState.content
          setIsProcessing(false)
          setIsStreaming(true)  // Keep streaming visible while reconnecting
          setStatusText(savedState.statusText || 'Reconnecting...')
          restored = true
        }
      } catch (restoreErr) {
        console.warn('[ChatPanel] Failed to restore streaming state:', restoreErr)
      }

      if (!restored) {
        // No saved state — clear everything (fresh session or completed task)
        setStreamingContent('')
        streamingContentRef.current = ''
        setIsProcessing(false)
        setIsStreaming(false)
        setStatusText('')
      }

      // TAB-PERSIST-FIX: If switching TO a session with active execution,
      // do NOT clear UI state — the tryLoad effect (below) will restore from
      // executionStore/processStore automatically on [sessionId] change.
      if (!targetExecutionActive) {
        setProgressSteps([])
        setAgentActivities([])
        setLiveDebate(null)
        setPendingDecision(null)
        setInlineDebateRounds([])
        inlineDebateRoundsRef.current = []
      }
    } catch (tabSwitchErr) {
      // CRITICAL FALLBACK: If tab switch logic fails entirely, ensure UI is in a usable state
      console.error('[ChatPanel] Tab switch error  - resetting to clean state:', tabSwitchErr)
      setStreamingContent('')
      streamingContentRef.current = ''
      setIsProcessing(false)
      setIsStreaming(false)
      setStatusText('')
      setProgressSteps([])
      setAgentActivities([])
      setLiveDebate(null)
      setPendingDecision(null)
      setInlineDebateRounds([])
      inlineDebateRoundsRef.current = []
    }

    // Update ref to track current session
    prevSessionIdRef.current = sessionId
    // Reset scroll state so auto-scroll works in new session
    userScrolledUpRef.current = false
    setShowScrollTop(false)
    setShowScrollBottom(false)
    setUnreadCount(0)
  }, [sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── SAFETY: Auto-reset stuck isProcessing ──
  // DEBATE-VIS-FIX: Dynamic timeout based on processStore stage.
  // Debate/executing tasks can run 30+ minutes legitimately.
  // Before auto-resetting, check backend task status to avoid killing live tasks.
  useEffect(() => {
    if (!isProcessing) return
    const currentStage = useProcessStore.getState().stage
    const isLongRunning = ['debating', 'executing', 'synthesizing', 'deep_analysis', 'spawning', 'preparing', 'researching'].includes(currentStage)
    // Long-running tasks (debate/swarm): 60min. Normal tasks: 10min (was 5min, too aggressive).
    const timeout = isLongRunning ? 3_600_000 : 600_000

    const timer = setTimeout(async () => {
      // DEBATE-VIS-FIX: Before force-resetting, check if task is actually still running.
      // If yes, DON'T reset — extend the timer implicitly (next effect cycle will re-check).
      try {
        const state = await apiGet<{ is_running: boolean }>(`/sessions/${sessionId}/state`)
        if (state.is_running) {
          console.info('[ChatPanel] Safety timeout fired but task still running  - skipping reset')
          return // Task is genuinely running, don't reset
        }
      } catch {
        // State check failed — DON'T reset blindly, the task might still be running
        // but the API call failed due to network issues. Only reset after 2 consecutive failures.
        console.warn('[ChatPanel] Safety timeout: state check failed  - skipping reset (conservative)')
        return
      }
      console.warn(`[ChatPanel] isProcessing stuck > ${timeout / 60000}min  - auto-resetting`)
      // FIX-POLL-LEAK: Also clear executionActive, polling, and SSE flag
      // to prevent stale task ID from triggering infinite polling loops.
      pollKilledRef.current = true
      setIsProcessing(false)
      setIsStreaming(false)
      setPollingTaskId(null)
      setStatusText('')
      setExecutionActive(sessionId, false)
      useProcessStore.getState().endProcess(false, 'Processing timeout — auto-reset')
      // BUG-FIX: Load messages from DB after auto-reset.
      // Task likely completed while WS was disconnected — result sits in DB.
      useSessionStore.getState().loadSessionMessages(sessionId)
    }, timeout)
    return () => clearTimeout(timer)
  }, [isProcessing, sessionId])

  // ── WATCHDOG: Detect backend task running while frontend shows idle/completed ──
  // Covers race conditions where WS misses task_started or completion events.
  // Checks backend state every 15s when frontend is not actively tracking a task.
  useEffect(() => {
    if (isProcessing || pollingTaskId) return

    const timer = setInterval(async () => {
      if (pollKilledRef.current) return
      // Re-check conditions at fire time (not just mount time)
      const currentStage = useProcessStore.getState().stage
      if (currentStage !== 'idle' && currentStage !== 'completed' && currentStage !== 'failed') return
      const msgs = useSessionStore.getState().messagesBySession[sessionId] || []
      const lastMsg = msgs[msgs.length - 1]
      if (!lastMsg || lastMsg.role !== 'user') return

      try {
        const state = await apiGet<{ is_running: boolean; task_id?: string }>(
          `/sessions/${sessionId}/state`
        )
        if (state.is_running && state.task_id) {
          console.info('[ChatPanel] Watchdog: backend task running but frontend idle — activating polling:', state.task_id)
          pollKilledRef.current = false
          const ps = useProcessStore.getState()
          ps.startOrResumeProcess(state.task_id, sessionId)
          ps.setStage('executing')
          setExecutionActive(sessionId, true)
          setPollingTaskId(state.task_id)
          setIsProcessing(true)
          setStatusText('🔄 Task running — reconnected...')
        }
      } catch { /* network error — retry next interval */ }
    }, 15_000)

    return () => clearInterval(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, isProcessing, pollingTaskId])

  // ── Load execution events from DB on session change ─────────

  useEffect(() => {
    /**
     * Convert a flat SSE-style event (from metadata.execution_events or task_events API)
     * into an ExecutionEvent for the executionStore / processStore.
     *
     * This shared helper is used by BOTH:
     * - Primary source: assistant message metadata.execution_events (always available)
     * - Fallback source: task events API (backward compat for older sessions)
     */
    const mapSseToExecEvent = (
      e: Record<string, unknown>,
      idx: number,
      sourceId: string,
      baseTimestamp?: number,
    ): ExecutionEvent => {
      // Use sequence from API if available, otherwise use array index as tiebreaker.
      // Add idx as sub-millisecond offset to guarantee stable sort order
      // for events with identical created_at timestamps.
      const seq = (e.sequence as number) ?? idx
      // Use a deterministic base timestamp (from message creation time) for stable
      // replay ordering on reload. Avoids Date.now() which shifts on each F5.
      const stableBase = baseTimestamp ?? 0
      const base = {
        id: `replay-${idx}-${sourceId}`,
        timestamp: e.created_at
          ? new Date(e.created_at as string).getTime() + seq * 0.001
          : stableBase + idx * 10, // 10ms gap per event for stable ordering
      }

      // Normalize: SSE events use "type", task_events API uses "event_type"
      const eventType = (e.type as string) || (e.event_type as string) || ''

      if (eventType === 'step') {
        return {
          ...base,
          type: 'step' as const,
          step: (e.step as number) || (e.data as Record<string, unknown>)?.step as number || 0,
          total_steps: (e.total_steps as number) || (e.data as Record<string, unknown>)?.total_steps as number || 0,
          icon: (e.icon as string) || (e.data as Record<string, unknown>)?.icon as string || '⏳',
          title: (e.title as string) || (e.data as Record<string, unknown>)?.title as string || '',
          description: (e.description as string) || (e.data as Record<string, unknown>)?.description as string || '',
          status: (e.status as 'active' | 'done' | 'error' | 'pending') || 'done',
        }
      }
      if (eventType === 'tool_start' || eventType === 'tool_done') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: eventType as 'tool_start' | 'tool_done',
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
          tool: (e.tool as string) || (d?.tool as string) || '',
          params: e.params || d?.params || d?.params_preview,
          output: e.output || d?.output || e.output_preview || d?.output_preview,
          success: (e.success ?? d?.success) as boolean | undefined,
          execution_time_ms: (e.execution_time_ms ?? d?.execution_time_ms) as number | undefined,
        }
      }
      if (eventType === 'agent_thinking') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'thinking' as const,
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
          agent_role: (e.agent_role as string) || (d?.agent_role as string) || '',
          content: (e.content as string) || (d?.content as string) || (e.preview as string) || (d?.preview as string) || '',
        }
      }
      if (eventType === 'agent_spawn') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'agent_spawn' as const,
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || '',
          agent_role: (e.agent_role as string) || (d?.agent_role as string) || '',
          system_prompt: (e.system_prompt_preview as string) || (d?.system_prompt as string) || (d?.system_prompt_preview as string),
        }
      }
      if (eventType === 'agent_response') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'thinking' as const,
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
          agent_role: 'response',
          content: (e.content as string) || (d?.content as string) || '',
        }
      }
      // Swarm round events
      if (eventType === 'swarm_round' || eventType === 'swarm_round_start') {
        const d = (e.data as Record<string, unknown>) || e
        const rawProposals = (e.proposals as Array<Record<string, unknown>>) || (d?.proposals as Array<Record<string, unknown>>) || []
        const rawRebuttals = (e.rebuttals as Array<Record<string, unknown>>) || (d?.rebuttals as Array<Record<string, unknown>>) || []
        const verdictData = e.verdict || d?.verdict
        return {
          ...base,
          type: 'swarm_round' as const,
          round: (e.round as number) || (d?.round as number) || 0,
          total_rounds: (e.total_rounds as number) || (d?.total_rounds as number) || 0,
          proposals: rawProposals.map((p) => ({
            agent: (p.agent as string) || (p.agent_name as string) || 'Agent',
            role: (p.role as string) || (p.agent_role as string),
            content: (p.content as string) || '',
            tools_used: (p.tools_used as string[]) || undefined,
          })),
          rebuttals: rawRebuttals.map((r) => ({
            agent: (r.agent as string) || (r.agent_name as string) || 'Agent',
            content: (r.content as string) || '',
          })),
          verdict: verdictData ? {
            verdict: (verdictData as Record<string, unknown>).verdict as string | undefined,
            winner: ((verdictData as Record<string, unknown>).winner as string) || null,
            synthesis: (verdictData as Record<string, unknown>).synthesis as string | undefined,
            reasoning: (verdictData as Record<string, unknown>).reasoning as string | undefined,
          } : undefined,
        }
      }
      // Debate granular events — preserve original types for history replay
      if (eventType === 'debate_proposal') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'debate_proposal' as const,
          round: (e.round as number) || (d?.round as number) || 0,
          total_rounds: (e.total_rounds as number) || (d?.total_rounds as number) || 0,
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
          agent_role: (e.agent_role as string) || (d?.agent_role as string) || '',
          content: (e.content as string) || (d?.content as string) || '',
          tools_used: (e.tools_used as string[]) || (d?.tools_used as string[]) || [],
        }
      }
      if (eventType === 'debate_rebuttal') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'debate_rebuttal' as const,
          round: (e.round as number) || (d?.round as number) || 0,
          total_rounds: (e.total_rounds as number) || (d?.total_rounds as number) || 0,
          agent_name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
          content: (e.content as string) || (d?.content as string) || '',
        }
      }
      if (eventType === 'debate_verdict') {
        const d = (e.data as Record<string, unknown>) || e
        const verdictData = (e.verdict || d?.verdict || {}) as Record<string, unknown>
        return {
          ...base,
          type: 'debate_verdict' as const,
          round: (e.round as number) || (d?.round as number) || 0,
          total_rounds: (e.total_rounds as number) || (d?.total_rounds as number) || 0,
          verdict: verdictData,
        }
      }
      // DEBATE-CHAT-FIX: Map chat-based debate events (from DebateChatRoom)
      if (eventType === 'debate_started') {
        return {
          ...base,
          type: 'debate_started' as const,
          task: (e.task as string) || '',
          agents: (e.agents as string[]) || [],
          max_turns: (e.max_turns as number) || 30,
        }
      }
      if (eventType === 'debate_message') {
        return {
          ...base,
          type: 'debate_message' as const,
          turn: (e.turn as number) || 0,
          agent_name: (e.agent_name as string) || 'Agent',
          agent_role: (e.agent_role as string) || '',
          content: (e.content as string) || '',
          message_type: (e.message_type as string) || 'opinion',
          references: (e.references as number[]) || [],
          tools_used: (e.tools_used as string[]) || [],
          evidence_items: (e.evidence_items as string[]) || [],
          confidence: (e.confidence as number) || 0.7,
          is_final: e.is_final as boolean | undefined,
          total_turns: (e.total_turns as number) || undefined,
          budget_remaining: (e.budget_remaining as number) || undefined,
          quality_score: (e.quality_score as number) || undefined,
          convergence_score: (e.convergence_score as number) || undefined,
        }
      }
      if (eventType === 'moderator_intervention') {
        return {
          ...base,
          type: 'moderator_intervention' as const,
          turn: (e.turn as number) || 0,
          message: (e.message as string) || (e.content as string) || '',
          action: (e.action as string) || '',
          quality_score: (e.quality_score as number) || undefined,
          convergence_score: (e.convergence_score as number) || undefined,
        }
      }
      if (eventType === 'debate_concluded') {
        return {
          ...base,
          type: 'debate_concluded' as const,
          total_turns: (e.total_turns as number) || 0,
          concluded_by: (e.concluded_by as string) || 'unknown',
          convergence_score: (e.convergence_score as number) || undefined,
          agents: (e.agents as string[]) || [],
        }
      }
      // Deep reasoning events — preserve specific types for granularity
      if (eventType === 'deep_analysis') {
        return {
          ...base,
          type: 'deep_analysis' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'self_reflection') {
        return {
          ...base,
          type: 'self_reflection' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'auto_verify') {
        return {
          ...base,
          type: 'auto_verify' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'completion_check') {
        return {
          ...base,
          type: 'completion_check' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'mid_analysis') {
        return {
          ...base,
          type: 'mid_analysis' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'react_phase') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'react_phase' as const,
          phase: (e.phase as string) || (d?.phase as string) || '',
          round: (e.round as number) || (d?.round as number),
        }
      }
      if (eventType === 'debate_critique') {
        return {
          ...base,
          type: 'debate_critique' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      if (eventType === 'debate_revision') {
        return {
          ...base,
          type: 'debate_revision' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      // Progressive Compaction event — shows context management in action
      if (eventType === 'context_compaction') {
        const d = (e.data as Record<string, unknown>) || e
        return {
          ...base,
          type: 'step' as const,
          step: (d?.compaction_number as number) || (e.compaction_number as number) || 0,
          total_steps: 0,
          icon: '🔄',
          title: `Context Compaction #${(d?.compaction_number as number) || (e.compaction_number as number) || ''}`,
          description: (d?.status as string) || (e.status as string) || `Tokens: ${(d?.tokens_before as number) || '?'} → ${(d?.tokens_after as number) || '?'}`,
          status: 'done' as const,
        }
      }
      // Deep Analysis Complete — full analysis data (reuse existing card)
      if (eventType === 'deep_analysis_complete') {
        return {
          ...base,
          type: 'deep_analysis' as const,
          data: (e.data as Record<string, unknown>) || e,
        }
      }
      // System events
      if (eventType === 'event') {
        return {
          ...base,
          type: 'step' as const,
          step: 0,
          total_steps: 0,
          icon: '📋',
          title: (e.event_type as string) || 'system event',
          description: '',
          status: 'done' as const,
        }
      }
      // Default: step event
      const d = (e.data as Record<string, unknown>) || e
      return {
        ...base,
        type: 'step' as const,
        step: (e.step as number) || (d?.step as number) || 0,
        total_steps: (e.total_steps as number) || (d?.total_steps as number) || 0,
        icon: (e.icon as string) || (d?.icon as string) || '📋',
        title: (e.title as string) || (d?.title as string) || eventType,
        description: (e.description as string) || (d?.description as string) || '',
        status: (e.status as any) || (d?.status as any) || 'done',
      }
    }

    /** Safely coerce a value to an array — returns [] if not actually an array. */
    const safeArray = <T,>(val: unknown): T[] => (Array.isArray(val) ? val as T[] : [])

    /**
     * Populate processStore from a flat list of SSE-style events.
     * Used both for metadata-based replay and task-events API replay.
     * Wrapped in try/catch so a single malformed event never crashes the whole UI.
     */
    const populateProcessStore = (
      events: Array<Record<string, unknown>>,
      taskId: string,
      /** If true, mark process as completed. If false, leave it running. Default: true (history replay). */
      markCompleted: boolean = true,
      /** Stable base timestamp for deterministic ordering on replay. Avoids Date.now() drift on F5. */
      baseTimestamp?: number,
    ) => { try {
      // MED-01 FIX: Debug logging removed for production

      const ps = useProcessStore.getState()

      // FLICKER-FIX: Don't rebuild a finished task — prevents completed→running flicker
      if (ps.taskId === taskId && (ps.stage === 'completed' || ps.stage === 'failed')) {
        return
      }

      // FLICKER-FIX: Only reset arrays when switching to a different task.
      // Same-task rebuilds skip resetDataArrays() to prevent momentary empty state
      // that causes ProcessStream to fallback to executionStore (flicker).
      if (ps.taskId !== taskId) {
        ps.startOrResumeProcess(taskId, sessionId)
        ps.resetDataArrays()
      } else {
        ps.startOrResumeProcess(taskId, sessionId)
      }
      // Use a stable base for replay timestamps — fall back to now only if truly unavailable
      const stableBase = baseTimestamp ?? 0

      // PRE-PASS: Aggregate individual debate_proposal/debate_rebuttal/debate_verdict
      // events into per-round groups. Backend emits these individually per agent,
      // so we must group them before adding to processStore as complete debate rounds.
      const debateProposalsByRound = new Map<number, Array<Record<string, unknown>>>()
      const debateRebuttalsByRound = new Map<number, Array<Record<string, unknown>>>()
      const debateVerdictsByRound = new Map<number, Record<string, unknown>>()
      let debateTotalRounds = 0
      for (const e of events) {
        if (e == null || typeof e !== 'object') continue
        const et = String(e.type || e.event_type || '')
        const dd = (e.data != null && typeof e.data === 'object' ? e.data as Record<string, unknown> : e) as Record<string, unknown>
        if (et === 'debate_proposal') {
          const rn = (e.round as number) || (dd?.round as number) || 1
          if (!debateProposalsByRound.has(rn)) debateProposalsByRound.set(rn, [])
          debateProposalsByRound.get(rn)!.push(dd)
          debateTotalRounds = Math.max(debateTotalRounds, (e.total_rounds as number) || (dd?.total_rounds as number) || rn)
        } else if (et === 'debate_rebuttal') {
          const rn = (e.round as number) || (dd?.round as number) || 1
          if (!debateRebuttalsByRound.has(rn)) debateRebuttalsByRound.set(rn, [])
          debateRebuttalsByRound.get(rn)!.push(dd)
        } else if (et === 'debate_verdict') {
          const rn = (e.round as number) || (dd?.round as number) || 1
          const vd = (e.verdict || dd?.verdict || {}) as Record<string, unknown>
          debateVerdictsByRound.set(rn, vd)
        }
      }

      for (let idx = 0; idx < events.length; idx++) {
        // DEFENSIVE: Per-event try/catch — one malformed event never crashes entire replay
        try {
        const e = events[idx]
        if (e == null || typeof e !== 'object') continue
        const eventType = String(e.type || e.event_type || '')
        const d = (e.data != null && typeof e.data === 'object' ? e.data as Record<string, unknown> : e) as Record<string, unknown>
        // TIMESTAMP-FIX: Prefer data.timestamp (exact emit time) over created_at (DB insert time).
        // data.timestamp is an ISO string set at the moment the event was emitted in react_loop.
        // created_at is when the async persist task actually wrote to DB (may be delayed/out of order).
        let eventTs = stableBase + idx * 10
        try {
          const emitTime = e.timestamp as string
          if (emitTime) {
            eventTs = new Date(emitTime).getTime() || eventTs
          } else if (e.created_at) {
            eventTs = new Date(e.created_at as string).getTime() || eventTs
          }
        } catch { /* bad date */ }

        if (eventType === 'agent_thinking') {
          ps.addAgentThinking({
            agentName: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
            agentRole: (e.agent_role as string) || (d?.agent_role as string) || '',
            content: (e.content as string) || (d?.content as string) || (e.preview as string) || (d?.preview as string) || '',
          })
        } else if (eventType === 'tool_done') {
          ps.addToolExecution({
            id: `hist-tool-${idx}-${taskId}`,
            timestamp: eventTs,
            agentId: (e.agent_name as string) || (d?.agent_name as string) || 'unknown',
            agentName: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
            toolName: (e.tool as string) || (d?.tool as string) || '',
            toolArgs: (e.params as Record<string, unknown>) || (d?.params as Record<string, unknown>) || {},
            status: (e.success === false || d?.success === false) ? 'failed' : 'completed',
            result: (e.output as string) || (d?.output as string) || (e.output_preview as string) || (d?.output_preview as string),
            duration: (e.execution_time_ms as number) || (d?.execution_time_ms as number),
          })
        } else if (eventType === 'tool_start') {
          // Only add if no matching tool_done follows (avoid duplicates)
          // For simplicity in replay, we add all tool_starts as running
          ps.addToolExecution({
            id: `hist-ts-${idx}-${taskId}`,
            timestamp: eventTs,
            agentId: (e.agent_name as string) || (d?.agent_name as string) || 'unknown',
            agentName: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
            toolName: (e.tool as string) || (d?.tool as string) || '',
            toolArgs: (e.params as Record<string, unknown>) || (d?.params as Record<string, unknown>) || {},
            status: 'completed',
          })
        } else if (eventType === 'agent_spawn') {
          // Track agent spawns in processStore for agent display
          ps.addAgent({
            id: `agent-replay-${idx}-${taskId}`,
            name: (e.agent_name as string) || (d?.agent_name as string) || 'Agent',
            role: (e.agent_role as string) || (d?.agent_role as string) || '',
            status: 'completed',
            spawnedAt: eventTs,
            systemPromptPreview: (e.system_prompt_preview as string) || (d?.system_prompt_preview as string),
          })
        } else if (eventType === 'swarm_round' || eventType === 'swarm_round_start') {
          const rawProposals = (e.proposals as Array<Record<string, unknown>>) || (d?.proposals as Array<Record<string, unknown>>) || []
          const rawRebuttals = (e.rebuttals as Array<Record<string, unknown>>) || (d?.rebuttals as Array<Record<string, unknown>>) || []
          const verdictData = e.verdict || d?.verdict
          // Only add debate round if there are actual proposals (swarm_round_start has none)
          if (rawProposals.length > 0) {
            ps.addDebateRound({
              round: (e.round as number) || (d?.round as number) || 0,
              proposals: rawProposals.map((p) => ({
                agentId: (p.agent as string) || '',
                agentName: (p.agent as string) || (p.agent_name as string) || 'Agent',
                agentRole: (p.role as string) || (p.agent_role as string) || '',
                content: (p.content as string) || '',
              })),
              rebuttals: rawRebuttals.map((r) => ({
                agentId: (r.agent as string) || '',
                agentName: (r.agent as string) || (r.agent_name as string) || 'Agent',
                targetAgentId: '',
                content: (r.content as string) || '',
              })),
              verdict: verdictData ? {
                winner: ((verdictData as Record<string, unknown>).winner as string) ?? undefined,
                summary: ((verdictData as Record<string, unknown>).verdict as string) || ((verdictData as Record<string, unknown>).synthesis as string) || '',
                confidence: undefined,
              } : undefined,
            })
          }
          ps.setStage('debating')
        } else if (eventType === 'react_phase') {
          const _rp = (d?.phase as string) || ''
          // When backend signals "completed", clear the phase to hide the indicator
          ps.setReactPhase(_rp === 'done' ? '' : _rp)
        } else if (eventType === 'planning_start') {
          ps.setStage('planning')
          ps.setReactPhase('plan')  // Show ReAct Loop indicator early
        } else if (eventType === 'planning_done') {
          ps.setStage('executing')
        } else if (eventType === 'memory_retrieval' || eventType === 'xskill_retrieve') {
          // Memory/skill retrieval — add as an XSkill event for "Learning" display
          ps.addXSkillEvent({
            id: `xskill-${idx}-${taskId}`,
            timestamp: eventTs,
            type: 'retrieve',
            skillName: (d?.skill_name as string) || (d?.query as string) || 'Knowledge',
            details: (d?.result as string) || (d?.summary as string) || 'Retrieved',
          })
        } else if (eventType === 'deep_analysis' || eventType === 'deep_analysis_complete') {
          // BUG-FIX: Backend emits "deep_analysis_complete" but previous code only checked "deep_analysis"
          ps.setDeepAnalysis({
            dimensions: safeArray<string>(d?.dimensions),
            assumptions: safeArray<string>(d?.assumptions),
            unknowns: safeArray<string>(d?.unknowns),
            risks: safeArray<string>(d?.risks),
            strategy: (d?.strategy as string),
            complexity_estimate: (d?.complexity_estimate as 'low' | 'medium' | 'high' | 'very_high' | undefined) || (d?.complexity as 'low' | 'medium' | 'high' | 'very_high' | undefined),
            tool_plan: safeArray<string>(d?.tool_plan),
            success_criteria: safeArray<string>(d?.success_criteria),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
          ps.setStage('deep_analysis')
        } else if (eventType === 'self_reflection') {
          ps.addSelfReflection({
            round: (d?.round as number),
            quality_score: (d?.quality_score as number),
            on_track: (d?.on_track as boolean),
            missing_aspects: safeArray<string>(d?.missing_aspects),
            strategy_change: (d?.strategy_change as string),
            improvement_suggestions: safeArray<string>(d?.improvement_suggestions),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
        } else if (eventType === 'auto_verify') {
          ps.addVerification({
            tool_name: (d?.tool_name as string),
            verification_result: (d?.verification_result as 'pass' | 'fail' | 'warning' | undefined),
            issues: safeArray<string>(d?.issues),
            fix_suggestions: safeArray<string>(d?.fix_suggestions),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
        } else if (eventType === 'completion_check') {
          ps.setCompletionCheck({
            quality_score: (d?.quality_score as number),
            missing_items: safeArray<string>(d?.missing_items),
            completeness_pct: (d?.completeness_pct as number),
            verdict: (d?.verdict as 'pass' | 'needs_improvement' | 'fail' | undefined),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
        } else if (eventType === 'mid_analysis') {
          ps.addMidAnalysis({
            round: (d?.round as number),
            on_track: (d?.on_track as boolean),
            strategy_adjustment: (d?.strategy_adjustment as string),
            new_insights: safeArray<string>(d?.new_insights),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
        // NOTE: react_phase already handled at line 823 — no duplicate handler needed
        } else if (eventType === 'debate_critique') {
          ps.addCritique({
            agent_name: (d?.agent_name as string),
            overall_quality: (d?.overall_quality as number),
            critique_points: safeArray<{ severity: string; point: string }>(d?.critique_points),
            missing_perspectives: safeArray<string>(d?.missing_perspectives),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
          ps.setStage('debating')
        } else if (eventType === 'debate_revision') {
          ps.addRevision({
            agent_name: (d?.agent_name as string),
            revised_content: (d?.revised_content as string) || (d?.content as string),
            timestamp: (d?.timestamp as string) || new Date().toISOString(),
          })
          ps.setStage('debating')
        } else if (eventType === 'debate_proposal') {
          // Individual proposal event — aggregate into a full debate round
          // Only add the round on the FIRST proposal we see for this round number
          // to avoid duplicates (the pre-pass already grouped everything).
          const roundNum = (e.round as number) || (d?.round as number) || 1
          const proposals = debateProposalsByRound.get(roundNum) || []
          const rebuttals = debateRebuttalsByRound.get(roundNum) || []
          const verdictData = debateVerdictsByRound.get(roundNum)
          // Check if this is the first proposal event for this round in sequence
          const isFirstForRound = !events.slice(0, idx).some((prev) => {
            if (prev == null || typeof prev !== 'object') return false
            const pt = String(prev.type || prev.event_type || '')
            const pd = (prev.data != null && typeof prev.data === 'object' ? prev.data as Record<string, unknown> : prev) as Record<string, unknown>
            return pt === 'debate_proposal' && ((prev.round as number) || (pd?.round as number) || 1) === roundNum
          })
          if (isFirstForRound && proposals.length > 0) {
            ps.addDebateRound({
              round: roundNum,
              proposals: proposals.map((p) => ({
                agentId: (p.agent_name as string) || '',
                agentName: (p.agent_name as string) || 'Agent',
                agentRole: (p.agent_role as string) || '',
                content: (p.content as string) || '',
              })),
              rebuttals: rebuttals.map((r) => ({
                agentId: (r.agent_name as string) || '',
                agentName: (r.agent_name as string) || 'Agent',
                targetAgentId: '',
                content: (r.content as string) || '',
              })),
              verdict: verdictData ? {
                winner: (verdictData.winner as string) ?? undefined,
                summary: (verdictData.verdict as string) || (verdictData.synthesis as string) || '',
                confidence: undefined,
              } : undefined,
            })
          }
          ps.setStage('debating')
        } else if (eventType === 'debate_rebuttal') {
          // Handled via pre-pass aggregation in debate_proposal handler above
          ps.setStage('debating')
        } else if (eventType === 'debate_verdict') {
          // Verdict was already added with the debate_proposal handler.
          // However, if we never got a debate_proposal (edge case), add round here.
          const roundNum = (e.round as number) || (d?.round as number) || 1
          const proposals = debateProposalsByRound.get(roundNum) || []
          if (proposals.length === 0) {
            // Standalone verdict without proposals (fallback)
            const verdictData = (e.verdict || d?.verdict || {}) as Record<string, unknown>
            ps.addDebateRound({
              round: roundNum,
              proposals: [],
              verdict: {
                winner: (verdictData.winner as string) ?? undefined,
                summary: (verdictData.verdict as string) || (verdictData.synthesis as string) || '',
                confidence: undefined,
              },
            })
          }
          ps.setStage('debating')
        } else if (eventType === 'debate_started' || eventType === 'debate_message' ||
                   eventType === 'debate_concluded' || eventType === 'moderator_intervention' ||
                   eventType === 'debate_turn' || eventType === 'debate_tool_call' ||
                   eventType === 'debate_tool_result' || eventType === 'debate_agent_plan' ||
                   eventType === 'debate_action_phase') {
          // DEBATE-CHAT-FIX: Chat-based debate events — just set stage to 'debating'.
          // The actual rendering is handled by useDebateChat hook which reads from executionStore.
          if (eventType === 'debate_concluded') {
            ps.setStage('synthesizing')
          } else {
            ps.setStage('debating')
          }
        } else if (eventType === 'step') {
          // FULL-HISTORY-FIX: Rebuild progressSteps from persisted step events.
          // These are the "Agent Process" indicator steps (e.g., "Analyzing target",
          // "Scanning ports", "Generating report"). Without this, history replay
          // shows empty Agent Process or just "Analysis Finished".
          const stepData: StepInfo = {
            step: (d?.step as number) || (e.step as number) || idx,
            total_steps: (d?.total_steps as number) || (e.total_steps as number) || 0,
            icon: (d?.icon as string) || (e.icon as string) || '🔄',
            title: (d?.title as string) || (e.title as string) || '',
            description: (d?.description as string) || (e.description as string) || '',
            status: 'done' as const,  // In history replay, all steps are done
          }
          if (stepData.title) {
            setProgressSteps((prev) => {
              const existing = prev.findIndex((s) => s.step === stepData.step)
              if (existing >= 0) {
                const updated = [...prev]
                updated[existing] = stepData
                return updated
              }
              return [...prev, stepData]
            })
          }
        }
        } catch (eventErr) {
          // DEFENSIVE: Skip malformed event, continue processing remaining events
          console.warn('[ChatPanel] populateProcessStore: skipped event at index', idx, eventErr)
        }
      }

      // Only mark as completed if explicitly requested (history replay of finished tasks).
      // If the task is still running on backend, leave processStore active so UI shows progress.
      if (markCompleted) {
        ps.endProcess(true)
      }
    } catch (err) {
      console.warn('[ChatPanel] populateProcessStore error (non-fatal):', err)
    } }

    // Store refs so polling effect can reuse the same mapper + processStore populator
    mapSseToExecEventRef.current = mapSseToExecEvent
    populateProcessStoreRef.current = populateProcessStore

    const loadExecutionHistory = async () => {
      // FULL-HISTORY-FIX: ALWAYS use DB (/tasks/{id}/events) as PRIMARY source.
      // metadata.execution_events is truncated to ~500KB and drops events for long tasks.
      // DB has ALL persisted events (tool_done, self_reflection, etc. are now HIGH_PRIORITY).
      // Fall back to metadata only if DB fetch fails or returns empty.
      const currentMessages = useSessionStore.getState().messagesBySession[sessionId] || []
      const assistantMsgs = currentMessages.filter((m) => m.role === 'assistant' && m.metadata?.execution_events)


      if (assistantMsgs.length > 0) {
        let allEvents: Array<Record<string, unknown>> = []
        const firstMsgTime = assistantMsgs[0]?.timestamp
          ? new Date(assistantMsgs[0].timestamp).getTime()
          : 0

        const lastAssistantMsg = assistantMsgs[assistantMsgs.length - 1]
        const taskIdFromMeta = (lastAssistantMsg.metadata?.task_id as string) || ''

        // PRIMARY: Always try DB events first (complete, not truncated)
        if (taskIdFromMeta && !taskIdFromMeta.startsWith('msg-')) {
          try {
            const fullEvents = await apiGet<Array<{
              event_type: string
              data: Record<string, unknown>
              created_at: string
              sequence?: number
              started_at?: number
            }>>(`/tasks/${taskIdFromMeta}/events`)
            if (fullEvents.length > 0) {
              allEvents = fullEvents.map((e) => ({
                ...e.data,
                type: e.event_type,
                event_type: e.event_type,
                created_at: e.created_at,
                sequence: e.sequence,
                started_at: e.started_at,
              }))
            }
          } catch {
            // API failed — fall through to metadata below
          }
        }

        // FALLBACK: use metadata events (may be truncated for long tasks)
        if (allEvents.length === 0) {
          for (const msg of assistantMsgs) {
            const events = msg.metadata?.execution_events as Array<Record<string, unknown>> | undefined
            if (events && Array.isArray(events)) {
              allEvents.push(...events)
            }
          }
        }

        if (allEvents.length > 0) {
          // DEDUP-FIX: Remove consecutive duplicate events that may exist in stored metadata.
          // Backend had a bug where _direct_event_callback AND _handle_event both appended
          // to collected_events, causing every event to appear 2x. This filter handles old data.
          const dedupedEvents: Array<Record<string, unknown>> = []
          for (const evt of allEvents) {
            if (dedupedEvents.length === 0) {
              dedupedEvents.push(evt)
              continue
            }
            const prev = dedupedEvents[dedupedEvents.length - 1]
            const evtType = String(evt.type || evt.event_type || '')
            const prevType = String(prev.type || prev.event_type || '')
            // Skip if same type + same agent + same tool/content (consecutive duplicate)
            if (evtType === prevType &&
                (evt.agent_name || '') === (prev.agent_name || '') &&
                (evt.tool || '') === (prev.tool || '') &&
                (evt.content || '') === (prev.content || '')) {
              continue // Skip duplicate
            }
            dedupedEvents.push(evt)
          }

          // NOISE-FILTER: Remove low-value events that pollute timeline display
          const NOISE_TYPES = new Set(['token', 'done', 'error', 'usage', 'heartbeat', 'pong', 'ping'])
          const meaningfulEvents = dedupedEvents.filter((e) => {
            const t = String((e.type as string) || (e.event_type as string) || '')
            return !NOISE_TYPES.has(t)
          })
          const execEvents: ExecutionEvent[] = meaningfulEvents.map((e, idx) =>
            mapSseToExecEvent(e, idx, sessionId, firstMsgTime)
          )
          setExecutionEvents(sessionId, execEvents)
          setExecutionActive(sessionId, false)

          // Populate processStore for full process view
          const taskId = taskIdFromMeta || `msg-${sessionId}`

          // Check if task is still running via session state (in-memory, no DB side effects).
          // Previously used GET /tasks/{id} which had a side-effect of auto-failing tasks >15min.
          let taskStillRunning = false
          try {
            const sessionState = await apiGet<{ is_running: boolean; task_id?: string }>(
              `/sessions/${sessionId}/state`
            )
            taskStillRunning = sessionState.is_running && sessionState.task_id === taskId
          } catch {
            // State check failed → treat as completed (conservative)
          }
          populateProcessStore(dedupedEvents, taskId, !taskStillRunning, firstMsgTime)


          // Restore agents from message metadata.agents_spawned
          // This ensures agent info survives F5 / history navigation
          for (const msg of assistantMsgs) {
            const spawnedAgents = msg.metadata?.agents_spawned as Array<{ name: string; role: string; id?: string }> | undefined
            if (spawnedAgents && Array.isArray(spawnedAgents)) {
              const ps = useProcessStore.getState()
              for (const agent of spawnedAgents) {
                ps.addAgent({
                  id: agent.id || `restored-${agent.name}`,
                  name: agent.name,
                  role: agent.role,
                  status: taskStillRunning ? 'active' : 'completed',
                  spawnedAt: msg.timestamp ? new Date(msg.timestamp).getTime() : Date.now(),
                })
              }
            }
          }

          // If task is still running, keep execution trace active for live updates.
          // F5-RECOVERY: setExecutionActive(true) triggers the polling effect
          // (lines ~810+) which polls /tasks/{id} every 2s. When the task
          // completes, polling reloads messages and clears the active state.
          // NOTE: Do NOT set isProcessing=true here — that would block polling
          // (polling skips when isProcessing is true, because SSE is active).
          // The GlobalProcessBar + UnifiedProcessPanel already show progress
          // based on processStore stage, so user sees "Executing..." indicator.
          if (taskStillRunning) {
            setExecutionActive(sessionId, true)
            setStatusText('Resuming task progress...')
          }

          // Replay swarm_round events into inlineDebateRounds
          const swarmRoundEvents = execEvents.filter(
            (ev): ev is SwarmRoundEvent => ev.type === 'swarm_round' && (ev as SwarmRoundEvent).proposals?.length > 0,
          )
          if (swarmRoundEvents.length > 0) {
            setInlineDebateRounds(swarmRoundEvents)
            inlineDebateRoundsRef.current = swarmRoundEvents
          } else {
            // No swarm_round events — try to build from individual debate_proposal events
            const proposalEvents = execEvents.filter((ev) => ev.type === 'debate_proposal') as Array<{ round: number; total_rounds: number; agent_name: string; agent_role: string; content: string; tools_used: string[]; timestamp: number; id: string; type: string }>
            if (proposalEvents.length > 0) {
              const rebuttalEvents = execEvents.filter((ev) => ev.type === 'debate_rebuttal') as Array<{ round: number; agent_name: string; content: string; id: string; type: string }>
              const verdictEvents = execEvents.filter((ev) => ev.type === 'debate_verdict') as Array<{ round: number; verdict: Record<string, unknown>; id: string; type: string }>
              // Group by round
              const roundNums = [...new Set(proposalEvents.map((p) => p.round))]
              const builtRounds: SwarmRoundEvent[] = roundNums.map((rn) => {
                const rProposals = proposalEvents.filter((p) => p.round === rn)
                const rRebuttals = rebuttalEvents.filter((r) => r.round === rn)
                const rVerdict = verdictEvents.find((v) => v.round === rn)
                return {
                  id: `built-round-${rn}`,
                  type: 'swarm_round' as const,
                  round: rn,
                  total_rounds: rProposals[0]?.total_rounds || roundNums.length,
                  proposals: rProposals.map((p) => ({
                    agent: p.agent_name || 'Agent',
                    role: p.agent_role || '',
                    content: p.content || '',
                    tools_used: p.tools_used || [],
                  })),
                  rebuttals: rRebuttals.map((r) => ({
                    agent: r.agent_name || 'Agent',
                    content: r.content || '',
                  })),
                  verdict: rVerdict ? {
                    verdict: (rVerdict.verdict?.verdict as string) || undefined,
                    winner: (rVerdict.verdict?.winner as string) || null,
                    synthesis: (rVerdict.verdict?.synthesis as string) || '',
                    reasoning: (rVerdict.verdict?.reasoning as string) || '',
                  } : undefined,
                  timestamp: rProposals[0]?.timestamp || Date.now(),
                }
              })
              if (builtRounds.length > 0) {
                setInlineDebateRounds(builtRounds)
                inlineDebateRoundsRef.current = builtRounds
              }
            }
          }
          return // Done — no need for task events API fallback
        }
      }

      // ── FALLBACK: task events API (backward compat for older sessions) ──
      try {
        const tasks = await apiGet<Array<{ id: string; status: string; title: string }>>(
          `/tasks/?session_id=${sessionId}&limit=5`
        )

        const runningTask = tasks.find((t) =>
          ['executing', 'planning', 'decomposing'].includes(t.status)
        ) || tasks.find((t) => t.status === 'completed') || tasks[0]

        if (runningTask) {
          const events = await apiGet<Array<{
            event_type: string
            data: Record<string, unknown>
            created_at: string
            sequence?: number
          }>>(`/tasks/${runningTask.id}/events`)
          // Sort by sequence for deterministic ordering
          events.sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))

          if (events.length > 0) {
            // Map task events (which have event_type + data structure) to ExecutionEvents
            const execEvents: ExecutionEvent[] = events.map((e, idx) =>
              mapSseToExecEvent({ ...e.data, event_type: e.event_type, created_at: e.created_at, sequence: e.sequence ?? idx }, idx, runningTask.id)
            )
            setExecutionEvents(sessionId, execEvents)
            setExecutionActive(sessionId, ['executing', 'planning', 'decomposing'].includes(runningTask.status))

            // Populate processStore from task events
            const isRunning = ['executing', 'planning', 'decomposing'].includes(runningTask.status)
            const flatEvents = events.map((e) => ({
              ...e.data,
              type: e.event_type,
              event_type: e.event_type,
              created_at: e.created_at,
              sequence: e.sequence,
            }))
            const firstEventTime = events[0]?.created_at
              ? new Date(events[0].created_at).getTime()
              : undefined
            populateProcessStore(flatEvents, runningTask.id, !isRunning, firstEventTime)

            // Replay swarm_round events into inlineDebateRounds
            const swarmRoundEvents = execEvents.filter(
              (ev): ev is SwarmRoundEvent => ev.type === 'swarm_round' && (ev as SwarmRoundEvent).proposals?.length > 0,
            )
            if (swarmRoundEvents.length > 0) {
              setInlineDebateRounds(swarmRoundEvents)
              inlineDebateRoundsRef.current = swarmRoundEvents
            } else {
              // No swarm_round events — try to build from individual debate_proposal events
              const proposalEvents = execEvents.filter((ev) => ev.type === 'debate_proposal') as Array<{ round: number; total_rounds: number; agent_name: string; agent_role: string; content: string; tools_used: string[]; timestamp: number; id: string; type: string }>
              if (proposalEvents.length > 0) {
                const rebuttalEvents = execEvents.filter((ev) => ev.type === 'debate_rebuttal') as Array<{ round: number; agent_name: string; content: string; id: string; type: string }>
                const verdictEvents = execEvents.filter((ev) => ev.type === 'debate_verdict') as Array<{ round: number; verdict: Record<string, unknown>; id: string; type: string }>
                const roundNums = [...new Set(proposalEvents.map((p) => p.round))]
                const builtRounds: SwarmRoundEvent[] = roundNums.map((rn) => {
                  const rProposals = proposalEvents.filter((p) => p.round === rn)
                  const rRebuttals = rebuttalEvents.filter((r) => r.round === rn)
                  const rVerdict = verdictEvents.find((v) => v.round === rn)
                  return {
                    id: `built-round-${rn}`,
                    type: 'swarm_round' as const,
                    round: rn,
                    total_rounds: rProposals[0]?.total_rounds || roundNums.length,
                    proposals: rProposals.map((p) => ({
                      agent: p.agent_name || 'Agent',
                      role: p.agent_role || '',
                      content: p.content || '',
                      tools_used: p.tools_used || [],
                    })),
                    rebuttals: rRebuttals.map((r) => ({
                      agent: r.agent_name || 'Agent',
                      content: r.content || '',
                    })),
                    verdict: rVerdict ? {
                      verdict: (rVerdict.verdict?.verdict as string) || undefined,
                      winner: (rVerdict.verdict?.winner as string) || null,
                      synthesis: (rVerdict.verdict?.synthesis as string) || '',
                      reasoning: (rVerdict.verdict?.reasoning as string) || '',
                    } : undefined,
                    timestamp: rProposals[0]?.timestamp || Date.now(),
                  }
                })
                if (builtRounds.length > 0) {
                  setInlineDebateRounds(builtRounds)
                  inlineDebateRoundsRef.current = builtRounds
                }
              }
            }
          }
        }
      } catch {
        // Silent — events not available
      }
    }

    // Wait until messages are loaded from DB before trying to extract execution_events.
    // Uses event-driven approach: subscribe to store changes instead of polling.
    const tryLoad = async () => {
      // Ensure messages are being loaded
      const currentState = useSessionStore.getState()
      if (!currentState.messagesBySession[sessionId] && currentState.loadingSessionId !== sessionId) {
        currentState.loadSessionMessages(sessionId)
      }

      // Fast check: messages already available
      const fastCheck = useSessionStore.getState()
      const fastMsgs = fastCheck.messagesBySession[sessionId]
      if (fastMsgs && fastMsgs.length > 0) {
        await loadExecutionHistory()
        return
      }
      if (fastCheck.loadingSessionId === null && fastMsgs !== undefined) {
        await loadExecutionHistory()
        return
      }

      // Wait for store to finish loading (event-driven with timeout fallback)
      await new Promise<void>((resolve) => {
        let resolved = false
        const unsub = useSessionStore.subscribe((state) => {
          if (resolved) return
          const msgs = state.messagesBySession[sessionId]
          if (msgs !== undefined || state.loadingSessionId === null) {
            resolved = true
            unsub()
            resolve()
          }
        })
        // Safety timeout: 5s max wait, then proceed anyway
        setTimeout(() => {
          if (!resolved) {
            resolved = true
            unsub()
            resolve()
          }
        }, 5000)
      })
      await loadExecutionHistory()

      // ── F5-RECOVERY: Task-based discovery (Real-time State Sync) ──
      // If the last message is "user" with no assistant reply, the orchestrator
      // may still be running. Instead of blindly polling for the assistant message,
      // query the tasks API to find any running task for this session and display
      // live progress immediately.

      // Guard: if loadExecutionHistory already populated processStore with active progress,
      // skip F5-Recovery (it would incorrectly overwrite live progress)
      const psState = useProcessStore.getState()
      if (psState.stage && psState.stage !== 'idle' && psState.stage !== 'completed') {
        console.log('[F5-Recovery] Skipping — processStore already active with stage:', psState.stage)
        return
      }

      const postLoadMsgs = useSessionStore.getState().messagesBySession[sessionId] || []
      if (postLoadMsgs.length > 0) {
        const lastMsg = postLoadMsgs[postLoadMsgs.length - 1]
        if (lastMsg.role === 'user') {
          console.log('[F5-Recovery] Detected orphan user message  - querying running tasks')

          // Check authoritative session state (in-memory) before falling back to DB query
          try {
            const sessionState = await apiGet<{ is_running: boolean; task_id?: string }>(
              `/sessions/${sessionId}/state`
            )
            if (sessionState.is_running && sessionState.task_id) {
              console.log('[F5-Recovery] Session state reports task still running:', sessionState.task_id)
              // Task IS running — activate live progress instead of entering fallback
              const ps = useProcessStore.getState()
              ps.startProcess(sessionState.task_id, sessionId)
              ps.setStage('executing')
              persistTaskMapping(sessionId, sessionState.task_id)
              setExecutionActive(sessionId, true)
              return
            }
          } catch (e) {
            console.warn('[F5-Recovery] Failed to check session state, continuing with task query', e)
          }

          try {
            // Query running tasks for this session
            const tasks = await apiGet<Array<{
              id: string
              title: string
              status: string
              created_at?: string
            }>>(`/tasks/?session_id=${sessionId}&status=executing`)

            const runningTask = tasks[0]
            if (runningTask) {
              // TRUST-BACKEND: If backend says task is executing, trust it — regardless of age.
              // Tasks can legitimately run for hours/days (research, multi-step agents).
              // Frontend NEVER declares a task dead based on time — only backend status matters.
              {
                console.log(`[F5-Recovery] Found running task ${runningTask.id}  - activating live progress`)
                // Activate execution tracking immediately
                // NOTE: Do NOT set isProcessing=true — that flag means "active SSE stream"
                // and would block the polling effect. We want polling to handle F5 recovery.
                const ps = useProcessStore.getState()
                ps.startProcess(runningTask.id, sessionId)
                ps.setStage('executing')
                persistTaskMapping(sessionId, runningTask.id)
                setExecutionActive(sessionId, true)

                // Load any events already flushed to DB (periodic flush every 5s)
                try {
                  const events = await apiGet<Array<{
                    event_type: string
                    data: Record<string, unknown>
                    created_at: string
                    sequence?: number
                  }>>(`/tasks/${runningTask.id}/events`)

                  if (events.length > 0) {
                    events.sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
                    const mapper = mapSseToExecEventRef.current
                    if (mapper) {
                      const execEvents: ExecutionEvent[] = events.map((e, idx) =>
                        mapper(
                          { ...e.data, event_type: e.event_type, created_at: e.created_at, sequence: e.sequence ?? idx },
                          idx, runningTask.id,
                        )
                      )
                      setExecutionEvents(sessionId, execEvents)
                    }
                    // Populate processStore from task events
                    const flatEvents = events.map((e) => ({
                      ...e.data, type: e.event_type, event_type: e.event_type,
                      created_at: e.created_at, sequence: e.sequence,
                    }))
                    const firstEventTime = events[0]?.created_at
                      ? new Date(events[0].created_at).getTime()
                      : undefined
                    populateProcessStore(flatEvents, runningTask.id, false, firstEventTime)
                  }
                } catch {
                  // Event loading failure is non-critical — polling effect will pick up
                }
                // The polling effect (below) will automatically continue updating
                // events every 2s since executionActive is now true.
              }
            } else {
              // No running task — check if there's a resumable (suspended/crashed) task
              try {
                const resumableData = await apiGet<{ tasks: Array<{ task_id: string; turn_number: number; session_id: string }> }>(`/tasks/resumable?session_id=${sessionId}`)
                if (resumableData.tasks?.length > 0) {
                  const rt = resumableData.tasks[0]
                  setCanResume(true)
                  setResumeInfo({ turn: rt.turn_number, taskId: rt.task_id })
                  console.log(`[F5-Recovery] Found resumable task ${rt.task_id} at turn ${rt.turn_number}`)
                }
              } catch { /* non-critical */ }

              // If there's an orphan user message with no assistant reply,
              // either the task failed/was cancelled (backend handled it)
              // or the message just hasn't been persisted yet.
              // Retry with increasing delay to handle persist latency + slow task startup.
              console.log('[F5-Recovery] No running task found — retrying with backoff')
              const retryDelays = [1500, 3000, 5000]
              for (const delay of retryDelays) {
                await new Promise(r => setTimeout(r, delay))
                // Re-check session state — task may have started during delay
                try {
                  const retryState = await apiGet<{ is_running: boolean; task_id?: string }>(
                    `/sessions/${sessionId}/state`
                  )
                  if (retryState.is_running && retryState.task_id) {
                    console.log(`[F5-Recovery] Task appeared on retry: ${retryState.task_id}`)
                    const ps = useProcessStore.getState()
                    ps.startProcess(retryState.task_id, sessionId)
                    ps.setStage('executing')
                    persistTaskMapping(sessionId, retryState.task_id)
                    setExecutionActive(sessionId, true)
                    return
                  }
                } catch { /* continue retry loop */ }
              }
              await useSessionStore.getState().loadSessionMessages(sessionId)

              // If still no assistant message, try task.result as data recovery (no fake messages)
              const retryMsgs = useSessionStore.getState().messagesBySession[sessionId] || []
              const hasAssistantMsg = retryMsgs.some(m => m.role === 'assistant' && m.content)
              if (!hasAssistantMsg) {
                try {
                  const allTasks = await apiGet<Array<{
                    id: string
                    status: string
                  }>>(`/tasks/?session_id=${sessionId}&limit=5`)
                  const completedTask = allTasks.find(t => t.status === 'completed' || t.status === 'failed')
                  if (completedTask) {
                    const taskDetail = await apiGet<{
                      id: string
                      status: string
                      result?: { content?: string }
                    }>(`/tasks/${completedTask.id}`)
                    if (taskDetail.result?.content) {
                      // Found task result — display as assistant message (real data, not fake)
                      addMessage(sessionId, {
                        id: crypto.randomUUID(),
                        role: 'assistant',
                        content: taskDetail.result.content,
                        timestamp: new Date().toISOString(),
                        metadata: { recovered_from_task: completedTask.id },
                      })
                      console.log(`[F5-Recovery] Recovered from task.result: ${completedTask.id}`)
                    }
                    // If result.content is empty — do NOT create fake message.
                    // Trust that backend state is correct. User can see history.
                  }
                } catch {
                  // Task API failed — silently skip
                  console.warn('[F5-Recovery] Task API failed during recovery — skipping')
                }
              }
              await loadExecutionHistory()
            }
          } catch {
            // Tasks API failed — fallback to simple message retry
            console.log('[F5-Recovery] Tasks API failed  - fallback message retry')
            await new Promise(r => setTimeout(r, 3000))
            await useSessionStore.getState().loadSessionMessages(sessionId)
            await loadExecutionHistory()
          }
        }
      }

    }
    tryLoad()
  }, [sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── CONSOLIDATED: F5-recovery now uses pollingTaskId → useTaskPolling ──
  // When executionActive is set (by tryLoad above), activate useTaskPolling
  // to resume event delivery. This replaces the old 1s manual polling effect.
  useEffect(() => {
    if (!executionActive || isProcessing || pollingTaskId) return

    // Get taskId from processStore
    const psState = useProcessStore.getState()
    const taskId = psState.focusedTaskId || psState.taskId
    if (!taskId || taskId.startsWith('msg-')) return

    // Activate useTaskPolling for this task
    console.info('[ChatPanel] F5-recovery: activating useTaskPolling for task:', taskId)
    setPollingTaskId(taskId)
  }, [sessionId, isProcessing, executionActive, pollingTaskId])

  // Persist session info to sessionStorage for fast reconnect after reload
  useEffect(() => {
    if (sessionId) {
      sessionStorage.setItem('nexus_lastActiveSessionId', sessionId)
    }
    const psState = useProcessStore.getState()
    const taskId = psState.focusedTaskId || psState.taskId
    if (taskId && !taskId.startsWith('msg-')) {
      sessionStorage.setItem(`nexus_lastTaskId_${sessionId}`, taskId)
    }
  }, [sessionId, pollingTaskId])

  // ── File handling ───────────────────────────────────────────

  const handleFileSelect = useCallback((files: File[]) => {
    if (files.length === 0) return
    const newFiles: AttachedFile[] = files.map((file) => ({
      file,
      preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
    }))
    setAttachedFiles((prev) => [...prev, ...newFiles].slice(0, 10))
  }, [])

  const handleFileRemove = useCallback((index: number) => {
    setAttachedFiles((prev) => {
      const updated = [...prev]
      if (updated[index]?.preview) URL.revokeObjectURL(updated[index].preview!)
      updated.splice(index, 1)
      return updated
    })
  }, [])

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items)
    const imageItems = items.filter((item) => item.type.startsWith('image/'))
    if (imageItems.length > 0) {
      e.preventDefault()
      const files = imageItems.map((item, i) => {
        const blob = item.getAsFile()!
        return new File([blob], `clipboard-${Date.now()}-${i}.png`, { type: blob.type })
      })
      const newFiles: AttachedFile[] = files.map((file) => ({
        file,
        preview: URL.createObjectURL(file),
      }))
      setAttachedFiles((prev) => [...prev, ...newFiles].slice(0, 10))
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return
    const newFiles: AttachedFile[] = files.map((file) => ({
      file,
      preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
    }))
    setAttachedFiles((prev) => [...prev, ...newFiles].slice(0, 10))
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
  }, [])

  // ── Cancel streaming ────────────────────────────────────────

  const cancelStream = useCallback(() => {
    // Cancel the backend task; the real terminal state arrives via polling.
    const ps = useProcessStore.getState()
    const realTaskId = ps.taskId
    if (realTaskId && !realTaskId.startsWith('msg-')) {
      apiPost(`/tasks/${realTaskId}/cancel`, {}).catch(() => {
        // Best-effort: task may have already completed
      })
    }
    // Show a "cancelling" affordance but KEEP polling until the backend writes
    // a terminal task_cancelled event — otherwise the UI would claim the task
    // stopped while the owning worker is still draining.
    setStatusText('Đang hủy nhiệm vụ…')
  }, [])

  // ── Send message (SSE streaming) ────────────────────────────

  const sendMessage = async (overrideMessage?: string) => {
    const effectiveInput = overrideMessage ?? input
    if ((!effectiveInput.trim() && attachedFiles.length === 0) || isProcessing) return

    // UI-01: Track session in processStore (conditional reset on switch)
    useProcessStore.getState().setSession(sessionId)

    // UI-03: Clear retry state on new send
    setCanRetry(false)

    // Upload attached files
    let contextFromFiles = ''
    let fileNames: string[] = []
    if (attachedFiles.length > 0) {
      setIsUploading(true)
      setStatusText('Đang upload và xử lý tài liệu...')
      try {
        const result: UploadResult = await uploadAttachments(attachedFiles.map((af) => af.file), sessionId)
        contextFromFiles = result.context
        fileNames = result.files.filter((f) => f.success).map((f) => f.filename)
        const errors = result.files.filter((f) => !f.success)
        if (errors.length > 0) {
          const errorMsg = errors.map((e) => `${e.filename}: ${e.error}`).join('\n')
          addMessage(sessionId, {
            id: crypto.randomUUID(),
            role: 'system',
            content: `⚠️ Upload errors:\n${errorMsg}`,
            timestamp: new Date().toISOString(),
          })
        }
      } catch (err) {
        addMessage(sessionId, {
          id: crypto.randomUUID(),
          role: 'system',
          content: `Upload failed: ${err}`,
          timestamp: new Date().toISOString(),
        })
        setIsUploading(false)
        setStatusText('')
        return
      }
      setIsUploading(false)
    }

    // Compose message
    const messageText = effectiveInput.trim()
    lastSentMessageRef.current = messageText // UI-03: save for retry
    const displayContent =
      fileNames.length > 0
        ? `${messageText}${messageText ? '\n\n' : ''}📎 ${fileNames.join(', ')}`
        : messageText

    const fullMessage = contextFromFiles
      ? `${messageText}\n\n--- Attached Documents ---\n${contextFromFiles}`
      : messageText

    // Add user message to store
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: displayContent,
      timestamp: new Date().toISOString(),
      metadata: fileNames.length > 0 ? { attachments: fileNames } : undefined,
    }
    addMessage(sessionId, userMessage)

    // AUDIT-FIX BUG-1: Force scroll to bottom when USER sends a message.
    // Previously, if user was scrolled up, their own new message wouldn't be visible.
    // User expectation: "when I send, I should see my message immediately"
    userScrolledUpRef.current = false
    setUnreadCount(0)
    setShowScrollBottom(false)
    scrollToBottom(true, true) // instant=true, force=true

    // TAB-TITLE-FIX: Update tab title with first user message content
    // Only update if tab still shows default "New Chat" title
    const tabState = useTabStore.getState()
    const currentTab = tabState.tabs.find(t => t.sessionId === sessionId)
    if (currentTab && (currentTab.title === 'New Chat' || currentTab.title === '')) {
      const shortTitle = messageText.slice(0, 60).replace(/\n/g, ' ').trim() || 'New Chat'
      tabState.updateTabTitle(currentTab.id, shortTitle)
    }

    // Reset UI state
    // FIX-POLL-LEAK: Clear kill flag so new task can use background poll if needed
    pollKilledRef.current = false
    setIsProcessing(true)
    setIsStreaming(true)
    setStreamingContent('')
    clearExecutionSession(sessionId)
    setExecutionActive(sessionId, true)
    processingStartTimeRef.current = Date.now()
    toolsUsedRef.current = new Set()
    inlineDebateRoundsRef.current = []
    setInlineDebateRounds([])
    setLiveDebate(null)
    setPendingDecision(null)
    setStatusText('Thinking...')
    setInput('')
    setAttachedFiles([])
    // Clear previous task's progress steps (kept for completed state display until new task starts)
    setProgressSteps([])
    setAgentActivities([])

    // Start process tracking
    const taskId = crypto.randomUUID()
    const processState = useProcessStore.getState()
    processState.startProcess(taskId, sessionId)

    // ── ARCHITECTURE: DB-first polling, no SSE/WS dependency ──────
    // Instead of SSE streaming, we POST to /chat/send (fire-and-forget)
    // which starts a background task and returns task_id immediately.
    // The useTaskPolling hook then polls GET /tasks/{task_id}/events
    // every 2s to receive events from DB (single source of truth).
    try {
      const response = await sendChatMessage(fullMessage, sessionId, selectedModel || undefined)
      const realTaskId = response.task_id

      // Update process store with real backend task_id
      useProcessStore.getState().setRealTaskId(realTaskId)
      persistTaskMapping(sessionId, realTaskId)

      // Activate polling — useTaskPolling hook will start fetching events
      setPollingTaskId(realTaskId)
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : 'Failed to send message'
      setIsProcessing(false)
      setIsStreaming(false)
      setStatusText('')
      setExecutionActive(sessionId, false)
      useProcessStore.getState().endProcess(false, errMsg)
      addMessage(sessionId, {
        id: crypto.randomUUID(),
        role: 'system',
        content: `❌ Error: ${errMsg}`,
        timestamp: new Date().toISOString(),
      })
      addNotification('error', 'Send Failed', errMsg)
      setCanRetry(!!lastSentMessageRef.current)
    }
  }

  // ── Retry handler (for MessageBubble error messages) ────────

  const handleRetry = useCallback((errorContent: string) => {
    // Find the previous user message before the error
    const errorMsgIndex = messages.findIndex((m) => m.content === errorContent && m.role === 'system')
    if (errorMsgIndex > 0) {
      const prevUserMsg = messages.slice(0, errorMsgIndex).reverse().find((m) => m.role === 'user')
      if (prevUserMsg) {
        setInput(prevUserMsg.content)
      }
    }
  }, [messages])

  // PERF: Memoized callbacks for extracted sub-components
  const handleDecisionResolved = useCallback(() => {
    setPendingDecision(null)
    setStatusText('Agent đang tiếp tục xử lý...')
  }, [])

  const handleRetryLast = useCallback(() => {
    setCanRetry(false)
    sendMessage(lastSentMessageRef.current)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleResumeTask = useCallback(async () => {
    if (!resumeInfo) return
    setCanResume(false)
    setResumeInfo(null)
    try {
      const resp = await fetch(`/api/v1/tasks/${resumeInfo.taskId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      const data = await resp.json()
      if (data.status === 'resumed') {
        setPollingTaskId(resumeInfo.taskId)
        setIsProcessing(true)
        setStatusText(`Đang tiếp tục từ turn ${data.resumed_from_turn}...`)
      }
    } catch (err) {
      console.error('Resume failed:', err)
      setCanResume(true)
      setResumeInfo(resumeInfo)
    }
  }, [resumeInfo]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load older messages ─────────────────────────────────────

  const loadOlderMessages = useCallback(async () => {
    if (loadingMore) return
    setLoadingMore(true)
    try {
      const PAGE_SIZE = 200
      const currentCount = messages.length
      // Fetch next page of older messages (offset = current count)
      const olderMessages = await getSessionMessages(sessionId, currentCount, PAGE_SIZE)

      if (olderMessages.length === 0) {
        setHasMore(sessionId, false)
        return
      }

      // Convert SessionMessage[] to ChatMessage[] (same as loadSessionMessages)
      const chatMessages: ChatMessage[] = olderMessages
        .filter((m) => m.role !== 'title')
        .map((m) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant' | 'system',
          content: m.content,
          timestamp: m.created_at,
          ...(m.metadata ? { metadata: m.metadata as ChatMessage['metadata'] } : {}),
        }))

      if (chatMessages.length > 0) {
        prependMessages(sessionId, chatMessages)
      }

      // If fewer than PAGE_SIZE returned, no more pages exist
      setHasMore(sessionId, chatMessages.length >= PAGE_SIZE)
    } catch {
      // Silent fail
    } finally {
      setLoadingMore(false)
    }
  }, [loadingMore, messages.length, sessionId, setHasMore, prependMessages])

  // ── Render ──────────────────────────────────────────────────

  return (
    <div className="relative flex h-full min-h-0 w-full">
      {/* Main Chat Area */}
      <div
        className="relative flex flex-col h-full min-h-0 w-full"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        {/* Messages Area — scrollable */}
        <div className="relative flex-1 min-h-0">
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            className="h-full overflow-y-auto px-4 sm:px-6 py-6 scroll-smooth"
          >
          {/* Load more indicator */}
          {hasMore && (
            <div className="flex justify-center py-3">
              <button
                onClick={loadOlderMessages}
                disabled={loadingMore}
                className="text-[12px] text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 font-medium flex items-center gap-1.5"
              >
                {loadingMore ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Loading...
                  </>
                ) : (
                  '↑ Load older messages'
                )}
              </button>
            </div>
          )}

          {/* Empty State */}
          {messages.length === 0 && !isProcessing && (
            <div className="flex flex-col items-center justify-center min-h-[65vh] animate-fade-in">
              <div className="relative mb-8">
                <div className="w-[72px] h-[72px] rounded-[20px] bg-gradient-to-br from-[#1456f0] via-[#3b82f6] to-[#818cf8] flex items-center justify-center shadow-[0_8px_30px_rgba(59,130,246,0.25)]">
                  <Sparkles className="w-8 h-8 text-white" strokeWidth={1.5} />
                </div>
                <div className="absolute -top-1 -right-1 w-5 h-5 bg-emerald-400 border-[3px] border-white dark:border-[#0f0f1e] rounded-full shadow-sm" />
              </div>
              <h2 className="font-display text-[28px] font-semibold text-text-dark dark:text-gray-100 text-center tracking-[-0.02em]">
                What can I help you with?
              </h2>
              <p className="text-[15px] text-text-secondary dark:text-gray-400 text-center mt-2.5 max-w-[420px] leading-[1.6]">
                Core Agent sẽ spawn team AI, tranh luận debate, tìm kiếm web, viết code, và trả kết quả tốt nhất.
              </p>
              <div className="mt-4 flex items-center gap-2 text-[12px] text-text-muted dark:text-gray-500">
                <Paperclip className="w-3.5 h-3.5" />
                <span>Đính kèm PDF, DOCX, ảnh, CSV... bằng nút 📎 hoặc kéo thả</span>
              </div>
              <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-[520px]">
                {EXAMPLE_PROMPTS.map((example) => (
                  <button
                    key={example.text}
                    onClick={() => setInput(example.text)}
                    className={cn(
                      'group flex items-center gap-3 px-4 py-3 rounded-[12px] border transition-all duration-200 text-left',
                      'bg-white border-[#e5e7eb] hover:border-[#bfdbfe] hover:bg-[#f8faff] hover:shadow-[0_2px_8px_rgba(59,130,246,0.08)]',
                      'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:hover:border-[#3b82f6]/40 dark:hover:bg-[#1e1e35]',
                    )}
                  >
                    <span className="text-lg shrink-0">{example.icon}</span>
                    <span className="text-[13px] text-text-secondary dark:text-gray-400 group-hover:text-text-dark dark:group-hover:text-gray-200 transition-colors font-medium">
                      {example.text}
                    </span>
                    <ExternalLink className="w-3.5 h-3.5 text-text-muted opacity-0 group-hover:opacity-100 transition-opacity ml-auto shrink-0" />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* PERF: Message list — virtualized via ChatMessageList for large conversations */}
          <ChatMessageList
            messages={messages}
            sessionId={sessionId}
            onRetry={handleRetry}
            scrollContainerRef={scrollContainerRef}
            userScrolledUp={userScrolledUpRef.current}
          />

          {/* PERF: Processing status — isolated to prevent message list re-renders */}
          <div className="space-y-6">
            <ChatProcessingStatus
              sessionId={sessionId}
              isProcessing={isProcessing}
              processStageActive={processStageActive}
              executionActive={executionActive}
              progressSteps={progressSteps}
              agentActivities={agentActivities}
              statusText={statusText}
              liveDebate={liveDebate}
              inlineDebateRounds={inlineDebateRounds}
              pendingDecision={pendingDecision}
              onDecisionResolved={handleDecisionResolved}
              canRetry={canRetry}
              onRetry={handleRetryLast}
              canResume={canResume}
              onResume={handleResumeTask}
              resumeInfo={resumeInfo}
            />

            {/* PERF: Streaming content — isolated so token updates don't re-render messages */}
            <ChatStreamingContent
              isStreaming={isStreaming}
              streamingContent={streamingContent}
            />

            <div ref={messagesEndRef} />
          </div>
          </div>

          {/* Scroll-to-Top Button — glassmorphism + smooth enter/exit transitions */}
          <button
            onClick={() => {
              const el = scrollContainerRef.current
              if (el) {
                el.scrollTo({ top: 0, behavior: 'smooth' })
                userScrolledUpRef.current = true
              }
            }}
            className={cn(
              'chat-scroll-btn-base absolute top-4 right-4 z-10 w-11 h-11 rounded-full flex items-center justify-center group',
              showScrollTop ? 'chat-scroll-btn-visible' : 'chat-scroll-btn-hidden',
            )}
            title="Scroll to top"
            aria-label="Scroll to top"
            tabIndex={showScrollTop ? 0 : -1}
          >
            <ChevronUp className="w-[18px] h-[18px] text-[#6b7280] dark:text-[#a0a0b8] group-hover:text-[#3b82f6] dark:group-hover:text-[#60a5fa] transition-colors duration-200" strokeWidth={2.5} />
          </button>

          {/* Scroll-to-Bottom Button — glassmorphism + progress ring + unread badge */}
          <button
            onClick={() => {
              userScrolledUpRef.current = false
              setUnreadCount(0)
              setShowScrollBottom(false)
              scrollToBottom(false, true)
            }}
            className={cn(
              'chat-scroll-btn-base absolute bottom-4 right-4 z-10 w-11 h-11 rounded-full flex items-center justify-center group',
              showScrollBottom
                ? (unreadCount > 0 ? 'chat-scroll-btn-visible chat-scroll-btn--has-unread' : 'chat-scroll-btn-visible')
                : 'chat-scroll-btn-hidden',
            )}
            title={unreadCount > 0 ? `${unreadCount} new message${unreadCount > 1 ? 's' : ''}` : 'Scroll to bottom'}
            aria-label={unreadCount > 0 ? `Scroll to bottom (${unreadCount} unread)` : 'Scroll to bottom'}
            tabIndex={showScrollBottom ? 0 : -1}
          >
            {/* Progress ring — shows how far from bottom */}
            <svg className="chat-scroll-progress-ring" viewBox="0 0 48 48">
              <circle className="track" cx="24" cy="24" r="21" />
              <circle
                className="progress"
                cx="24" cy="24" r="21"
                strokeDasharray={`${2 * Math.PI * 21}`}
                strokeDashoffset={`${2 * Math.PI * 21 * (1 - scrollProgress)}`}
              />
            </svg>
            <ChevronDown className="w-[18px] h-[18px] text-[#6b7280] dark:text-[#a0a0b8] group-hover:text-[#3b82f6] dark:group-hover:text-[#60a5fa] transition-colors duration-200" strokeWidth={2.5} />
            {unreadCount > 0 && (
              <span className="chat-scroll-badge">
                {unreadCount > 99 ? '99+' : unreadCount}
              </span>
            )}
          </button>
        </div>

        {/* PERF: Input area — isolated to prevent re-renders from message/status changes */}
        <ChatInputArea
          sessionId={sessionId}
          input={input}
          onInputChange={setInput}
          onSend={sendMessage}
          onCancel={cancelStream}
          isProcessing={isProcessing}
          isStreaming={isStreaming}
          isUploading={isUploading}
          attachedFiles={attachedFiles}
          onFileSelect={handleFileSelect}
          onFileRemove={handleFileRemove}
          onPaste={handlePaste}
          onDrop={handleDrop}
          onModelChange={setSelectedModel}
        />
      </div>
    </div>
  )
}
