// ============================================================
// MekongAI Nexus - Common shared types
// ============================================================

/** Loading state for async operations */
export type LoadingState = 'idle' | 'loading' | 'success' | 'error'

/** Standardized API error shape */
export interface ApiError {
  status: number
  message: string
  details?: unknown
}

/** Pagination query parameters */
export interface PaginationParams {
  offset?: number
  limit?: number
}

/** Paginated API response wrapper */
export interface PaginatedResponse<T> {
  data: T[]
  total: number
  offset: number
  limit: number
}

/** Sort direction for list queries */
export type SortDirection = 'asc' | 'desc'

/** Date range filter */
export interface DateRange {
  from: Date
  to: Date
}

// ============================================================
// Re-exports from domain type files
// ============================================================

export type {
  Agent,
  Task,
  Swarm,
  ChatMessage,
  SystemStats,
  Session,
  SessionMessage,
  SessionSearchResult,
  ActionEntry,
  SessionStats,
  ConversationProfile,
  LLMModel,
  UploadResult,
  StreamChatResult,
  StreamEvent,
  StreamStepEvent,
  StreamAgentEvent,
  StreamAgentSpawnEvent,
  StreamSwarmRoundEvent,
} from './api'

export type {
  ThinkingEvent,
  ToolEvent,
  AgentSpawnEvent,
  SwarmRoundEvent,
  StepEvent,
  DeepAnalysisExecEvent,
  SelfReflectionExecEvent,
  AutoVerifyExecEvent,
  CompletionCheckExecEvent,
  MidAnalysisExecEvent,
  ReactPhaseExecEvent,
  DebateCritiqueExecEvent,
  DebateRevisionExecEvent,
  DebateProposalExecEvent,
  DebateRebuttalExecEvent,
  DebateVerdictExecEvent,
  ExecutionEvent,
} from './execution'
