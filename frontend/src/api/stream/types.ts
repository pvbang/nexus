// ============================================================
// Stream Types - HandlerContext and shared types for stream handlers
// ============================================================

import type {
  StreamChatOptions,
  StreamEvent,
} from '@/api/types'

/**
 * Mutable state shared across all handlers during a single stream session.
 */
export interface StreamState {
  fullContent: string
  usage: Record<string, number>
  doneReceived: boolean
  eventCount: number
}

/**
 * Context passed to every event handler.
 * Contains session info, callbacks, and mutable stream state.
 */
export interface HandlerContext {
  sessionId: string
  options: StreamChatOptions
  state: StreamState
}

/**
 * Signature for all event handlers.
 */
export type EventHandler = (event: StreamEvent, ctx: HandlerContext) => void
