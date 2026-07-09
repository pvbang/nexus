// ============================================================
// MekongAI Nexus - ChatStreamingContent
// Displays streaming AI response content in real-time.
// Extracted from ChatPanel for performance isolation.
// React.memo prevents re-render from unrelated state changes (e.g. statusText).
// ============================================================

import React from 'react'
import { StreamingMessage } from './StreamingMessage'

// ── Props ─────────────────────────────────────────────────────

export interface ChatStreamingContentProps {
  isStreaming: boolean
  streamingContent: string
}

// ── Component ─────────────────────────────────────────────────

export const ChatStreamingContent = React.memo(function ChatStreamingContent({
  isStreaming,
  streamingContent,
}: ChatStreamingContentProps) {
  if (!isStreaming || !streamingContent) return null

  return <StreamingMessage content={streamingContent} isActive={true} />
}, (prev, next) => {
  // Only re-render when streaming state actually changes
  return (
    prev.isStreaming === next.isStreaming &&
    prev.streamingContent === next.streamingContent
  )
})
