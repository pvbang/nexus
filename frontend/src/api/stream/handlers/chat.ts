// ============================================================
// Chat Handlers - Token streaming, done, error, heartbeat, decisions
// ============================================================

import { devLog, devError } from '@/api/client'
import { _getPS, _getNotify } from '@/api/stream/utils'
import type { StreamChatResult, DecisionOption } from '@/api/types'
import type { EventHandler } from '@/api/stream/types'

export const handleToken: EventHandler = (event, ctx) => {
  if (event.content) {
    ctx.state.fullContent += event.content
    ctx.options.onToken(event.content)
  }
}

export const handleUsage: EventHandler = (event, _ctx) => {
  if (event.usage) _ctx.state.usage = event.usage
}

export const handleDone: EventHandler = (event, ctx) => {
  ctx.state.doneReceived = true
  devLog(
    `[SSE] ✅ Done received. Events: ${ctx.state.eventCount}, content: ${(event.content || ctx.state.fullContent).length} chars, taskId: ${event.task_id}, status: ${event.status}`,
  )
  ctx.options.onDone({
    content: event.content || ctx.state.fullContent,
    conversationId: event.conversation_id,
    usage: ctx.state.usage as StreamChatResult['usage'],
    taskId: event.task_id,
    agentsSpawned: event.agents_spawned,
    swarmId: event.swarm_id,
    status: event.status,
    metadata: event.metadata,
  })
}

export const handleError: EventHandler = (event, ctx) => {
  devError('[SSE] ❌ Error event:', event.message)
  ctx.options.onError(event.message || 'Unknown stream error')
}

export const handleHeartbeat: EventHandler = () => {
  // Keep-alive — ignore
}

export const handleTaskHeartbeat: EventHandler = (event) => {
  // This confirms the task is alive even during long LLM calls.
  devLog(
    '[SSE] 💓 Task heartbeat:',
    (event as any).last_activity,
    (event as any).current_tool,
  )
}

export const handleTaskStarted: EventHandler = (event) => {
  devLog('[SSE] 📋 Task started:', event.task_id)
  const ps = _getPS()
  if (event.task_id) {
    ps.setRealTaskId(event.task_id)
  }
}

export const handleSwarmError: EventHandler = (event, ctx) => {
  devError('[SSE] ❌ Swarm error:', (event as any).error, (event as any).failed_agents)
  ctx.options.onError((event as any).error || 'Swarm execution failed')
}

export const handleUserInputRequired: EventHandler = (event, ctx) => {
  devLog('[SSE] 🤔 User input required:', event.question)
  ctx.options.onDecisionRequired?.({
    decision_id: event.decision_id || '',
    question: event.question || event.content || '',
    options: (event.options as DecisionOption[]) || [],
    allow_custom: !!event.allow_custom,
    timeout_seconds: (event.timeout_seconds as number) || 60,
    context: event.context as string | undefined,
    expires_at:
      (event.expires_at as string) || new Date(Date.now() + 60000).toISOString(),
  })
}

export const handleUserInputResolved: EventHandler = (event) => {
  devLog('[SSE] ✅ User input resolved:', event.answer)
  window.dispatchEvent(
    new CustomEvent('decision-resolved', {
      detail: {
        decision_id: event.decision_id,
        answer: event.answer,
      },
    }),
  )
}
