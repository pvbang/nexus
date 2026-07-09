// ============================================================
// Process Handlers - Orchestration, planning, synthesis, agents, tools
// ============================================================

import { devWarn } from '@/api/client'
import { _getPS, _getES, _getRT, safeArr } from '@/api/stream/utils'
import type {
  StreamStepEvent,
  StreamSwarmRoundEvent,
  StreamAgentEvent,
  SSESynthesisEvent,
  SSEXSkillEvent,
  AgentMessage,
} from '@/api/types'
import type { EventHandler } from '@/api/stream/types'

export const handleStep: EventHandler = (event, ctx) => {
  ctx.options.onStep?.({
    step: event.step || 0,
    total_steps: event.total_steps || 0,
    icon: event.icon || '⏳',
    title: event.title || '',
    description: event.description || '',
    status: (event.status as StreamStepEvent['status']) || 'active',
  })
}

export const handleAgentActivity: EventHandler = (event, ctx) => {
  // agent_thinking, agent_response, tool_start, tool_done
  const e = event as any
  ctx.options.onAgentEvent?.({
    event_type: event.type as StreamAgentEvent['event_type'],
    agent_name: event.agent_name || 'Agent',
    agent_role: event.agent_role || '',
    tool: event.tool,
    preview: event.preview,
    output_preview: event.output_preview,
    params: event.params,
    output: event.output,
    content: event.content,
    success: event.success,
    execution_time_ms: event.execution_time_ms,
    action: e.action,
    challenges: e.challenges,
    supports: e.supports,
    target_agents: e.target_agents,
    turn: e.turn,
  } as any)
  // Transition processStore stage to 'executing' for tool/agent events
  {
    const ps = _getPS()
    const currentStage = ps.stage
    if (
      currentStage === 'planning' ||
      currentStage === 'spawning' ||
      currentStage === 'communicating'
    ) {
      ps.setStage('executing')
    }
  }
  // Also forward tool events to legacy callbacks
  if (event.type === 'tool_start') {
    ctx.options.onToolCall?.(event.tool || '', '')
  } else if (event.type === 'tool_done') {
    ctx.options.onToolResult?.(event.tool || '', event.output_preview || '')
  }
}

export const handleToolCall: EventHandler = (event, ctx) => {
  // Current backend emits 'tool_start' instead. Kept for backward compat only.
  ctx.options.onToolCall?.(event.name || '', event.args || '')
}

export const handleToolResult: EventHandler = (event, ctx) => {
  // Current backend emits 'tool_done' instead. Kept for backward compat only.
  ctx.options.onToolResult?.(event.name || '', event.output || '')
}

export const handleAgentSpawn: EventHandler = (event, ctx) => {
  ctx.options.onAgentSpawn?.({
    agent_name: event.agent_name || '',
    agent_role: event.agent_role || '',
    system_prompt_preview: event.preview,
  })
  if (!ctx.options.onAgentSpawn) {
    const ps = _getPS()
    ps.addAgent({
      id: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: event.agent_name || 'Agent',
      role: event.agent_role || '',
      status: 'active',
      spawnedAt: Date.now(),
      systemPromptPreview: event.preview,
    })
    if (ps.stage === 'planning') {
      ps.setStage('spawning')
    }
  }
}

export const handleSwarmActivated: EventHandler = () => {
  const ps = _getPS()
  ps.setStage('debating')
}

export const handleSwarmRound: EventHandler = (event, ctx) => {
  // swarm_round, swarm_round_start
  if (Array.isArray(event.proposals) && event.proposals.length > 0) {
    ctx.options.onSwarmRound?.({
      round: event.round || 0,
      total_rounds: event.total_rounds || 0,
      proposals: event.proposals,
      rebuttals: event.rebuttals || [],
      verdict: event.verdict,
    })
  } else if (event.data) {
    const data = event.data as unknown as StreamSwarmRoundEvent
    if (Array.isArray(data.proposals) && data.proposals.length > 0) {
      ctx.options.onSwarmRound?.(data)
    }
  }
}

export const handleSystemEvent: EventHandler = (event, ctx) => {
  // type === 'event' — system events (task_created, swarm_formed, etc.)
  if (event.event_type === 'agent_spawn' && event.data) {
    ctx.options.onAgentSpawn?.({
      agent_name: (event.data.agent_name as string) || '',
      agent_role: (event.data.agent_role as string) || '',
      system_prompt_preview: event.data.system_prompt_preview as string | undefined,
    })
  } else if (event.event_type === 'swarm_round' && event.data) {
    const roundData = event.data as unknown as StreamSwarmRoundEvent
    if (Array.isArray(roundData.proposals) && roundData.proposals.length > 0) {
      ctx.options.onSwarmRound?.(roundData)
    }
  }
}

export const handlePlanningStart: EventHandler = (event, ctx) => {
  ctx.options.processCallbacks?.onPlanningStart?.({
    type: 'planning_start',
    taskId: event.task_id || '',
    strategy: event.strategy,
  })
  const ps = _getPS()
  ps.setStage('planning')
}

export const handlePlanningDone: EventHandler = (event, ctx) => {
  ctx.options.processCallbacks?.onPlanningDone?.({
    type: 'planning_done',
    taskId: event.task_id || '',
    plan: event.plan,
  })
}

export const handleDeepAnalysis: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    if (ps.stage === 'planning') {
      ps.setStage('deep_analysis')
    }
    ctx.options.onAgentEvent?.({
      event_type: 'deep_analysis',
      agent_name: event.agent_name || 'Deep Analysis',
      agent_role: 'analysis',
      content: event.content || event.analysis || '',
      preview: event.preview || event.complexity_estimate || '',
    })
    ps.setDeepAnalysis({
      dimensions: safeArr<string>(event.dimensions),
      assumptions: safeArr<string>(event.assumptions),
      unknowns: safeArr<string>(event.unknowns),
      risks: safeArr<string>(event.risks),
      strategy: event.strategy,
      complexity_estimate: event.complexity_estimate as
        | 'low'
        | 'medium'
        | 'high'
        | 'very_high'
        | undefined,
      tool_plan: safeArr<string>(event.tool_plan),
      success_criteria: safeArr<string>(event.success_criteria),
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `deep-analysis-${Date.now()}`,
      type: 'deep_analysis',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling deep_analysis event:', err)
  }
}

export const handleSynthesisStart: EventHandler = (event, ctx) => {
  const ps = _getPS()
  ps.setStage('synthesizing')
  ctx.options.processCallbacks?.onSynthesis?.({
    type: 'synthesis_start',
    taskId: event.task_id || '',
    sourcesCount: event.sources_count || 0,
  } as SSESynthesisEvent)
}

export const handleSynthesisProgress: EventHandler = (event, ctx) => {
  ctx.options.processCallbacks?.onSynthesis?.({
    type: 'synthesis_progress',
    taskId: event.task_id || '',
    sourcesCount: event.sources_count,
    summary: event.summary,
    confidence: event.confidence,
  } as SSESynthesisEvent)
}

export const handleSynthesisDone: EventHandler = (event, ctx) => {
  ctx.options.processCallbacks?.onSynthesis?.({
    type: 'synthesis_done',
    taskId: event.task_id || '',
    sourcesCount: event.sources_count || 0,
    summary: event.summary || '',
    confidence: event.confidence || 0,
  } as SSESynthesisEvent)
}

export const handleCompletionCheck: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ps.setStage('completion_check')
    ctx.options.onAgentEvent?.({
      event_type: 'completion_check',
      agent_name: 'Goal Engine',
      agent_role: 'verification',
      content: `Quality: ${event.quality_score ? Math.round(event.quality_score * 100) : '?'}% — ${event.verdict_str || 'checking'}`,
      preview: `${event.completeness_pct || 0}% complete`,
    })
    ps.setCompletionCheck({
      quality_score: event.quality_score,
      missing_items: safeArr<string>(event.missing_items),
      completeness_pct: event.completeness_pct,
      verdict: event.verdict_str as 'pass' | 'needs_improvement' | 'fail' | undefined,
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `completion-check-${Date.now()}`,
      type: 'completion_check',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling completion_check event:', err)
  }
}

export const handleXSkill: EventHandler = (event, ctx) => {
  // xskill_retrieve, xskill_accumulate
  try {
    const ps = _getPS()
    if (ps.stage !== 'completed' && ps.stage !== 'failed') {
      ps.setStage('learning')
    }
    ctx.options.processCallbacks?.onXSkill?.({
      type: event.type as SSEXSkillEvent['type'],
      taskId: event.task_id || '',
      skillName: event.skill_name,
      experienceCount: event.experience_count,
      quality: event.quality,
      details: event.details,
    } as SSEXSkillEvent)
  } catch (err) {
    devWarn('[SSE] Error handling xskill event:', err)
  }
}

export const handleSelfReflection: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ctx.options.onAgentEvent?.({
      event_type: 'self_reflection',
      agent_name: event.agent_name || 'Self-Reflection',
      agent_role: 'reflection',
      content: event.content || event.assessment || '',
      preview: event.preview || '',
    })
    ps.addSelfReflection({
      round: event.round,
      quality_score: event.quality_score,
      on_track: event.on_track,
      missing_aspects: safeArr<string>(event.missing_aspects),
      strategy_change: event.strategy_change,
      improvement_suggestions: safeArr<string>(event.improvement_suggestions),
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `self-reflection-${Date.now()}`,
      type: 'self_reflection',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling self_reflection event:', err)
  }
}

export const handleAutoVerify: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ctx.options.onAgentEvent?.({
      event_type: 'auto_verify',
      agent_name: event.agent_name || 'Auto-Verify',
      agent_role: 'verification',
      content: event.content || '',
      preview: event.preview || '',
      success: event.success,
    })
    ps.addVerification({
      tool_name: event.tool_name,
      verification_result: event.verification_result as
        | 'pass'
        | 'fail'
        | 'warning'
        | undefined,
      issues: safeArr<string>(event.issues),
      fix_suggestions: safeArr<string>(event.fix_suggestions),
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `auto-verify-${Date.now()}`,
      type: 'auto_verify',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling auto_verify event:', err)
  }
}

export const handleMidAnalysis: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ps.addMidAnalysis({
      round: event.round,
      on_track: event.on_track,
      strategy_adjustment: event.strategy_adjustment,
      new_insights: safeArr<string>(event.new_insights),
      timestamp: new Date().toISOString(),
    })
    es.addEvent(ctx.sessionId, {
      id: `mid-analysis-${Date.now()}`,
      type: 'mid_analysis',
      data: event as unknown as Record<string, unknown>,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling mid_analysis event:', err)
  }
}

export const handleReactPhase: EventHandler = (event, ctx) => {
  try {
    const ps = _getPS()
    const es = _getES()
    ps.setReactPhase(event.phase || '')
    es.addEvent(ctx.sessionId, {
      id: `react-phase-${Date.now()}`,
      type: 'react_phase',
      phase: event.phase || '',
      round: event.round,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling react_phase event:', err)
  }
}

export const handleAgentCommunication: EventHandler = (event, ctx) => {
  if (event.from_agent) {
    ctx.options.processCallbacks?.onAgentCommunication?.({
      type: 'agent_communication',
      message: {
        id: event.name || crypto.randomUUID(),
        timestamp: Date.now(),
        fromAgent: event.from_agent,
        toAgent: event.to_agent,
        content: event.content || '',
        type: (event.message_type as AgentMessage['type']) || 'message',
        metadata: event.metadata,
      },
    })
  }
}

export const handleToolCreation: EventHandler = (event, ctx) => {
  ctx.options.processCallbacks?.onToolCreation?.({
    type: 'tool_creation',
    taskId: event.task_id || '',
    toolName: event.tool_name || '',
    reason: event.reason || '',
    status:
      (event.status as import('@/types/process').SSEToolCreationEvent['status']) ||
      'creating',
    code: event.code,
  })
}

export const handleTaskUpdate: EventHandler = (event) => {
  try {
    const rt = _getRT()
    rt.updateTaskStatus({
      task_id:
        event.task_id ||
        ((event.data as Record<string, unknown>)?.task_id as string) ||
        '',
      title: ((event as unknown as Record<string, unknown>).title as string) || '',
      status: event.status || '',
      progress: (event as unknown as Record<string, unknown>).progress as
        | number
        | undefined,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling task_update event:', err)
  }
}

export const handleAgentStatus: EventHandler = (event) => {
  try {
    const rt = _getRT()
    rt.updateAgentStatus({
      agent_id:
        ((event as unknown as Record<string, unknown>).agent_id as string) || '',
      name:
        event.agent_name ||
        ((event as unknown as Record<string, unknown>).name as string) ||
        '',
      status: event.status || '',
      role:
        event.agent_role ||
        ((event as unknown as Record<string, unknown>).role as string) ||
        '',
      last_activity: event.description || undefined,
      timestamp: Date.now(),
    })
  } catch (err) {
    devWarn('[SSE] Error handling agent_status event:', err)
  }
}

export const handleAgentProposalStart: EventHandler = (event, ctx) => {
  ctx.options.onAgentEvent?.({
    event_type: 'agent_thinking',
    agent_name: event.agent_name || 'Agent',
    agent_role: event.agent_role || '',
    content: `Starting proposal (Round ${event.round || '?'})`,
  })
}

export const handleAgentProposalFailed: EventHandler = (event, ctx) => {
  ctx.options.onAgentEvent?.({
    event_type: 'agent_response',
    agent_name: event.agent_name || 'Agent',
    agent_role: event.agent_role || '',
    content: (event as any).error || 'Proposal generation failed',
  })
}

export const handleMicroStep: EventHandler = (event, ctx) => {
  ctx.options.onStep?.({
    step: (event as any).turn || 0,
    total_steps: 0,
    icon: '⚡',
    title: event.phase || 'micro_step',
    description: event.description || '',
    status: 'active',
  })
}

export const handleEvaluationDone: EventHandler = (event, ctx) => {
  ctx.options.onStep?.({
    step: 0,
    total_steps: 0,
    icon: (event as any).passed ? '✅' : '⚠️',
    title: 'evaluation_done',
    description: `Score: ${(event as any).score ?? '?'} — ${(event as any).passed ? 'passed' : 'needs improvement'}`,
    status: (event as any).passed ? 'done' : 'active',
  })
}

export const handleMiscAgentEvent: EventHandler = (event, ctx) => {
  // swarm_round_proposals_complete, agent_proposal_retry,
  // depth_enforcer_block, devils_advocate, report_compilation_start
  ctx.options.onAgentEvent?.({
    event_type: 'agent_thinking',
    agent_name: event.agent_name || 'System',
    agent_role: event.agent_role || 'system',
    content: event.content || (event as any).error || event.phase || '',
  })
}

export const handleDocumentCreated: EventHandler = (event, ctx) => {
  const es = _getES()
  const e = event as any
  es.addEvent(ctx.sessionId, {
    id: `doc-${e.doc_type || 'unknown'}-${Date.now()}`,
    type: 'document_created',
    doc_type: e.doc_type || e.document_type || '',
    title: event.title || '',
    content_preview: e.content_preview || event.content || '',
    path: e.path || '',
    turn: event.turn || 0,
    timestamp: Date.now(),
  } as any)
}

export const handleNextTurnPlan: EventHandler = (event, ctx) => {
  const es = _getES()
  const e = event as any
  es.addEvent(ctx.sessionId, {
    id: `next-plan-turn-${event.turn || 0}-${Date.now()}`,
    type: 'next_turn_plan',
    turn: event.turn || 0,
    assignments: e.assignments || [],
    reasoning: e.reasoning || '',
    focus_areas: e.focus_areas || [],
    timestamp: Date.now(),
  } as any)
}

export const handleEvolutionInsight: EventHandler = (event, ctx) => {
  const es = _getES()
  const e = event as any
  es.addEvent(ctx.sessionId, {
    id: `evolution-${event.turn || 0}-${Date.now()}`,
    type: 'evolution_insight',
    turn: event.turn || 0,
    improvement_suggestions: e.improvement_suggestions || [],
    blind_spots: e.blind_spots || [],
    quality_trajectory: e.quality_trajectory || '',
    challenge_questions: e.challenge_questions || [],
    severity_score: e.severity_score || 0,
    should_inject: e.should_inject || false,
    timestamp: Date.now(),
  } as any)
}
