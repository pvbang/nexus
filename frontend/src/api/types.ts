// ============================================================
// API Types - Shared interfaces/types used across API modules
// ============================================================

import type {
  UploadResult as _UploadResult,
  Agent as _Agent,
  Task as _Task,
  Swarm as _Swarm,
  ChatMessage as _ChatMessage,
  SystemStats as _SystemStats,
  Session as _Session,
  SessionMessage as _SessionMessage,
  SessionSearchResult as _SessionSearchResult,
  ActionEntry as _ActionEntry,
  SessionStats as _SessionStats,
  ConversationProfile as _ConversationProfile,
  LLMModel as _LLMModel,
  StreamChatResult as _StreamChatResult,
  StreamEvent as _StreamEvent,
  StreamStepEvent as _StreamStepEvent,
  StreamAgentEvent as _StreamAgentEvent,
  StreamAgentSpawnEvent as _StreamAgentSpawnEvent,
  StreamSwarmRoundEvent as _StreamSwarmRoundEvent,
} from '../types/api'

import type {
  SSEPlanningEvent,
  SSEAgentCommunicationEvent,
  SSESynthesisEvent,
  SSEXSkillEvent,
  SSEToolCreationEvent,
  AgentMessage,
} from '../types/process'

// Re-export with original names for backward compatibility
export type UploadResult = _UploadResult
export type Agent = _Agent
export type Task = _Task
export type Swarm = _Swarm
export type ChatMessage = _ChatMessage
export type SystemStats = _SystemStats
export type Session = _Session
export type SessionMessage = _SessionMessage
export type SessionSearchResult = _SessionSearchResult
export type ActionEntry = _ActionEntry
export type SessionStats = _SessionStats
export type ConversationProfile = _ConversationProfile
export type LLMModel = _LLMModel
export type StreamChatResult = _StreamChatResult
export type StreamEvent = _StreamEvent
export type StreamStepEvent = _StreamStepEvent
export type StreamAgentEvent = _StreamAgentEvent
export type StreamAgentSpawnEvent = _StreamAgentSpawnEvent
export type StreamSwarmRoundEvent = _StreamSwarmRoundEvent

// Re-export process types needed by stream.ts
export type {
  SSEPlanningEvent,
  SSEAgentCommunicationEvent,
  SSESynthesisEvent,
  SSEXSkillEvent,
  SSEToolCreationEvent,
  AgentMessage,
}

// ── DB-First Chat ──

export interface SendChatResponse {
  task_id: string
  session_id: string
  status: string
}

// ── SSE Streaming Types ──

/** Granular debate streaming events — emitted per agent, per phase */
export interface StreamDebateProposalEvent {
  round: number
  total_rounds: number
  agent_name: string
  agent_role: string
  content: string
  tools_used: string[]
  phase: 'proposal'
}

export interface StreamDebateRebuttalEvent {
  round: number
  total_rounds: number
  agent_name: string
  agent_role?: string
  content: string
  phase: 'rebuttal'
}

export interface StreamDebateVerdictEvent {
  round: number
  total_rounds: number
  verdict: {
    verdict?: string
    winner?: string | null
    synthesis?: string
    reasoning?: string
  }
  phase: 'verdict'
}

/** Optional callbacks for new process-transparency SSE events */
export interface StreamChatProcessCallbacks {
  onPlanningStart?: (event: SSEPlanningEvent) => void
  onPlanningDone?: (event: SSEPlanningEvent) => void
  onAgentCommunication?: (event: SSEAgentCommunicationEvent) => void
  onSynthesis?: (event: SSESynthesisEvent) => void
  onXSkill?: (event: SSEXSkillEvent) => void
  onToolCreation?: (event: SSEToolCreationEvent) => void
}

/**
 * BUG-F05 FIX: Options object for streamChat — replaces long positional parameter list.
 * All callbacks are grouped into a single typed interface for maintainability.
 */
export interface StreamChatOptions {
  onToken: (token: string) => void
  onDone: (result: StreamChatResult) => void
  onError: (error: string) => void
  onToolCall?: (name: string, args?: string) => void
  onToolResult?: (name: string, output?: string) => void
  onStep?: (step: StreamStepEvent) => void
  onAgentEvent?: (event: StreamAgentEvent) => void
  onAgentSpawn?: (event: StreamAgentSpawnEvent) => void
  onSwarmRound?: (event: StreamSwarmRoundEvent) => void
  processCallbacks?: StreamChatProcessCallbacks
  onDebateProposal?: (event: StreamDebateProposalEvent) => void
  onDebateRebuttal?: (event: StreamDebateRebuttalEvent) => void
  onDebateVerdict?: (event: StreamDebateVerdictEvent) => void
  onDecisionRequired?: (decision: PendingDecision) => void
  model?: string
}

// ── Interactive Decisions ──

export interface DecisionOption {
  value: string
  label: string
  description?: string
  priority: number
}

export interface PendingDecision {
  decision_id: string
  question: string
  options: DecisionOption[]
  allow_custom: boolean
  timeout_seconds: number
  context?: string
  expires_at: string
}

// ── Analytics ──

export interface AnalyticsSummary {
  total_tokens: number
  total_cost: number
  total_sessions: number
  total_messages: number
  avg_tokens_per_message: number
}

export interface DailyUsage {
  date: string
  tokens: number
  cost: number
  messages: number
}

export interface ModelBreakdown {
  model: string
  tokens: number
  cost: number
  percentage: number
}

export interface TopSession {
  session_id: string
  title: string
  tokens: number
  messages: number
}

export interface HourlyPattern {
  hour: number
  tokens: number
}

export interface AnalyticsData {
  period: string
  summary: AnalyticsSummary
  daily_usage: DailyUsage[]
  model_breakdown: ModelBreakdown[]
  top_sessions: TopSession[]
  hourly_pattern: HourlyPattern[]
}
