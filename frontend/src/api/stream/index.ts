// ============================================================
// SSE Streaming - Main orchestrator (streamChat + public API)
// ============================================================

import { getAuthHeaders, getUserId } from '@/api/auth'
import { devLog, devWarn, devError } from '@/api/client'
import { API_URL } from '@/lib/constants'
import { useAuthStore } from '@/stores/authStore'
import { _getPS, _getES, _getNotify } from '@/api/stream/utils'
import { handlerMap } from '@/api/stream/handlers'
import { fetchMissedEvents } from '@/api/stream/connection'

import type { StreamChatOptions, StreamChatResult, StreamEvent } from '@/api/types'
import type { HandlerContext, StreamState } from '@/api/stream/types'

// Re-export for public API
export { fetchMissedEvents } from '@/api/stream/connection'

/**
 * Stream chat via SSE (Server-Sent Events).
 *
 * Now routes through full CoreOrchestrator pipeline:
 * - Receives step events (planning, searching, executing...)
 * - Receives agent activity events (tool_start, tool_done, thinking...)
 * - Receives token events for final response streaming
 * - Receives done event with full metadata (task_id, agents, swarm, xskill)
 *
 * Returns an AbortController for cancellation.
 */
export function streamChat(
  message: string,
  sessionId: string,
  options: StreamChatOptions,
): AbortController {
  const { onDone, onError, model } = options

  const controller = new AbortController()
  const state: StreamState = {
    fullContent: '',
    usage: {},
    doneReceived: false,
    eventCount: 0,
  }

  // H-03 FIX: Unique request ID per streamChat call — prevents duplicate task creation
  // on SSE retry. Backend uses this to detect and reject duplicate requests.
  const requestId = crypto.randomUUID()

  const ctx: HandlerContext = { sessionId, options, state }

  const run = async (isRetry = false) => {
    try {
      devLog('[SSE] Starting stream →', `${API_URL}/api/v1/chat/stream`, {
        message: message.slice(0, 80),
        sessionId,
        isRetry,
        requestId,
      })
      const res = await fetch(`${API_URL}/api/v1/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept-Encoding': 'identity',
          // Disable caching for SSE
          'Cache-Control': 'no-cache',
          // H-03: Idempotency key so backend can detect retries
          'X-Request-Id': requestId,
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          message,
          conversation_id: sessionId,
          user_id: getUserId(),
          request_id: requestId,
          ...(model ? { model } : {}),
        }),
        signal: controller.signal,
        // Prevent browser from using cached/compressed response
        cache: 'no-store',
      })

      devLog(
        '[SSE] Response status:',
        res.status,
        res.statusText,
        'content-type:',
        res.headers.get('content-type'),
      )

      if (!res.ok) {
        devError('[SSE] HTTP error:', res.status, res.statusText)

        // SEC-SSE01: Handle 401 specifically — logout via auth store (triggers redirect)
        if (res.status === 401) {
          useAuthStore.getState().logout()
          const notify = _getNotify()
          notify.add(
            'error',
            'Session expired',
            'Please log in again to continue chatting.',
            8000,
          )
          onError('Session expired. Please log in again.')
          return
        }

        // Handle 429 rate limit
        if (res.status === 429) {
          const retryAfter = res.headers.get('Retry-After')
          const seconds = retryAfter ? parseInt(retryAfter, 10) : 30
          const notify = _getNotify()
          notify.add(
            'warning',
            'Rate limited',
            `Too many requests. Try again in ${seconds}s.`,
            6000,
          )
          onError(`Rate limited. Retry after ${seconds}s.`)
          return
        }

        onError(`HTTP ${res.status}: ${res.statusText}`)
        return
      }

      // CONN-05: Verify content-type is text/event-stream
      const contentType = res.headers.get('content-type') || ''
      if (!contentType.includes('text/event-stream')) {
        devError('[SSE] Unexpected content-type:', contentType)
        try {
          const errorBody = await res.text()
          onError(`Unexpected response (${contentType}): ${errorBody.slice(0, 200)}`)
        } catch {
          onError(`Unexpected content-type: ${contentType}`)
        }
        return
      }

      const reader = res.body?.getReader()
      if (!reader) {
        devError('[SSE] No ReadableStream')
        onError('ReadableStream not supported')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      const streamStartTime = Date.now()
      let chunkCount = 0

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) {
            devLog(
              `[SSE] Stream ended. Total events: ${state.eventCount}, chunks: ${chunkCount}, content length: ${state.fullContent.length}, elapsed: ${Date.now() - streamStartTime}ms, doneReceived: ${state.doneReceived}`,
            )
            break
          }

          chunkCount++
          const chunkTime = Date.now() - streamStartTime
          const decoded = decoder.decode(value, { stream: true })
          buffer += decoded

          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''

          // Log chunk timing to detect buffering
          const eventsInChunk: string[] = []

          for (const part of parts) {
            const lines = part.split('\n')
            for (const line of lines) {
              if (!line.startsWith('data: ')) continue

              const jsonStr = line.slice(6)
              if (!jsonStr.trim()) continue

              try {
                const event: StreamEvent = JSON.parse(jsonStr)
                state.eventCount++
                if (event.type !== 'token' && event.type !== 'heartbeat') {
                  eventsInChunk.push(event.type)
                }

                // Dispatch to handler
                const handler = handlerMap[event.type]
                if (handler) {
                  handler(event, ctx)
                }
              } catch (parseErr) {
                devWarn('[SSE] JSON parse error:', jsonStr.slice(0, 100), parseErr)
              }
            }
          }

          // Log chunk timing — helps detect buffering
          if (eventsInChunk.length > 0) {
            devLog(
              `[SSE] Chunk #${chunkCount} @${chunkTime}ms: ${eventsInChunk.length} events [${eventsInChunk.join(', ')}] (${decoded.length} bytes)`,
            )
          }
        }

        // If stream ended without 'done', check if task is still running before concluding.
        if (!state.doneReceived && !controller.signal.aborted) {
          let taskStillRunning = false

          for (
            let _stateAttempt = 0;
            _stateAttempt < 3 && !taskStillRunning;
            _stateAttempt++
          ) {
            if (_stateAttempt > 0) {
              await new Promise((r) => setTimeout(r, 500))
            }
            try {
              const stateRes = await fetch(
                `${API_URL}/api/v1/sessions/${sessionId}/state`,
                { headers: { ...getAuthHeaders() } },
              )
              if (stateRes.ok) {
                const sessionState = await stateRes.json()
                taskStillRunning = !!sessionState.is_running
              }
            } catch {
              // State check failed — try task status check below
            }

            // This catches the race condition where session state is stale but task is still executing.
            if (!taskStillRunning) {
              try {
                const ps = _getPS()
                const realTaskId = ps.focusedTaskId || ps.taskId
                if (realTaskId && !realTaskId.startsWith('msg-')) {
                  const taskRes = await fetch(
                    `${API_URL}/api/v1/tasks/${realTaskId}`,
                    { headers: { ...getAuthHeaders() } },
                  )
                  if (taskRes.ok) {
                    const taskInfo = await taskRes.json()
                    taskStillRunning = ['executing', 'planning', 'decomposing'].includes(
                      taskInfo.status,
                    )
                  }
                }
              } catch {
                // Task check failed — use session state result
              }
            }
          }

          // This catches edge cases where task status has timing issues.
          if (!taskStillRunning && !state.fullContent) {
            const es = _getES()
            const sessionEvents = es.eventsBySession[sessionId] || []
            const hasDebateStarted = sessionEvents.some(
              (e: any) => e.type === 'debate_started',
            )
            const hasDebateConcluded = sessionEvents.some(
              (e: any) => e.type === 'debate_concluded',
            )
            if (hasDebateStarted && !hasDebateConcluded) {
              devWarn(
                '[SSE] Debate started but not concluded — treating as still running',
              )
              taskStillRunning = true
            }
          }

          if (taskStillRunning) {
            // Task is still executing — replay missed events then switch to background
            devWarn(
              `[SSE] Stream ended but task still running. Replaying missed events...`,
            )
            const es = _getES()
            const lastSeq = es.getMaxSequence(sessionId)
            await fetchMissedEvents(sessionId, lastSeq)
            onDone({
              content: state.fullContent,
              conversationId: sessionId,
              usage: state.usage as StreamChatResult['usage'],
              status: 'background',
              taskId: sessionId,
            })
          } else if (state.fullContent || state.eventCount > 0) {
            // Events were processed (agent activity) or content was received — treat as success
            devWarn(
              `[SSE] ⚠️ Stream ended without 'done' event. Events: ${state.eventCount}, content: ${state.fullContent.length} chars. Sending fallback done.`,
            )
            onDone({
              content: state.fullContent,
              conversationId: sessionId,
              usage: state.usage as StreamChatResult['usage'],
            })
          } else {
            devError(
              `[SSE] ⚠️ Stream ended with NO content and NO events. Treating as error.`,
            )
            onError('Stream ended unexpectedly without response')
          }
        }
      } finally {
        // Stream cleanup (no stale detection needed — backend handles timeouts)
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        devLog('[SSE] Stream aborted by user')
        return
      }
      devError('[SSE] ❌ Stream error:', err)

      if (!isRetry && !controller.signal.aborted) {
        const MAX_RETRIES = 3
        const BASE_DELAY_MS = 1000 // 1s, 3s, 9s (×3 each retry)
        const notify = _getNotify()

        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
          if (controller.signal.aborted) break
          const delay = BASE_DELAY_MS * Math.pow(3, attempt - 1)
          notify.add(
            'info',
            'Reconnecting...',
            `Attempt ${attempt}/${MAX_RETRIES} in ${delay / 1000}s...`,
            delay + 1000,
          )
          await new Promise((resolve) => setTimeout(resolve, delay))
          if (controller.signal.aborted) break

          try {
            const stateRes = await fetch(
              `${API_URL}/api/v1/sessions/${sessionId}/state`,
              { headers: { ...getAuthHeaders() } },
            )
            if (stateRes.ok) {
              const sessionState = await stateRes.json()
              if (sessionState.is_running) {
                // Task already running in background — DON'T retry POST (would duplicate)
                // Replay missed events before switching to background
                devLog(
                  `[SSE] Task already running (${sessionState.task_id}). Replaying missed events...`,
                )
                const es = _getES()
                const lastSeq = es.getMaxSequence(sessionId)
                await fetchMissedEvents(sessionId, lastSeq)
                notify.add(
                  'info',
                  'Task continues',
                  'Your request is being processed in background.',
                  4000,
                )
                onDone({
                  content: '',
                  conversationId: sessionId,
                  status: 'background',
                  taskId: sessionState.task_id,
                })
                return
              }
            }
          } catch {
            // State check failed — fall through to retry POST
          }

          devLog(`[SSE] Retry attempt ${attempt}/${MAX_RETRIES} after ${delay}ms...`)
          try {
            // Use setTimeout wrapper to break stack frame accumulation
            await new Promise<void>((resolve, reject) => {
              setTimeout(() => {
                run(true).then(resolve, reject)
              }, 0)
            })
            return // Success on retry
          } catch (retryErr) {
            devWarn(`[SSE] Retry ${attempt} failed:`, retryErr)
            if (attempt === MAX_RETRIES) break
          }
        }
      }

      // All retries exhausted or was already a retry
      if (!isRetry) {
        const notify = _getNotify()
        notify.add('error', 'Connection lost', 'Please resend your message.', 8000)
      }
      onError(err instanceof Error ? err.message : 'Stream connection failed')
    }
  }

  run()
  return controller
}
