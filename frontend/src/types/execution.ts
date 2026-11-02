// ============================================================
// Nexus - Execution event types
// Extracted from ExecutionTracePanel.tsx for shared usage
// ============================================================

export interface ThinkingEvent {
  id: string
  type: 'thinking'
  agent_name: string
  agent_role: string
  content: string
  action?: string
  challenges?: string[]
  supports?: string[]
  target_agents?: string[]
  turn?: number
  timestamp: number
}

export interface DocumentCreatedEvent {
  id: string
  type: 'document_created'
  document: 'TASK_BRIEF' | 'ROE' | 'TODO' | 'TODO_UPDATE'
  description: string
  timestamp: number
}

export interface EvolutionInsightEvent {
  id: string
  type: 'evolution_insight'
  turn: number
  improvement_suggestions: string[]
  blind_spots: string[]
  quality_trajectory: 'improving' | 'stagnating' | 'declining' | 'unclear'
  challenge_questions: string[]
  severity_score: number
  should_inject: boolean
  timestamp: number
}

export interface ToolEvent {
  id: string
  type: 'tool_start' | 'tool_done'
  agent_name: string
  tool: string
  params_preview?: string
  output_preview?: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  params?: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  output?: any
  success?: boolean
  execution_time_ms?: number
  timestamp: number
  /** Set to true when a tool_done has been merged into this tool_start */
  _merged?: boolean
}

export interface AgentSpawnEvent {
  id: string
  type: 'agent_spawn'
  agent_name: string
  agent_role: string
  system_prompt_preview?: string
  /** Full system prompt (used in ExecutionTracePanel detail view) */
  system_prompt?: string
  timestamp: number
}

export interface SwarmRoundEvent {
  id: string
  type: 'swarm_round'
  round: number
  total_rounds: number
  proposals: Array<{
    agent: string
    role?: string
    content: string
    tools_used?: string[]
  }>
  rebuttals?: Array<{
    agent: string
    content: string
  }>
  verdict?: {
    verdict?: string
    winner?: string | null
    synthesis?: string
    reasoning?: string
  }
  timestamp: number
}

export interface StepEvent {
  id: string
  type: 'step'
  step: number
  total_steps: number
  icon: string
  title: string
  description: string
  status: 'active' | 'done' | 'error' | 'pending'
  timestamp: number
}

// ============================================================
// Deep Reasoning execution events (new backend SSE events)
// ============================================================

export interface DeepAnalysisExecEvent {
  id: string
  type: 'deep_analysis'
  data: Record<string, unknown>
  timestamp: number
}

export interface SelfReflectionExecEvent {
  id: string
  type: 'self_reflection'
  data: Record<string, unknown>
  timestamp: number
}

export interface AutoVerifyExecEvent {
  id: string
  type: 'auto_verify'
  data: Record<string, unknown>
  timestamp: number
}

export interface CompletionCheckExecEvent {
  id: string
  type: 'completion_check'
  data: Record<string, unknown>
  timestamp: number
}

export interface MidAnalysisExecEvent {
  id: string
  type: 'mid_analysis'
  data: Record<string, unknown>
  timestamp: number
}

export interface ReactPhaseExecEvent {
  id: string
  type: 'react_phase'
  phase: string
  round?: number
  timestamp: number
}

export interface DebateCritiqueExecEvent {
  id: string
  type: 'debate_critique'
  data: Record<string, unknown>
  timestamp: number
}

export interface DebateRevisionExecEvent {
  id: string
  type: 'debate_revision'
  data: Record<string, unknown>
  timestamp: number
}

/** Granular debate streaming events preserved for history replay */
export interface DebateProposalExecEvent {
  id: string
  type: 'debate_proposal'
  round: number
  total_rounds: number
  agent_name: string
  agent_role: string
  content: string
  tools_used: string[]
  timestamp: number
}

export interface DebateRebuttalExecEvent {
  id: string
  type: 'debate_rebuttal'
  round: number
  total_rounds: number
  agent_name: string
  content: string
  timestamp: number
}

export interface DebateVerdictExecEvent {
  id: string
  type: 'debate_verdict'
  round: number
  total_rounds: number
  verdict: Record<string, unknown>
  timestamp: number
}

// ============================================================
// Chat-based debate events (new conversational debate system)
// ============================================================

export interface DebateChatMessageEvent {
  id: string
  type: 'debate_message'
  turn: number
  agent_name: string
  agent_role: string
  content: string
  message_type: string // opinion | evidence | challenge | agreement | question | synthesis | final_position | moderator
  references: number[]
  tools_used: string[]
  evidence_items: string[]
  confidence: number
  is_final?: boolean
  total_turns?: number
  budget_remaining?: number
  // Moderator-specific fields
  quality_score?: number
  convergence_score?: number
  action_type?: string
  timestamp: number
}

export interface DebateStartedEvent {
  id: string
  type: 'debate_started'
  task: string
  agents: string[]
  max_turns: number
  strategy?: string
  timestamp: number
}

export interface DebateConcludedEvent {
  id: string
  type: 'debate_concluded'
  total_turns: number
  concluded_by: string
  convergence_score?: number
  agents?: string[]
  answer_length?: number
  timestamp: number
}

export interface ModeratorInterventionEvent {
  id: string
  type: 'moderator_intervention'
  turn: number
  action?: string
  /** SSE path sends action_type (from chat.py _handle_event) */
  action_type?: string
  quality_score?: number
  convergence_score?: number
  message: string
  /** BUG-FIX: SSE handler transforms backend `message` → `content`.
   *  Both fields must exist in type to avoid runtime undefined access. */
  content?: string
  timestamp: number
}

// ============================================================
// Debate real-time activity events (turn progress, tool calls)
// ============================================================

interface DebateTurnEvent {
  id: string
  type: 'debate_turn'
  turn: number
  speaker?: string
  speaker_role?: string
  agent_name?: string
  agent_role?: string
  total_turns?: number
  budget_remaining?: number
  timestamp: number
}

interface DebateToolCallEvent {
  id: string
  type: 'debate_tool_call'
  agent_name: string
  tool: string
  params_preview?: string
  turn?: number
  phase?: string
  timestamp: number
}

interface DebateToolResultEvent {
  id: string
  type: 'debate_tool_result'
  agent_name: string
  tool: string
  success?: boolean
  output_preview?: string
  turn?: number
  phase?: string
  timestamp: number
}

interface DebateAgentPlanEvent {
  id: string
  type: 'debate_agent_plan'
  agent_name: string
  main_argument?: string
  turn?: number
  timestamp: number
}

interface DebateActionPhaseEvent {
  id: string
  type: 'debate_action_phase'
  agent_name: string
  turn?: number
  security_phase?: string
  timestamp: number
}

// ============================================================
// ============================================================

export interface DebatePhaseEvent {
  id: string
  type: 'debate_phase'
  phase: string // 'preparation' | 'final_positions' | 'synthesis'
  status: string // 'active' | 'complete'
  message?: string
  turns_completed?: number
  timestamp: number
}

export interface DebateReflectionEvent {
  id: string
  type: 'debate_reflection'
  turn: number
  sufficient: boolean
  stagnating: boolean
  progress_score: number
  recommendation: string // 'continue' | 'conclude_now'
  should_stop: boolean
  timestamp: number
}

// ============================================================
// ============================================================

export interface SynthesisStartEvent {
  id: string
  type: 'synthesis_start'
  timestamp: number
}

export interface SynthesisDoneEvent {
  id: string
  type: 'synthesis_done'
  timestamp: number
}

// ============================================================
// ============================================================

export interface SystemWarningEvent {
  id: string
  type: 'system_warning'
  warning_type: 'inactivity' | 'long_running' | 'budget' | 'resource'
  message: string
  idle_seconds?: number
  elapsed_seconds?: number
  memory_percent?: number
  cpu_percent?: number
  timestamp: number
}

export interface TaskCrashedEvent {
  id: string
  type: 'task_crashed'
  task_id: string
  session_id: string
  message: string
  timestamp: number
}

export interface ResourceAlertEvent {
  id: string
  type: 'resource_alert'
  level: 'warning' | 'critical' | 'normal'
  memory_percent: number
  cpu_percent: number
  message: string
  timestamp: number
}

// ============================================================
// FIX: BUG-RT08 - Missing TypeScript interface definitions for
// event types that are handled at runtime but had no interfaces.
// ============================================================

/** Gate check event trước khi vào debate */
export interface DebateGateCheckEvent {
  id: string
  type: 'debate_gate_check'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  result: 'approved' | 'rejected' | 'skipped'
  reason?: string
}

/** Tool call trong action phase */
export interface ActionToolCallEvent {
  id: string
  type: 'action_tool_call'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  tool: string
  agent_name?: string
  arguments?: Record<string, unknown>
}

/** Tool result trong action phase */
export interface ActionToolResultEvent {
  id: string
  type: 'action_tool_result'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  tool: string
  agent_name?: string
  success: boolean
  output?: string
}

/** Preparation research event */
export interface PreparationResearchEvent {
  id: string
  type: 'preparation_research'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  agent_name: string
  topic?: string
  status?: 'started' | 'completed'
}

/** Preparation started */
export interface PreparationStartedEvent {
  id: string
  type: 'preparation_started'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  agents: string[]
}

/** Preparation plan */
export interface PreparationPlanEvent {
  id: string
  type: 'preparation_plan'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  agent_name: string
  plan?: string
}

/** Preparation complete */
export interface PreparationCompleteEvent {
  id: string
  type: 'preparation_complete'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  duration_ms?: number
}

/** Report building event */
export interface ReportBuildingEvent {
  id: string
  type: 'report_building'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  status: 'started' | 'section_complete' | 'done'
  section?: string
}

/** Swarm activated */
export interface SwarmActivatedEvent {
  id: string
  type: 'swarm_activated'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  mode: string
  agent_count?: number
}

/** Task heartbeat */
export interface TaskHeartbeatEvent {
  id: string
  type: 'task_heartbeat'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  round?: number
  tool_calls?: number
  elapsed_ms?: number
}

/** Context compaction */
export interface ContextCompactionEvent {
  id: string
  type: 'context_compaction'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  before_tokens?: number
  after_tokens?: number
  method?: string
}

/** Context trimmed */
export interface ContextTrimmedEvent {
  id: string
  type: 'context_trimmed'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  messages_removed?: number
  reason?: string
}

/** Debate error */
export interface DebateErrorEvent {
  id: string
  type: 'debate_error'
  session_id: string
  task_id?: string
  sequence?: number
  timestamp: number
  error: string
  phase?: string
  recoverable?: boolean
}

export interface MicroStepEvent {
  id: string
  type: 'micro_step'
  turn: number
  phase: string
  agent?: string
  description?: string
  title?: string
  timestamp: number
}

export interface PlanningStartEvent {
  id: string
  type: 'planning_start'
  description?: string
  timestamp: number
}

export interface PlanningDoneEvent {
  id: string
  type: 'planning_done'
  complexity?: string
  agents_count?: number
  turns_planned?: number
  selected_agents?: string[]
  intent?: string
  plan_summary?: string
  timestamp: number
}

export interface EvaluationDoneEvent {
  id: string
  type: 'evaluation_done'
  progress_pct?: number
  decision?: string
  missing_items?: string[]
  evaluation_text?: string
  agent_evaluations?: Array<{ agent_name: string; score?: number; summary?: string }>
  contradictions?: string[]
  next_turn_focus?: string
  timestamp: number
}

export interface NextTurnPlanEvent {
  id: string
  type: 'next_turn_plan'
  turn: number
  objective: string
  milestone?: string
  execution_mode?: string
  assignments: Array<{
    agent_name: string
    agent_role?: string
    task: string
    success_criteria?: string
  }>
  timestamp: number
}

export type ExecutionEvent =
  | ThinkingEvent
  | ToolEvent
  | AgentSpawnEvent
  | SwarmRoundEvent
  | StepEvent
  | DeepAnalysisExecEvent
  | SelfReflectionExecEvent
  | AutoVerifyExecEvent
  | CompletionCheckExecEvent
  | MidAnalysisExecEvent
  | ReactPhaseExecEvent
  | DebateCritiqueExecEvent
  | DebateRevisionExecEvent
  | DebateProposalExecEvent
  | DebateRebuttalExecEvent
  | DebateVerdictExecEvent
  | DebateChatMessageEvent
  | DebateStartedEvent
  | DebateTurnEvent
  | DebateToolCallEvent
  | DebateToolResultEvent
  | DebateAgentPlanEvent
  | DebateActionPhaseEvent
  | SynthesisStartEvent
  | SynthesisDoneEvent
  | SystemWarningEvent
  | TaskCrashedEvent
  | ResourceAlertEvent
  | DebateConcludedEvent
  | ModeratorInterventionEvent
  | DebatePhaseEvent
  | DebateReflectionEvent
  | DebateGateCheckEvent
  | ActionToolCallEvent
  | ActionToolResultEvent
  | PreparationResearchEvent
  | PreparationStartedEvent
  | PreparationPlanEvent
  | PreparationCompleteEvent
  | ReportBuildingEvent
  | SwarmActivatedEvent
  | TaskHeartbeatEvent
  | ContextCompactionEvent
  | ContextTrimmedEvent
  | DebateErrorEvent
  | MicroStepEvent
  | PlanningStartEvent
  | PlanningDoneEvent
  | EvaluationDoneEvent
  | NextTurnPlanEvent
  | DocumentCreatedEvent
  | EvolutionInsightEvent
