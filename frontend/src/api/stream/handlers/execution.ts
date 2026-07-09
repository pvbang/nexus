// ============================================================
// Execution Handlers - Debate events (chat-based debate, critique, etc.)
// ============================================================

import { devWarn } from '@/api/client'
import { _getPS, _getES, safeArr } from '@/api/stream/utils'
import type { EventHandler } from '@/api/stream/types'

export const handleDebateProposal: EventHandler = (event, ctx) => {
  ctx.options.onDebateProposal?.({
    round: event.round || 0,
    total_rounds: event.total_rounds || 0,
    agent_name: event.agent_name || '',
    agent_role: event.agent_role || '',
    content: event.content || '',
    tools_used: (event as unknown as { tools_used?: string[] }).tools_used || [],
    phase: 'proposal',
  })
}

export const handleDebateRebuttal: EventHandler = (event, ctx) => {
  ctx.options.onDebateRebuttal?.({
    round: event.round || 0,
    total_rounds: event.total_rounds || 0,
    agent_name: event.agent_name || '',
    agent_role: event.agent_role || '',
    content: event.content || '',
    phase: 'rebuttal',
  })
}

export const handleDebateVerdict: EventHandler = (event, ctx) => {
  ctx.options.onDebateVerdict?.({
    round: event.round || 0,
    total_rounds: event.total_rounds || 0,
    verdict: event.verdict || { verdict: 'continue' },
    phase: 'verdict',
  })
}

export const handleDebateCritique: EventHandler = (event, ctx) => {
  // debate_critique, cross_critique
  try {
    const ps = _getPS()
    const es = _getES()
    ctx.options.onAgentEvent?.({
      event_type: 'debate_critique',
      agent_name: event.agent_name || 'Critic Agent',
      agent_role: 'critic',
      content: event.summary || event.content || '',
      preview: `Quality: ${event.quality_score || event.overall_quality || '?'}`,
    })
    ps.addCritique({
      agent_name: event.agent_name,
      overall_quality: event.overall_quality,
      critique_points: safeArr<{ severity: string; point: string }>(event.critique_points),
      missing_perspectives: safeArr<string>(event.missing_perspectives),
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `${event.type}-${Date.now()}`,
      type: 'debate_critique',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
    ps.setStage('debating')
  } catch (err) {
    devWarn('[SSE] Error handling debate_critique event:', err)
  }
}

export const handleDebateRevision: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ctx.options.onAgentEvent?.({
      event_type: 'debate_revision',
      agent_name: event.agent_name || 'Agent',
      agent_role: event.agent_role || '',
      content: event.content || event.revised_content || '',
      preview: `Revision for round ${event.round || '?'}`,
    })
    ps.addRevision({
      agent_name: event.agent_name,
      revised_content: event.revised_content || event.content,
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `debate-revision-${Date.now()}`,
      type: 'debate_revision',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
    ps.setStage('debating')
  } catch (err) {
    devWarn('[SSE] Error handling debate_revision event:', err)
  }
}

export const handleDebatePhase: EventHandler = (event, ctx) => {
  const ps = _getPS()
  ctx.options.onAgentEvent?.({
    event_type: 'debate_phase',
    agent_name: 'Swarm Engine',
    agent_role: 'orchestrator',
    content: `Phase: ${event.phase || ''} — ${event.status || ''}`,
    preview: event.phase || '',
  })
  ps.setReactPhase(event.phase || '')
}

export const handleDebateStarted: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ps.setStage('debating')
    es.addEvent(ctx.sessionId, {
      id: `debate-started-${Date.now()}`,
      type: 'debate_started',
      task: event.task || '',
      agents: event.agents || [],
      max_turns: event.max_turns || 30,
      timestamp: Date.now(),
    } as unknown as import('@/types/execution').ExecutionEvent)
  } catch (err) {
    devWarn('[SSE] Error handling debate_started event:', err)
  }
}

export const handleDebateMessage: EventHandler = (event, ctx) => {
  try {
    const es = _getES()
    const ps = _getPS()
    if (ps.stage !== 'debating') {
      ps.setStage('debating')
    }
    es.addEvent(ctx.sessionId, {
      id: `debate-msg-${event.turn || 0}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      type: 'debate_message',
      turn: event.turn || 0,
      agent_name: event.agent_name || 'Agent',
      agent_role: event.agent_role || '',
      content: event.content || '',
      message_type: event.message_type || 'opinion',
      references: event.references || [],
      tools_used: event.tools_used || [],
      evidence_items: event.evidence_items || [],
      confidence: event.confidence || 0.7,
      is_final: event.is_final,
      total_turns: event.total_turns,
      budget_remaining: event.budget_remaining,
      quality_score: event.quality_score,
      convergence_score: event.convergence_score,
      timestamp: Date.now(),
    } as unknown as import('@/types/execution').ExecutionEvent)
  } catch (err) {
    devWarn('[SSE] Error handling debate_message event:', err)
  }
}

export const handleModeratorIntervention: EventHandler = (event, ctx) => {
  try {
    const es = _getES()
    es.addEvent(ctx.sessionId, {
      id: `moderator-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      type: 'moderator_intervention',
      turn: event.turn || 0,
      message: event.message || event.content || '',
      action: (event as unknown as Record<string, unknown>).action_type || event.action,
      quality_score: event.quality_score,
      convergence_score: event.convergence_score,
      timestamp: Date.now(),
    } as unknown as import('@/types/execution').ExecutionEvent)
  } catch (err) {
    devWarn('[SSE] Error handling moderator_intervention event:', err)
  }
}

export const handleDebateConcluded: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    es.addEvent(ctx.sessionId, {
      id: `debate-concluded-${Date.now()}`,
      type: 'debate_concluded',
      total_turns: event.total_turns || 0,
      concluded_by: event.concluded_by || 'unknown',
      convergence_score: event.convergence_score,
      agents: event.agents,
      timestamp: Date.now(),
    } as unknown as import('@/types/execution').ExecutionEvent)
    // Transition to synthesizing (synthesis happens after debate)
    ps.setStage('synthesizing')
  } catch (err) {
    devWarn('[SSE] Error handling debate_concluded event:', err)
  }
}

export const handleDebateToolEvents: EventHandler = (event, ctx) => {
  // debate_turn, debate_tool_call, action_tool_call, debate_tool_result,
  // action_tool_result, debate_agent_plan, debate_action_phase,
  // preparation_research, preparation_started, preparation_complete
  try {
    const es = _getES()
    const e = event as unknown as Record<string, unknown>
    es.addEvent(ctx.sessionId, {
      id: `${event.type}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      type: event.type,
      agent_name: event.agent_name || '',
      tool: event.tool || '',
      params_preview: (e.params_preview as string) || '',
      output_preview: event.output_preview || '',
      success: event.success,
      turn: event.turn,
      phase: event.phase || '',
      main_argument: (e.main_argument as string) || '',
      timestamp: Date.now(),
    } as unknown as import('@/types/execution').ExecutionEvent)
  } catch (err) {
    devWarn('[SSE] Error handling debate tool event:', err)
  }
}

export const handleDebateError: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const _evt = event as unknown as Record<string, unknown>
    const _errType = String(_evt.error_type || 'Unknown')
    const _errMsg = String(_evt.error || '')
    console.error('[DEBATE ERROR]', _errType, _errMsg)
    ctx.options.onAgentEvent?.({
      event_type: 'debate_phase',
      agent_name: 'Swarm Engine',
      agent_role: 'orchestrator',
      content: `⚠️ Debate failed: ${_errType} — ${_errMsg}. Falling back to single-agent mode.`,
      preview: `Error: ${_errType}`,
    })
    // Don't change stage — fallback to ReactLoop will handle it
    ps.setReactPhase('fallback')
  } catch (err) {
    devWarn('[SSE] Error handling debate_error event:', err)
  }
}
