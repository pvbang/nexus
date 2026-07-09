// ============================================================
// MekongAI Nexus - Unified Process Panel (Right sidebar)
// Consolidates Execution Log, Files, Graph, and Debate Chat
// into a single tabbed panel.
// ============================================================

import { useState, useEffect, useRef, useMemo } from 'react'
import {
  ScrollText, GitBranch, FolderOpen, Users,
  ChevronRight,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { Tabs, type Tab } from '../ui/Tabs'
import { usePreferenceStore } from '../../stores/preferenceStore'
import { useProcessStore, getPersistedTaskId } from '../../stores/processStore'
import { useExecutionStore } from '../../stores/executionStore'
import { useArtifactPreviewStore } from '../../stores/artifactPreviewStore'
import { useDebateChat } from '../../hooks/useDebateChat'
import { DebateChatView } from './DebateChatView'
import { ErrorBoundary } from '../ui/ErrorBoundary'
import type { ToolEvent } from '../../types/execution'

// Stable empty array to avoid re-render loops from ?? [] creating new refs
const EMPTY_EVENTS: never[] = []

// Sub-panels rendered inside each tab
import { UnifiedTimeline } from '../process/UnifiedTimeline'
import { TaskKnowledgeGraph } from '../process/TaskKnowledgeGraph'
import { TaskArtifactsView } from '../process/TaskArtifactsView'
import { SessionArtifactsView } from '../process/SessionArtifactsView'

// ── Tab identifiers ───────────────────────────────────────────

type PanelTabId = 'log' | 'files' | 'graph' | 'debate'

// ── Props ─────────────────────────────────────────────────────

interface UnifiedProcessPanelProps {
  sessionId: string
}

// ── Tab definitions ───────────────────────────────────────────

const TAB_DEFS: Tab[] = [
  { id: 'log', label: 'Log', icon: <ScrollText className="w-3.5 h-3.5" /> },
  { id: 'debate', label: 'Debate', icon: <Users className="w-3.5 h-3.5" /> },
  { id: 'files', label: 'Files', icon: <FolderOpen className="w-3.5 h-3.5" /> },
  { id: 'graph', label: 'Graph', icon: <GitBranch className="w-3.5 h-3.5" /> },
]

// ── File-related tool names for SSE-driven refresh ────────────

const FILE_TOOL_KEYWORDS = [
  'write_file', 'save_file', 'create_file', 'execute_code',
  'download', 'generate', 'file', 'write', 'create', 'save',
]

function isFileRelatedTool(toolName: string): boolean {
  const lower = (toolName || '').toLowerCase()
  return FILE_TOOL_KEYWORDS.some((kw) => lower.includes(kw))
}

// ── Component ─────────────────────────────────────────────────

export function UnifiedProcessPanel({ sessionId }: UnifiedProcessPanelProps) {
  // -- Preference store (close button) ----------------------------
  const toggleRightPanel = usePreferenceStore((s) => s.toggleRightPanel)

  // -- Process store (live stage, taskId) -------------------------
  const processStage = useProcessStore((s) => s.stage)
  const processTaskId = useProcessStore((s) => s.taskId)
  const hasActiveProcess = processStage !== 'idle' && processStage !== 'completed'

  // -- Persisted taskId fallback (survives F5/reload) -------------
  // processTaskId is null after reload until ChatPanel replays events.
  // Use localStorage mapping as immediate fallback for Files/Graph tabs.
  const effectiveTaskId = processTaskId || getPersistedTaskId(sessionId)

  // -- Execution store (event count for tab badge) ----------------
  const executionEvents = useExecutionStore((s) => s.eventsBySession[sessionId] ?? EMPTY_EVENTS)
  const executionEventCount = executionEvents.length

  // -- Debate chat hook -------------------------------------------
  const { state: debateState, processEvent: processDebateEvent, isDebateActive } = useDebateChat()

  // Feed execution events to debate hook
  // DEBATE-FIX: Normalize event_type → type before passing to useDebateChat
  const prevEventCountRef = useRef(0)
  useEffect(() => {
    if (executionEvents.length > prevEventCountRef.current) {
      const newEvents = executionEvents.slice(prevEventCountRef.current)
      for (const evt of newEvents) {
        // Normalize: backend sends event_type, hook expects type
        if (evt.type) {
          processDebateEvent(evt)
        } else {
          const normalized = Object.assign({}, evt, { type: (evt as any).event_type || '' })
          processDebateEvent(normalized as typeof evt)
        }
      }
      prevEventCountRef.current = executionEvents.length
    }
  }, [executionEvents.length, processDebateEvent])

  // Auto-switch to debate tab when debate starts
  // DEBATE-FIX: Switch whenever debate becomes active (not only on first message)
  const prevDebateActive = useRef(false)
  useEffect(() => {
    if (isDebateActive && !prevDebateActive.current) {
      setActiveTab('debate')
    }
    prevDebateActive.current = isDebateActive
  }, [isDebateActive])

  // -- Active tab -------------------------------------------------
  const [activeTab, setActiveTab] = useState<PanelTabId>('log')

  // -- File refresh signal (SSE-driven) ---------------------------
  const [fileRefreshSignal, setFileRefreshSignal] = useState(0)

  // -- Artifact preview store (file link click from chat) -----------
  const requestedFile = useArtifactPreviewStore((s) => s.requestedFile)
  const requestTimestamp = useArtifactPreviewStore((s) => s.requestTimestamp)
  const [openFilePath, setOpenFilePath] = useState<string | null>(null)

  // When a file preview is requested from chat (via MarkdownRenderer click),
  // switch to Files tab and pass the file path down
  useEffect(() => {
    if (requestedFile && requestTimestamp > 0) {
      setActiveTab('files')
      setOpenFilePath(requestedFile)
      // Also ensure right panel is visible
      const rightCollapsed = usePreferenceStore.getState().rightPanelCollapsed
      if (rightCollapsed) {
        usePreferenceStore.getState().toggleRightPanel()
      }
      // Clear the request after handling
      useArtifactPreviewStore.getState().clearRequest()
    }
  }, [requestedFile, requestTimestamp])

  // Auto-switch logic — use primitive deps only to avoid loops
  const prevStageRef = useRef(processStage)
  useEffect(() => {
    // Only switch when stage actually changes
    if (prevStageRef.current === processStage) return
    prevStageRef.current = processStage

    if (hasActiveProcess) {
      setActiveTab('log')
    } else if (executionEventCount > 0 && processStage === 'completed') {
      setActiveTab('log')
    }
  }, [hasActiveProcess, processStage, executionEventCount])

  // -- Detect file-related tool completions for SSE-driven refresh --
  // Subscribe to the latest completed file-related tool event ID
  const latestFileToolEventId = useExecutionStore((s) => {
    const events = s.eventsBySession[sessionId] ?? EMPTY_EVENTS
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]
      if (e.type === 'tool_start' && (e as ToolEvent)._merged === true) {
        const tool = (e as ToolEvent).tool
        if (isFileRelatedTool(tool)) {
          return e.id
        }
      }
      // Also check standalone tool_done events
      if (e.type === 'tool_done') {
        const tool = (e as ToolEvent).tool
        if (isFileRelatedTool(tool)) {
          return e.id
        }
      }
    }
    return null
  })

  // Trigger file refresh when a new file-related tool event completes
  const prevFileToolRef = useRef(latestFileToolEventId)
  useEffect(() => {
    if (latestFileToolEventId && latestFileToolEventId !== prevFileToolRef.current) {
      prevFileToolRef.current = latestFileToolEventId
      setFileRefreshSignal((s) => s + 1)
    }
  }, [latestFileToolEventId])

  // Build tabs with dynamic counts — memoize to avoid new array each render
  const debateMessageCount = debateState.messages.length
  const tabs: Tab[] = useMemo(() => TAB_DEFS.map((tab) => {
    if (tab.id === 'log') return { ...tab, count: executionEventCount || undefined }
    if (tab.id === 'debate') return { ...tab, count: debateMessageCount || undefined, hidden: !isDebateActive && debateMessageCount === 0 }
    return tab
  }).filter(t => !(t as any).hidden), [executionEventCount, debateMessageCount, isDebateActive])

  // ── Render ──────────────────────────────────────────────────

  return (
    <div
      className={cn(
        'w-[340px] xl:w-[380px] hidden lg:flex lg:flex-col h-full shrink-0 overflow-hidden border-l',
        'bg-white border-gray-200',
        'dark:bg-[#0d0d1a] dark:border-[#2a2a45]',
      )}
    >
      {/* ── Header: Tabs + Close ──────────────────────────────── */}
      <div
        className={cn(
          'flex items-center justify-between px-2 py-2 border-b',
          'border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        <Tabs
          tabs={tabs}
          activeTab={activeTab}
          onChange={(id) => setActiveTab(id as PanelTabId)}
          variant="pill"
          size="sm"
          className="flex-1 min-w-0"
        />
        <button
          type="button"
          onClick={toggleRightPanel}
          className={cn(
            'ml-1 w-7 h-7 rounded-[7px] flex items-center justify-center shrink-0 transition-colors',
            'hover:bg-[#f4f5f7] dark:hover:bg-gray-800',
          )}
          title="Close panel (Ctrl+.)"
        >
          <ChevronRight className="w-4 h-4 text-text-muted" />
        </button>
      </div>

      {/* ── Tab content ───────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden min-h-0">
        {/* Tab: Log — UnifiedTimeline (merged process + execution events) */}
        {activeTab === 'log' && (
          <div className="h-full overflow-hidden">
            <UnifiedTimeline sessionId={sessionId} />
          </div>
        )}

        {/* Tab: Debate — Chat-based multi-agent debate view */}
        {activeTab === 'debate' && (
          <div className="h-full overflow-hidden">
            {isDebateActive ? (
              <ErrorBoundary fallback={
                <div className="flex flex-col items-center justify-center h-full px-4 text-center">
                  <span className="text-2xl mb-2">⚠️</span>
                  <p className="text-[13px] font-medium text-gray-500 dark:text-gray-400">
                    Debate view encountered an error
                  </p>
                  <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
                    The debate is still running in background. Try refreshing.
                  </p>
                </div>
              }>
                <DebateChatView state={debateState} />
              </ErrorBoundary>
            ) : (
              <div className="flex flex-col items-center justify-center h-full px-4 text-center">
                <Users className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-3" />
                <p className="text-[13px] font-medium text-gray-500 dark:text-gray-400">
                  No active debate
                </p>
                <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
                  Multi-agent debates will appear here as a real-time group chat.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Tab: Files — Task workspace artifacts */}
        {/* During active execution: use TaskArtifactsView for real-time updates */}
        {/* Otherwise: use SessionArtifactsView which discovers ALL task files from message metadata */}
        {activeTab === 'files' && (
          <div className="h-full overflow-hidden">
            {hasActiveProcess && effectiveTaskId ? (
              <TaskArtifactsView
                taskId={effectiveTaskId}
                isRunning={true}
                refreshSignal={fileRefreshSignal}
                openFilePath={openFilePath}
              />
            ) : (
              <SessionArtifactsView
                sessionId={sessionId}
                refreshSignal={fileRefreshSignal}
                openFilePath={openFilePath}
              />
            )}
          </div>
        )}

        {/* Tab: Graph — Knowledge Graph Entities (uses effectiveTaskId for F5 persistence) */}
        {activeTab === 'graph' && (
          <div className="h-full overflow-hidden">
            {effectiveTaskId ? (
              <TaskKnowledgeGraph taskId={effectiveTaskId} className="h-full" />
            ) : (
              <div className="flex flex-col items-center justify-center h-full px-4 text-center">
                <GitBranch className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-3" />
                <p className="text-[13px] font-medium text-gray-500 dark:text-gray-400">
                  No knowledge graph yet
                </p>
                <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
                  Knowledge entities will appear after a task completes.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
