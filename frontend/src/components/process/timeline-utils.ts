// ============================================================
// Nexus - Timeline Utilities (Shared)
// Extracted from ProcessStream for reuse in MessageEventExplorer.
// Contains: TimelineItem types + convertExecutionEventsToTimeline.
// ============================================================

import type { ExecutionEvent } from '../../types/execution'
import type {
  ProcessPlan,
  AgentMessage,
  ToolExecution,
  DebateRound,
  SynthesisEvent,
  XSkillEvent,
  ToolCreationEvent,
  DeepAnalysisResult,
  SelfReflectionResult,
  AutoVerifyResult,
  CompletionCheckResult,
  MidAnalysisResult,
  CritiqueResult,
  RevisionResult,
} from '../../types/process'

// ── Timeline item discriminated union ─────────────────────────

export interface PlanItem {
  kind: 'plan'
  timestamp: number
  data: ProcessPlan
}

export interface AgentSpawnItem {
  kind: 'agent_spawn'
  timestamp: number
  data: Array<{ id: string; name: string; role: string; tools: string[] }>
}

export interface ToolCallItem {
  kind: 'tool_call'
  timestamp: number
  data: ToolExecution
}

export interface DebateItem {
  kind: 'debate'
  timestamp: number
  data: DebateRound
}

export interface SynthesisItem {
  kind: 'synthesis'
  timestamp: number
  data: SynthesisEvent
}

export interface XSkillItem {
  kind: 'xskill'
  timestamp: number
  data: XSkillEvent[]
}

export interface ToolCreationItem {
  kind: 'tool_creation'
  timestamp: number
  data: ToolCreationEvent
}

export interface AgentThinkingItem {
  kind: 'agent_thinking'
  timestamp: number
  data: { agentName: string; agentRole: string; content: string; isActive: boolean }
}

export interface AgentCommunicationItem {
  kind: 'agent_communication'
  timestamp: number
  data: AgentMessage
}

export interface DeepAnalysisItem {
  kind: 'deep_analysis'
  timestamp: number
  data: DeepAnalysisResult
}

export interface SelfReflectionItem {
  kind: 'self_reflection'
  timestamp: number
  data: SelfReflectionResult
}

export interface AutoVerifyItem {
  kind: 'auto_verify'
  timestamp: number
  data: AutoVerifyResult
}

export interface CompletionCheckItem {
  kind: 'completion_check'
  timestamp: number
  data: CompletionCheckResult
}

export interface MidAnalysisItem {
  kind: 'mid_analysis'
  timestamp: number
  data: MidAnalysisResult
}

export interface CritiqueItem {
  kind: 'critique'
  timestamp: number
  data: CritiqueResult
}

export interface RevisionItem {
  kind: 'revision'
  timestamp: number
  data: RevisionResult
}

export interface ReactPhaseItem {
  kind: 'react_phase'
  timestamp: number
  data: { currentPhase: string; round?: number }
}

export type TimelineItem =
  | PlanItem
  | AgentSpawnItem
  | ToolCallItem
  | DebateItem
  | SynthesisItem
  | XSkillItem
  | ToolCreationItem
  | AgentThinkingItem
  | AgentCommunicationItem
  | DeepAnalysisItem
  | SelfReflectionItem
  | AutoVerifyItem
  | CompletionCheckItem
  | MidAnalysisItem
  | CritiqueItem
  | RevisionItem
  | ReactPhaseItem

// ── Helpers ───────────────────────────────────────────────────

export function formatEventTime(ts: number): string {
  if (!ts || ts < 1000) return ''
  const d = new Date(ts)
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/**
 * Format the assignment list carried by planning_done / next_turn_plan events
 * into displayable lines. Handles BOTH backend shapes:
 *   - planning_done: { agent, role, task, success_criteria }
 *   - next_turn_plan: { agent_name, agent_role, task, success_criteria }
 * Returns '' when there are no assignments.
 */
export function formatAssignments(raw: unknown): string {
  if (!Array.isArray(raw) || raw.length === 0) return ''
  const lines: string[] = ['', '📋 Phân công:']
  for (const a of raw) {
    if (a == null || typeof a !== 'object') continue
    const item = a as Record<string, unknown>
    const agent = String(item.agent || item.agent_name || 'Agent')
    const role = String(item.role || item.agent_role || '')
    const task = String(item.task || item.task_description || '')
    const header = role ? `${agent} (${role})` : agent
    lines.push(task ? `• ${header}: ${task}` : `• ${header}`)
  }
  return lines.length > 2 ? lines.join('\n') : ''
}

/** Map TimelineItem.kind to a filter category used by MessageEventExplorer */
export type TimelineFilterCategory = 'tool' | 'thinking' | 'debate' | 'analysis' | 'step' | 'other'

export function getTimelineItemCategory(kind: TimelineItem['kind']): TimelineFilterCategory {
  switch (kind) {
    case 'tool_call':
    case 'tool_creation':
      return 'tool'
    case 'agent_thinking':
    case 'agent_communication':
      return 'thinking'
    case 'debate':
    case 'synthesis':
      return 'debate'
    case 'deep_analysis':
    case 'self_reflection':
    case 'auto_verify':
    case 'completion_check':
    case 'mid_analysis':
    case 'critique':
    case 'revision':
      return 'analysis'
    case 'plan':
    case 'react_phase':
    case 'agent_spawn':
      return 'step'
    default:
      return 'other'
  }
}

// ── Supplemental events ───────────────────────────────────────
// Event types that ProcessStream's buildTimeline() does NOT build from
// processStore arrays (it only knows plan/agent_thinking/tools/debate/
// synthesis/deep-analysis/etc). These orchestrator decision & insight events
// only ever reach the executionStore (via DB polling), so during a LIVE run
// they would be invisible unless we supplement the processStore timeline with
// them. Keeping the list here — next to the converter that renders them —
// guarantees live and history go through the SAME rendering path.
export const EXEC_SUPPLEMENTAL_EVENT_TYPES = new Set<string>([
  'evaluation_done', 'next_turn_plan', 'evolution_insight',
  'metacognitive_insight', 'authorization_required', 'divergent_thinking_done',
  'workspace_rollback', 'infrastructure_backoff',
  'agent_failed', 'agent_force_stopped', 'agent_stuck',
  'report_building',
])

/**
 * Build TimelineItem[] for ONLY the supplemental (orchestrator decision/insight)
 * events found in `events`. Used by ProcessStream to append these to the
 * processStore-derived timeline during live runs, without duplicating the
 * rich cards (plan/synthesis/tool) that buildTimeline already produces.
 */
export function buildSupplementalTimeline(events: ExecutionEvent[]): TimelineItem[] {
  if (!Array.isArray(events)) return []
  const supplemental = events.filter((e) => {
    if (e == null || typeof e !== 'object') return false
    const rec = e as unknown as Record<string, unknown>
    const t = String(rec.type || rec.event_type || '')
    return EXEC_SUPPLEMENTAL_EVENT_TYPES.has(t)
  })
  if (supplemental.length === 0) return []
  return convertExecutionEventsToTimeline(supplemental)
}

// ── Convert execution events to timeline items ────────────────
// This is the shared converter that both ProcessStream (fallback)
// and MessageEventExplorer use to convert raw events into renderable
// TimelineItem[] for TimelineCard.

/**
 * Convert raw ExecutionEvent[] (from backend/metadata) to TimelineItem[]
 * for rendering with TimelineCard. Handles ALL known event types and
 * gracefully shows unknown types as generic timeline entries.
 */
export function convertExecutionEventsToTimeline(
  events: ExecutionEvent[],
): TimelineItem[] {
  // DEFENSIVE: If events is not an array, return empty timeline immediately
  if (!Array.isArray(events)) return []

  const items: TimelineItem[] = []
  const agentMap = new Map<string, { id: string; name: string; role: string; tools: string[] }>()

  for (let idx = 0; idx < events.length; idx++) {
    // DEFENSIVE: Per-event try/catch — one bad event never crashes the entire timeline
    try {
      const rawEvt = events[idx]
      if (rawEvt == null || typeof rawEvt !== 'object') continue

      const evt = rawEvt as unknown as Record<string, unknown>
      const evtType = String(evt.type || evt.event_type || '')
      const ts = Number(evt.timestamp) || idx * 10

      // ── Agent Spawn ──
      if (evtType === 'agent_spawn') {
        const name = String(evt.agent_name || 'Agent')
        const role = String(evt.agent_role || '')
        if (!agentMap.has(name)) {
          agentMap.set(name, { id: `exec-agent-${idx}`, name, role, tools: [] })
        }
      }
      // ── Agent Thinking (handle both 'thinking' and 'agent_thinking') ──
      else if (evtType === 'agent_thinking' || evtType === 'thinking') {
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: String(evt.agent_name || 'Agent'),
            agentRole: String(evt.agent_role || ''),
            content: String(evt.content || evt.preview || ''),
            isActive: false,
          },
        })
      }
      // ── Tool Events ──
      else if (evtType === 'tool_start' || evtType === 'tool_done') {
        if (evtType === 'tool_start' || evt._merged) {
          items.push({
            kind: 'tool_call',
            timestamp: ts,
            data: {
              id: `exec-tool-${idx}`,
              timestamp: ts,
              agentId: String(evt.agent_name || 'unknown'),
              agentName: String(evt.agent_name || 'Agent'),
              toolName: String(evt.tool || ''),
              toolArgs: (evt.params != null && typeof evt.params === 'object' ? evt.params : {}) as Record<string, unknown>,
              status: (evt.output !== undefined || evt._merged) ? 'completed' : 'running',
              result: evt.output != null ? String(evt.output) : evt.output_preview != null ? String(evt.output_preview) : undefined,
              duration: typeof evt.execution_time_ms === 'number' ? evt.execution_time_ms : undefined,
            },
          })
        }
      }
      else if (evtType === 'react_phase') {
        // Intentionally not displayed — fires every loop iteration
      }
      // ── Planning Start — skip (start marker only) ──
      else if (evtType === 'planning_start') {
        // No card: planning_done (below) renders the actual Orchestrator Plan
      }
      // ── XSkill / Memory Retrieval (Learning card) ──
      else if (evtType === 'xskill_retrieve' || evtType === 'xskill_accumulate' || evtType === 'memory_retrieval') {
        items.push({
          kind: 'xskill',
          timestamp: ts,
          data: [{
            id: `exec-xskill-${idx}`,
            timestamp: ts,
            type: evtType === 'xskill_accumulate' ? 'accumulate' : 'retrieve',
            skillName: String(evt.skill_name || evt.query || 'Knowledge'),
            details: String(evt.result || evt.summary || 'Retrieved'),
          }] as XSkillEvent[],
        })
      }
      // ── Deep Analysis ──
      else if (evtType === 'deep_analysis') {
        items.push({
          kind: 'deep_analysis',
          timestamp: ts,
          data: {
            dimensions: Array.isArray(evt.dimensions) ? evt.dimensions as string[] : [],
            assumptions: Array.isArray(evt.assumptions) ? evt.assumptions as string[] : [],
            unknowns: Array.isArray(evt.unknowns) ? evt.unknowns as string[] : [],
            risks: Array.isArray(evt.risks) ? evt.risks as string[] : [],
            strategy: evt.strategy != null ? String(evt.strategy) : undefined,
            complexity_estimate: evt.complexity_estimate as 'low' | 'medium' | 'high' | 'very_high' | undefined,
            tool_plan: Array.isArray(evt.tool_plan) ? evt.tool_plan as string[] : [],
            success_criteria: Array.isArray(evt.success_criteria) ? evt.success_criteria as string[] : [],
            timestamp: String(evt.created_at || new Date(ts).toISOString()),
          },
        })
      }
      // ── Self Reflection ──
      else if (evtType === 'self_reflection') {
        items.push({
          kind: 'self_reflection',
          timestamp: ts,
          data: {
            round: typeof evt.round === 'number' ? evt.round : undefined,
            quality_score: typeof evt.quality_score === 'number' ? evt.quality_score : undefined,
            on_track: typeof evt.on_track === 'boolean' ? evt.on_track : undefined,
            missing_aspects: Array.isArray(evt.missing_aspects) ? evt.missing_aspects as string[] : [],
            strategy_change: evt.strategy_change != null ? String(evt.strategy_change) : undefined,
            improvement_suggestions: Array.isArray(evt.improvement_suggestions) ? evt.improvement_suggestions as string[] : [],
            timestamp: String(evt.created_at || new Date(ts).toISOString()),
          },
        })
      }
      // ── Completion Check ──
      else if (evtType === 'completion_check') {
        items.push({
          kind: 'completion_check',
          timestamp: ts,
          data: {
            quality_score: typeof evt.quality_score === 'number' ? evt.quality_score : undefined,
            missing_items: Array.isArray(evt.missing_items) ? evt.missing_items as string[] : [],
            completeness_pct: typeof evt.completeness_pct === 'number' ? evt.completeness_pct : undefined,
            verdict: evt.verdict as 'pass' | 'needs_improvement' | 'fail' | undefined,
            timestamp: String(evt.created_at || new Date(ts).toISOString()),
          },
        })
      }
      // ── Auto Verify ──
      else if (evtType === 'auto_verify') {
        const vr = String(evt.verification_result || '')
        const validVr = (['pass', 'fail', 'warning'] as const).find(v => v === vr)
        items.push({
          kind: 'auto_verify',
          timestamp: ts,
          data: {
            tool_name: evt.tool_name != null ? String(evt.tool_name) : undefined,
            verification_result: validVr,
            issues: Array.isArray(evt.issues) ? evt.issues as string[] : [],
            fix_suggestions: Array.isArray(evt.fix_suggestions) ? evt.fix_suggestions as string[] : [],
            timestamp: String(evt.created_at || new Date(ts).toISOString()),
          },
        })
      }
      // ── Mid Analysis ──
      else if (evtType === 'mid_analysis') {
        items.push({
          kind: 'mid_analysis',
          timestamp: ts,
          data: {
            round: typeof evt.round === 'number' ? evt.round : undefined,
            on_track: typeof evt.on_track === 'boolean' ? evt.on_track : undefined,
            strategy_adjustment: evt.strategy_adjustment != null ? String(evt.strategy_adjustment) : undefined,
            new_insights: Array.isArray(evt.new_insights) ? evt.new_insights as string[] : [],
            timestamp: String(evt.created_at || new Date(ts).toISOString()),
          },
        })
      }
      else if (evtType === 'debate_critique') {
        items.push({
          kind: 'critique',
          timestamp: ts,
          data: {
            agent_name: evt.agent_name != null ? String(evt.agent_name) : undefined,
            overall_quality: typeof evt.overall_quality === 'number' ? evt.overall_quality : undefined,
            critique_points: Array.isArray(evt.critique_points) ? evt.critique_points : [],
            missing_perspectives: Array.isArray(evt.missing_perspectives) ? evt.missing_perspectives as string[] : [],
            timestamp: new Date(ts).toISOString(),
          },
        } as CritiqueItem)
      } else if (evtType === 'debate_revision') {
        items.push({
          kind: 'revision',
          timestamp: ts,
          data: {
            agent_name: evt.agent_name != null ? String(evt.agent_name) : undefined,
            original_content: evt.original_content != null ? String(evt.original_content) : undefined,
            revised_content: evt.revised_content != null ? String(evt.revised_content) : undefined,
            changes_made: Array.isArray(evt.changes_made) ? evt.changes_made as string[] : [],
            timestamp: new Date(ts).toISOString(),
          },
        } as RevisionItem)
      }
      // ── Chat Debate Events (from chat_room.py) ──
      else if (evtType === 'debate_message') {
        const dataObj = (evt.data && typeof evt.data === 'object') ? evt.data as Record<string, unknown> : {}
        const content = String(
          evt.content || evt.message ||
          dataObj.content || dataObj.message || ''
        )
        const messageType = String(evt.message_type || dataObj.message_type || 'opinion')
        const toolsUsed = Array.isArray(evt.tools_used) ? (evt.tools_used as string[])
          : Array.isArray(dataObj.tools_used) ? (dataObj.tools_used as string[]) : []
        const isFinal = Boolean(evt.is_final || dataObj.is_final)
        const turnNum = Number(evt.turn || dataObj.turn || 0)
        const agentName = String(evt.agent_name || dataObj.agent_name || 'Agent')
        const agentRole = String(evt.agent_role || dataObj.agent_role || 'debate')
        // Use agent_communication kind for richer display (name, role, content)
        items.push({
          kind: 'agent_communication',
          timestamp: ts,
          data: {
            id: `debate-msg-${idx}`,
            fromAgent: {
              id: String(agentName).toLowerCase().replace(/\s+/g, '-'),
              name: `${isFinal ? '🏁 ' : ''}${agentName}`,
              role: agentRole,
            },
            content: content || `[Debate message - Turn ${turnNum}]`,
            type: 'broadcast' as const,
            timestamp: ts,
            metadata: { messageType, toolsUsed, turn: turnNum, isFinal },
          },
        })
      }
      else if (evtType === 'debate_tool_call' || evtType === 'debate_tool_result') {
        const paramsPreview = String(evt.params_preview || '')
        const outputPreview = String(evt.output_preview || '')
        items.push({
          kind: 'tool_call',
          timestamp: ts,
          data: {
            id: `debate-tool-${idx}`,
            timestamp: ts,
            agentId: String(evt.agent_name || 'unknown'),
            agentName: String(evt.agent_name || 'Agent'),
            toolName: String(evt.tool || ''),
            toolArgs: paramsPreview ? { preview: paramsPreview } as Record<string, unknown> : {} as Record<string, unknown>,
            status: 'completed' as const,
            result: outputPreview || undefined,
            duration: undefined,
          },
        })
      }
      else if (evtType === 'moderator_intervention') {
        const modContent = String(evt.content || evt.message || '')
        const qualityScore = typeof evt.quality_score === 'number' ? evt.quality_score : undefined
        const convergenceScore = typeof evt.convergence_score === 'number' ? evt.convergence_score : undefined
        const action = String(evt.action || 'guide')
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: '🧑‍⚖️ Moderator',
            agentRole: `${action}${qualityScore != null ? ` | Quality: ${(qualityScore * 100).toFixed(0)}%` : ''}${convergenceScore != null ? ` | Convergence: ${(convergenceScore * 100).toFixed(0)}%` : ''}`,
            content: modContent || `Moderator ${action}`,
            isActive: false,
          },
        })
      }
      else if (evtType === 'debate_started' || evtType === 'debate_concluded' || evtType === 'debate_phase') {
        let phaseContent = ''
        if (evtType === 'debate_started') {
          const agents = Array.isArray(evt.agents) ? (evt.agents as string[]).join(', ') : ''
          phaseContent = `🏁 Debate bắt đầu${agents ? ` — Agents: ${agents}` : ''}${evt.max_turns ? ` (Max ${evt.max_turns} turns)` : ''}`
        } else if (evtType === 'debate_concluded') {
          const convergence = typeof evt.convergence_score === 'number' ? ` (Convergence: ${(evt.convergence_score as number * 100).toFixed(0)}%)` : ''
          phaseContent = `🏁 Debate kết thúc — ${evt.concluded_by || 'completed'}${convergence}${evt.total_turns ? ` | ${evt.total_turns} turns` : ''}`
        } else {
          phaseContent = `Debate phase: ${evt.phase || evt.status || 'transition'}`
        }
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: { agentName: 'Debate', agentRole: 'debate', content: phaseContent, isActive: false },
        })
      }
      else if (evtType === 'synthesis_start' || evtType === 'synthesis_done' || evtType === 'synthesis_progress') {
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: { agentName: 'Synthesis', agentRole: 'synthesis', content: evtType === 'synthesis_done' ? '✅ Synthesis hoàn thành' : '🔄 Đang tổng hợp kết quả...', isActive: false },
        })
      }
      else if (evtType === 'report_building') {
        const section = String(evt.section || evt.status || evt.content || 'Building report...')
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Report Builder',
            agentRole: 'synthesis',
            content: `📝 ${section}`,
            isActive: false,
          },
        })
      }
      else if (evtType === 'swarm_activated') {
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: { agentName: 'Swarm', agentRole: 'orchestrator', content: '🐝 Swarm activated — multi-agent debate starting', isActive: false },
        })
      }
      // ── Swarm/Debate Rounds ──
      else if (evtType === 'swarm_round' || evtType === 'swarm_round_start') {
        const rawProposals = Array.isArray(evt.proposals) ? evt.proposals as Array<Record<string, string>> : []
        const rawRebuttals = Array.isArray(evt.rebuttals) ? evt.rebuttals as Array<Record<string, string>> : []
        const verdictData = (evt.verdict != null && typeof evt.verdict === 'object') ? evt.verdict as Record<string, unknown> : undefined
        items.push({
          kind: 'debate',
          timestamp: ts,
          data: {
            round: typeof evt.round === 'number' ? evt.round : 0,
            proposals: rawProposals.map((p) => ({
              agentId: String(p?.agent || ''),
              agentName: String(p?.agent || p?.agent_name || 'Agent'),
              agentRole: String(p?.role || ''),
              content: String(p?.content || ''),
            })),
            rebuttals: rawRebuttals.map((r) => ({
              agentId: String(r?.agent || ''),
              agentName: String(r?.agent || r?.agent_name || 'Agent'),
              targetAgentId: '',
              content: String(r?.content || ''),
            })),
            verdict: verdictData ? {
              winner: verdictData.winner != null ? String(verdictData.winner) : undefined,
              summary: String(verdictData.verdict || verdictData.synthesis || ''),
              confidence: undefined,
            } : undefined,
          },
        })
      }
      // ── Individual Debate Events (debate_proposal / debate_rebuttal / debate_verdict) ──
      else if (evtType === 'debate_proposal' || evtType === 'debate_rebuttal' || evtType === 'debate_verdict') {
        if (evtType === 'debate_proposal') {
          const roundNum = typeof evt.round === 'number' ? evt.round : 1
          // Only add once per round
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const alreadyAdded = items.some((it) => it.kind === 'debate' && (it.data as any)?.round === roundNum)
          if (!alreadyAdded) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const allProposals = events.filter((e: any) => (e.type === 'debate_proposal') && ((e as any).round === roundNum))
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const allRebuttals = events.filter((e: any) => (e.type === 'debate_rebuttal') && ((e as any).round === roundNum))
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const verdictEvt = events.find((e: any) => (e.type === 'debate_verdict') && ((e as any).round === roundNum))
            items.push({
              kind: 'debate',
              timestamp: ts,
              data: {
                round: roundNum,
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                proposals: allProposals.map((p: any) => ({
                  agentId: String(p.agent_name || ''),
                  agentName: String(p.agent_name || 'Agent'),
                  agentRole: String(p.agent_role || ''),
                  content: String(p.content || ''),
                })),
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                rebuttals: allRebuttals.map((r: any) => ({
                  agentId: String(r.agent_name || ''),
                  agentName: String(r.agent_name || 'Agent'),
                  targetAgentId: '',
                  content: String(r.content || ''),
                })),
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                verdict: verdictEvt ? {
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  winner: (verdictEvt as any).verdict?.winner != null ? String((verdictEvt as any).verdict.winner) : undefined,
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  summary: String((verdictEvt as any).verdict?.verdict || (verdictEvt as any).verdict?.synthesis || ''),
                  confidence: undefined,
                } : undefined,
              },
            })
          }
        }
      }
      // ── Planning Done → Orchestrator Plan card ──
      else if (evtType === 'planning_done') {
        const planSummary = String(evt.plan_summary || evt.summary || '')
        const agentsCount = Number(evt.agents_count || 0)
        const turnsPlanned = evt.turns_planned != null ? String(evt.turns_planned) : '?'
        const selectedAgents: string[] = Array.isArray(evt.selected_agents) ? evt.selected_agents.map(String) : []
        const contentParts: string[] = []
        if (planSummary) contentParts.push(planSummary)
        contentParts.push(`Agents: ${agentsCount} | Turns planned: ${turnsPlanned}`)
        if (selectedAgents.length > 0) contentParts.push(`Selected: ${selectedAgents.join(', ')}`)
        const assignmentLines = formatAssignments(evt.assignments)
        if (assignmentLines) contentParts.push(assignmentLines)
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Orchestrator',
            agentRole: 'orchestrator_plan',
            content: `🎯 Orchestrator Plan\n${contentParts.join('\n')}`,
            isActive: false,
          },
        })
      }
      // ── Next Turn Plan → Orchestrator Plan card (assignments for turn N+1) ──
      else if (evtType === 'next_turn_plan') {
        const turn = evt.turn != null ? String(evt.turn) : '?'
        const objective = String(evt.objective || '')
        const contentParts: string[] = []
        if (objective) contentParts.push(objective)
        const assignmentLines = formatAssignments(evt.assignments)
        if (assignmentLines) contentParts.push(assignmentLines)
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Orchestrator',
            agentRole: 'orchestrator_plan',
            content: `🎯 Orchestrator Plan — Turn ${turn}\n${contentParts.join('\n')}`,
            isActive: false,
          },
        })
      }
      // ── Evaluation Done → Orchestrator Evaluation card ──
      else if (evtType === 'evaluation_done') {
        const progressPct = Number(evt.progress_pct || 0)
        const decision = String(evt.decision || 'unknown')
        const turn = evt.turn != null ? String(evt.turn) : '?'
        const missingItems = evt.missing_items
        const contentParts: string[] = []
        contentParts.push(`Progress: ${progressPct}% | Decision: ${decision}`)
        if (missingItems) {
          const missingStr = Array.isArray(missingItems) ? missingItems.join(', ') : String(missingItems)
          contentParts.push(`Missing: ${missingStr}`)
        }
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Orchestrator',
            agentRole: 'orchestrator_evaluation',
            content: `📊 Turn ${turn} Evaluation\n${contentParts.join('\n')}`,
            isActive: false,
          },
        })
      }
      // ── Evolution Agent insight → card (payload has no `content` → was dropped by fallback) ──
      else if (evtType === 'evolution_insight') {
        const turn = evt.turn != null ? String(evt.turn) : '?'
        const suggestions = Array.isArray(evt.improvement_suggestions) ? evt.improvement_suggestions.map(String) : []
        const blindSpots = Array.isArray(evt.blind_spots) ? evt.blind_spots.map(String) : []
        const challenges = Array.isArray(evt.challenge_questions) ? evt.challenge_questions.map(String) : []
        const parts: string[] = []
        if (suggestions.length) parts.push(`💡 Đề xuất: ${suggestions.join('; ')}`)
        if (blindSpots.length) parts.push(`🕳️ Điểm mù: ${blindSpots.join('; ')}`)
        if (challenges.length) parts.push(`❓ Chất vấn: ${challenges.join('; ')}`)
        if (parts.length) {
          items.push({
            kind: 'agent_thinking',
            timestamp: ts,
            data: {
              agentName: 'Evolution Agent',
              agentRole: 'evolution',
              content: `✨ Evolution Agent — Turn ${turn}\n${parts.join('\n')}`,
              isActive: false,
            },
          })
        }
      }
      // ── Metacognitive breakthrough → card (may carry a question for the user) ──
      else if (evtType === 'metacognitive_insight') {
        const rootCause = String(evt.root_cause || '')
        const action = String(evt.concrete_action || '')
        const askUser = Boolean(evt.should_ask_user)
        const question = String(evt.user_question || '')
        const parts: string[] = []
        if (rootCause) parts.push(`Gốc rễ: ${rootCause}`)
        if (action) parts.push(`Hành động: ${action}`)
        if (askUser && question) parts.push(`🙋 Cần hỏi bạn: ${question}`)
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Orchestrator',
            agentRole: 'metacognition',
            content: `🧠 Đột phá nhận thức\n${parts.join('\n')}`,
            isActive: false,
          },
        })
      }
      // ── Authorization gate (ROE) → blocking card ──
      else if (evtType === 'authorization_required') {
        const target = String(evt.target || '')
        const reason = String(evt.reason || '')
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Authorization',
            agentRole: 'guardrail',
            content: `⛔ Cần cấp phép — task chưa thực thi\nMục tiêu: ${target}${reason ? `\nLý do: ${reason}` : ''}`,
            isActive: false,
          },
        })
      }
      // ── Agent failure / force-stop / stuck → error cards (payload lacks `content`) ──
      else if (evtType === 'agent_failed' || evtType === 'agent_force_stopped' || evtType === 'agent_stuck') {
        const agentName = String(evt.agent_name || 'Agent')
        const agentRole = String(evt.agent_role || '')
        const reason = String(evt.failure_reason || evt.reason || '')
        const icon = evtType === 'agent_stuck' ? '🔁' : '❌'
        const label = evtType === 'agent_failed'
          ? 'thất bại'
          : evtType === 'agent_force_stopped'
            ? 'bị dừng cưỡng bức'
            : 'bị kẹt'
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName,
            agentRole: agentRole || 'error',
            content: `${icon} ${agentName} ${label}${reason ? `: ${reason}` : ''}`,
            isActive: false,
          },
        })
      }
      // ── Divergent thinking strategy chosen ──
      else if (evtType === 'divergent_thinking_done') {
        const strategy = String(evt.strategy || '')
        const insights = Array.isArray(evt.key_insights) ? evt.key_insights.map(String) : []
        const parts: string[] = []
        if (strategy) parts.push(strategy)
        if (insights.length) parts.push(`Insights: ${insights.join('; ')}`)
        if (parts.length) {
          items.push({
            kind: 'agent_thinking',
            timestamp: ts,
            data: {
              agentName: 'Orchestrator',
              agentRole: 'strategy',
              content: `🧭 Chiến lược tiếp cận\n${parts.join('\n')}`,
              isActive: false,
            },
          })
        }
      }
      // ── Workspace auto-rollback (regression guard) ──
      else if (evtType === 'workspace_rollback') {
        const turn = evt.turn != null ? String(evt.turn) : '?'
        const restored = Array.isArray(evt.restored_files) ? evt.restored_files.map(String) : []
        items.push({
          kind: 'agent_thinking',
          timestamp: ts,
          data: {
            agentName: 'Workspace',
            agentRole: 'guardrail',
            content: `↩️ Tự động rollback (Turn ${turn})${restored.length ? `\nKhôi phục: ${restored.join(', ')}` : ''}`,
            isActive: false,
          },
        })
      }
      // ── Step Events — skip (spammy like react_phase) ──
      else if (evtType === 'step') {
        // Intentionally not displayed — step events fire too frequently
      }
      // ── FALLBACK: Unknown event types → show as agent_thinking (generic display) ──
      else if (evtType && !['token', 'heartbeat', 'done', 'error', 'usage', 'task_started',
        'agent_response', 'task_update', 'agent_status',
        'task_completed', 'task_failed', 'task_cancelled'].includes(evtType)) {
        const content = String(evt.content || evt.message || evt.description || evt.title || '')
        if (content) {
          items.push({
            kind: 'agent_thinking',
            timestamp: ts,
            data: {
              agentName: String(evt.agent_name || 'System'),
              agentRole: evtType,
              content,
              isActive: false,
            },
          })
        }
      }
    } catch (eventErr) {
      // DEFENSIVE: Skip this event, continue with remaining events
      console.warn('[timeline-utils] convertExecutionEventsToTimeline: skipped event at index', idx, eventErr)
    }
  }

  // Add agent_spawn card if we collected agents
  if (agentMap.size > 0) {
    items.unshift({
      kind: 'agent_spawn',
      timestamp: 1,
      data: Array.from(agentMap.values()),
    })
  }

  // Sort by timestamp to maintain correct order
  items.sort((a, b) => a.timestamp - b.timestamp)
  return items
}
