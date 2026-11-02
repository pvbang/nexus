// ============================================================
// Nexus - Process types for real-time transparency
// ============================================================
// Each task flows through stages: planning → spawning → communicating
// → executing → debating → synthesizing → learning → responding

/** Stage a task can be in during the orchestration pipeline */
export type ProcessStage =
  | 'idle'
  | 'planning'
  | 'deep_analysis'    // DISC-03: Deep analysis before execution
  | 'spawning'
  | 'communicating'
  | 'executing'
  | 'debating'
  | 'synthesizing'
  | 'completion_check' // DISC-03: Verifying task completeness
  | 'learning'         // XSkill learning phase
  | 'responding'
  | 'completed'
  | 'failed'

// ============================================================
// Planning
// ============================================================

/** High-level plan produced by the orchestrator */
export interface ProcessPlan {
  taskId: string
  strategy: string
  steps: ProcessPlanStep[]
  estimatedDuration?: number
  complexity?: string
}

/** Individual step within a plan */
export interface ProcessPlanStep {
  id: string
  description: string
  type: 'decompose' | 'spawn' | 'execute' | 'debate' | 'synthesize'
  status: 'pending' | 'running' | 'completed' | 'failed'
  agentId?: string
}

// ============================================================
// Agent Info (tracked per session)
// ============================================================

/** Information about an active agent in the current session */
export interface AgentInfo {
  id: string
  name: string
  role: string
  status: 'spawning' | 'active' | 'idle' | 'completed' | 'error'
  spawnedAt: number
  systemPromptPreview?: string
  lastActivityAt?: number
  toolsUsed?: string[]
}

// ============================================================
// Agent Communication
// ============================================================

/** Message exchanged between agents */
export interface AgentMessage {
  id: string
  timestamp: number
  fromAgent: { id: string; name: string; role: string }
  toAgent?: { id: string; name: string; role: string } | null
  content: string
  type: 'message' | 'delegate' | 'response' | 'question' | 'broadcast'
  metadata?: Record<string, unknown>
}

// ============================================================
// Tool Execution
// ============================================================

/** A single tool invocation by an agent */
export interface ToolExecution {
  id: string
  timestamp: number
  agentId: string
  agentName: string
  toolName: string
  toolArgs: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'
  result?: string
  duration?: number
  error?: string
}

// ============================================================
// Debate
// ============================================================

/** One round of swarm debate (proposals → rebuttals → verdict) */
export interface DebateRound {
  round: number
  proposals: Array<{
    agentId: string
    agentName: string
    agentRole: string
    content: string
    confidence?: number
  }>
  rebuttals?: Array<{
    agentId: string
    agentName: string
    agentRole?: string   // DISC-02: agent role for display attribution
    targetAgentId: string
    content: string
  }>
  verdict?: {
    winner?: string
    summary: string
    confidence?: number
  }
}

// ============================================================
// Synthesis
// ============================================================

/** Progress event during final answer synthesis */
export interface SynthesisEvent {
  id: string
  timestamp: number
  type: 'start' | 'progress' | 'done'
  sourcesCount?: number
  summary?: string
  confidence?: number
}

// ============================================================
// XSkill (Experience-driven learning)
// ============================================================

/** XSkill lifecycle event (retrieve / accumulate / critique) */
export interface XSkillEvent {
  id: string
  timestamp: number
  type: 'retrieve' | 'accumulate' | 'critique'
  skillName?: string
  experienceCount?: number
  quality?: number
  details?: string
}

// ============================================================
// Tool Creation (self-healing)
// ============================================================

/** Dynamic tool creation event */
export interface ToolCreationEvent {
  id: string
  timestamp: number
  toolName: string
  reason: string
  status: 'creating' | 'testing' | 'ready' | 'failed'
  code?: string
}

// ============================================================
// Deep Reasoning Events (new backend SSE events)
// ============================================================

/** Deep analysis result from backend */
export interface DeepAnalysisResult {
  dimensions?: string[]
  assumptions?: string[]
  unknowns?: string[]
  risks?: string[]
  strategy?: string
  complexity_estimate?: 'low' | 'medium' | 'high' | 'very_high'
  tool_plan?: string[]
  success_criteria?: string[]
  timestamp?: string
}

/** Self-reflection checkpoint result */
export interface SelfReflectionResult {
  round?: number
  quality_score?: number
  on_track?: boolean
  missing_aspects?: string[]
  strategy_change?: string
  improvement_suggestions?: string[]
  timestamp?: string
}

/** Auto-verification result for tool output */
export interface AutoVerifyResult {
  tool_name?: string
  verification_result?: 'pass' | 'fail' | 'warning'
  issues?: string[]
  fix_suggestions?: string[]
  timestamp?: string
}

/** Completion quality check before final answer */
export interface CompletionCheckResult {
  quality_score?: number
  missing_items?: string[]
  completeness_pct?: number
  verdict?: 'pass' | 'needs_improvement' | 'fail'
  timestamp?: string
}

/** Mid-execution analysis checkpoint */
export interface MidAnalysisResult {
  round?: number
  on_track?: boolean
  strategy_adjustment?: string
  new_insights?: string[]
  timestamp?: string
}

/** Critic agent cross-critique result */
export interface CritiqueResult {
  agent_name?: string
  overall_quality?: number
  critique_points?: Array<{ severity: string; point: string }>
  missing_perspectives?: string[]
  timestamp?: string
}

/** Agent revision after critique */
export interface RevisionResult {
  agent_name?: string
  revised_content?: string
  timestamp?: string
}

// ============================================================
// Aggregate Process State
// ============================================================

/** Full process state for a single task */
export interface ProcessState {
  taskId: string | null
  sessionId: string | null
  stage: ProcessStage
  plan: ProcessPlan | null
  agentMessages: AgentMessage[]
  toolExecutions: ToolExecution[]
  debateRounds: DebateRound[]
  synthesisEvents: SynthesisEvent[]
  xskillEvents: XSkillEvent[]
  toolCreations: ToolCreationEvent[]
  agentThinkingEvents: Array<{ agentName: string; agentRole: string; content: string; timestamp: number }>
  /** Tracked agents for this session/task */
  agents: AgentInfo[]
  /** Overall progress 0-100 */
  progress: number
  startTime: number | null
  error: string | null

  // Deep reasoning state (new)
  deepAnalysis: DeepAnalysisResult | null
  selfReflections: SelfReflectionResult[]
  verifications: AutoVerifyResult[]
  completionCheck: CompletionCheckResult | null
  midAnalyses: MidAnalysisResult[]
  reactPhase: string
  critiques: CritiqueResult[]
  revisions: RevisionResult[]
}

// ============================================================
// SSE Event Payloads (new event types)
// ============================================================

/** Planning lifecycle SSE event */
export interface SSEPlanningEvent {
  type: 'planning_start' | 'planning_done'
  taskId: string
  plan?: ProcessPlan
  strategy?: string
}

/** Agent-to-agent communication SSE event */
export interface SSEAgentCommunicationEvent {
  type: 'agent_communication'
  message: AgentMessage
}

/** Synthesis lifecycle SSE event */
export interface SSESynthesisEvent {
  type: 'synthesis_start' | 'synthesis_progress' | 'synthesis_done'
  taskId: string
  sourcesCount?: number
  summary?: string
  confidence?: number
}

/** XSkill SSE event */
export interface SSEXSkillEvent {
  type: 'xskill_retrieve' | 'xskill_accumulate'
  taskId: string
  skillName?: string
  experienceCount?: number
  quality?: number
  details?: string
}

/** Tool creation SSE event */
export interface SSEToolCreationEvent {
  type: 'tool_creation'
  taskId: string
  toolName: string
  reason: string
  status: 'creating' | 'testing' | 'ready' | 'failed'
  code?: string
}
