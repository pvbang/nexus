// ============================================================
// Nexus - ChatMessageList
// Virtualized message list with scroll management.
// Extracted from ChatPanel for performance isolation.
// Uses VirtualMessageList (tanstack/react-virtual) for large lists.
// React.memo prevents re-render when unrelated ChatPanel state changes.
// ============================================================

import React, { useCallback, useMemo, type RefObject } from 'react'
import { VirtualMessageList } from '../ui/VirtualMessageList'
import { MessageBubble } from './MessageBubble'
import { MessageEventExplorer } from './MessageEventExplorer'
import { ErrorBoundary } from '../ui/ErrorBoundary'
import type { ChatMessage } from '../../api'

// ── Props ─────────────────────────────────────────────────────

export interface ChatMessageListProps {
  messages: ChatMessage[]
  sessionId: string
  onRetry: (content: string) => void
  /** External scroll container ref from ChatPanel (handles scroll for entire chat area) */
  scrollContainerRef?: RefObject<HTMLDivElement | null>
  /** Whether user has manually scrolled up (prevents auto-scroll) */
  userScrolledUp: boolean
}

// ── Message renderer (stable via useCallback in parent) ───────

function MessageRenderer({
  message,
  sessionId,
  onRetry,
}: {
  message: ChatMessage
  sessionId: string
  onRetry: (content: string) => void
}) {
  return (
    <ErrorBoundary
      fallback={
        <div className="p-3 text-sm text-red-500 bg-red-50 dark:bg-red-900/20 rounded-lg">
          ⚠️ Message failed to render.{' '}
          <button className="underline" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      }
    >
      {/* Per-message events: show full event explorer BEFORE assistant message */}
      {message.role === 'assistant' &&
        message.metadata?.execution_events &&
        Array.isArray(message.metadata.execution_events) &&
        (message.metadata.execution_events as Array<Record<string, unknown>>).length > 0 && (
          <MessageEventExplorer
            events={message.metadata.execution_events as Array<Record<string, unknown>>}
            taskId={message.metadata?.task_id as string | undefined}
            sessionId={sessionId}
            isLive={false}
            defaultCollapsed={true}
          />
        )}
      <MessageBubble message={message} onRetry={onRetry} />
    </ErrorBoundary>
  )
}

// ── Component ─────────────────────────────────────────────────

export const ChatMessageList = React.memo(function ChatMessageList({
  messages,
  sessionId,
  onRetry,
  scrollContainerRef,
  userScrolledUp,
}: ChatMessageListProps) {
  // Stable render function for VirtualMessageList
  const renderMessage = useCallback(
    (message: ChatMessage) => (
      <MessageRenderer
        message={message}
        sessionId={sessionId}
        onRetry={onRetry}
      />
    ),
    [sessionId, onRetry],
  )

  // Memoize messages with id for VirtualMessageList interface
  const virtualMessages = useMemo(
    () =>
      messages.map((msg) => ({
        ...msg,
        id: msg.id,
      })),
    [messages],
  )

  if (messages.length === 0) return null

  return (
    <VirtualMessageList
      messages={virtualMessages}
      renderMessage={renderMessage}
      estimateSize={120}
      autoScrollToBottom={!userScrolledUp}
      scrollContainerRef={scrollContainerRef}
      className="space-y-6"
    />
  )
}, (prev, next) => {
  // Custom comparator: only re-render if messages or key props changed
  return (
    prev.messages === next.messages &&
    prev.sessionId === next.sessionId &&
    prev.onRetry === next.onRetry &&
    prev.userScrolledUp === next.userScrolledUp &&
    prev.scrollContainerRef === next.scrollContainerRef
  )
})
