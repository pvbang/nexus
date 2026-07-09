// ============================================================
// Task-related API calls
// ============================================================

import { apiGet, apiPost } from './client'
import { getUserId } from './auth'
import type { Task, SendChatResponse } from './types'

// ============================================================
// Task Detail API
// ============================================================

export function getTaskDetail(taskId: string): Promise<Task> {
  return apiGet<Task>(`/tasks/${taskId}`)
}

// ============================================================
// DB-First Chat (Polling Architecture)
// ARCHITECTURE: DB-first polling, no SSE/WS dependency.
// ============================================================

/**
 * ARCHITECTURE: DB-first polling, no SSE/WS dependency.
 *
 * Fire-and-forget chat message. Starts task in background,
 * returns task_id immediately. Frontend polls for events via
 * GET /tasks/{task_id}/events?after_seq=N
 */
export function sendChatMessage(
  message: string,
  sessionId: string,
  model?: string,
): Promise<SendChatResponse> {
  return apiPost<SendChatResponse>('/chat/send', {
    message,
    conversation_id: sessionId,
    user_id: getUserId(),
    ...(model ? { model } : {}),
  })
}
