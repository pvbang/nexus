// ============================================================
// MekongAI Nexus - API type definitions (SOURCE OF TRUTH)
// All API interfaces live here. api.ts re-exports them.
// ============================================================

import type {
  ProcessPlan,
} from './process'

export interface UploadResult {
  files: Array<{
    filename: string
    file_size: number
    mime_type: string
    char_count: number
    truncated: boolean
    markdown_content: string
    error: string | null
    success: boolean
    path: string
  }>
  total: number
  successful: number
  context: string
}

export interface Agent {
  id: string
  name: string
  role: string
  description?: string
  personality?: string
  system_prompt?: string
  status: string
  llm_model: string
  swarm_id?: string
  parent_agent_id?: string
  skills?: Record<string, boolean>
  tools?: string[]
  // Rich identity fields
  avatar?: string
  expertise?: string[]
  thinking_style?: string
  background?: string
  created_at?: string
}

export interface TaskSubtaskSummary {
  id: string
  title: string
  status: string
  error?: string
  created_at?: string
  updated_at?: string
}

export interface Task {
  id: string
  title: string
  description?: string
  status: string
  priority: number
  result?: Record<string, unknown>
  error?: string
  created_at?: string
  updated_at?: string
  parent_task_id?: string | null
  subtasks?: TaskSubtaskSummary[]
}

export interface Swarm {
  id: string
  name: string
  status: string
  strategy: string
  task_id?: string
  agents?: Agent[]
  result?: Record<string, unknown>
  created_at?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  metadata?: {
    agents_spawned?: Array<{ name: string; role: string; id?: string; status?: string }>
    swarm_id?: string
    status?: string
    strategy?: string
    rounds?: number
    xskill?: {
      prior_knowledge_used?: boolean
      skill_built?: boolean
      experiences_extracted?: number
    }
    attachments?: string[]
    usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
    debate_rounds?: Array<Record<string, unknown>>
    /** Persisted execution events (thinking, tools, steps, agent_spawn, debate, etc.)
     * Saved by backend into assistant message metadata so they survive F5 / history navigation.
     * Frontend reads these to reconstruct the full process view. */
    execution_events?: Array<Record<string, unknown>>
    [key: string]: unknown
  }
}

export interface SystemStats {
  agents: { total: number }
  tasks: { total: number }
  swarms: { total: number }
  messages: { total: number }
  knowledge_graph: {
    node_count: number
    edge_count: number
    entity_types: string[]
  }
}

export interface Session {
  session_id: string
  title: string
  created_at: string
  message_count: number
}

export interface SessionMessage {
  id: string
  session_id: string
  role: string
  content: string
  created_at: string
  metadata?: Record<string, unknown> | null
}

export interface SessionSearchResult {
  session_id: string
  role: string
  content_snippet: string
  timestamp: string
  relevance_score: number
}

export interface ActionEntry {
  session_id: string
  timestamp: string
  action_type: string
  agent_name?: string
  content: string
  details: Record<string, unknown>
  duration_ms?: number
}

export interface SessionStats {
  session_id: string
  total_actions: number
  total_tool_calls: number
  agents_involved: string[]
  action_types: Record<string, number>
  duration_ms: number
  first_action?: string
  last_action?: string
}

export interface ConversationProfile {
  name: string
  description: string
  persona?: string
  temperature?: number
  tools?: string[]
}

export interface LLMModel {
  name: string
  provider: string
  description: string
}

export interface StreamChatResult {
  content: string
  conversationId?: string
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
  // Orchestrator metadata (from done event)
  taskId?: string
  agentsSpawned?: Array<{ name: string; role: string; id?: string; status?: string }>
  swarmId?: string
  status?: string
  metadata?: Record<string, unknown>
}

export interface StreamEvent {
  type: string
  // token events
  content?: string
  // step events (from orchestrator)
  step?: number
  total_steps?: number
  icon?: string
  title?: string
  description?: string
  status?: string
  // agent activity events
  agent_name?: string
  agent_role?: string
  tool?: string
  preview?: string
  output_preview?: string
  params?: any
  success?: boolean
  execution_time_ms?: number
  // tool call events (legacy)
  name?: string
  args?: string
  output?: string
  // done/error events
  message?: string
  usage?: Record<string, number>
  conversation_id?: string
  task_id?: string
  agents_spawned?: Array<{ name: string; role: string; id?: string; status?: string }>
  swarm_id?: string
  metadata?: Record<string, unknown>
  // system events
  event_type?: string
  data?: Record<string, unknown>
  // swarm round events
  round?: number
  total_rounds?: number
  proposals?: Array<{
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
  // planning events
  plan?: ProcessPlan
  strategy?: string
  // agent communication events
  from_agent?: { id: string; name: string; role: string }
  to_agent?: { id: string; name: string; role: string } | null
  message_type?: string
  // synthesis events
  sources_count?: number
  summary?: string
  confidence?: number
  // xskill events
  skill_name?: string
  experience_count?: number
  quality?: number
  details?: string
  // tool creation events
  tool_name?: string
  reason?: string
  code?: string
  // deep reasoning events
  dimensions?: string[]
  assumptions?: string[]
  unknowns?: string[]
  risks?: string[]
  complexity_estimate?: string
  tool_plan?: string[]
  success_criteria?: string[]
  quality_score?: number
  on_track?: boolean
  missing_aspects?: string[]
  strategy_change?: string
  improvement_suggestions?: string[]
  verification_result?: string
  issues?: string[]
  fix_suggestions?: string[]
  missing_items?: string[]
  completeness_pct?: number
  verdict_str?: string
  strategy_adjustment?: string
  new_insights?: string[]
  phase?: string
  overall_quality?: number
  critique_points?: Array<{ severity: string; point: string }>
  missing_perspectives?: string[]
  revised_content?: string
  // interactive decision events
  decision_id?: string
  question?: string
  options?: Array<{ value: string; label: string; description?: string; priority: number }>
  allow_custom?: boolean
  timeout_seconds?: number
  context?: string
  expires_at?: string
  answer?: string
  // deep analysis extended fields
  analysis?: string
  assessment?: string
  // xskill extended fields
  skills_count?: number
  experiences_count?: number
  // chat-based debate events (DebateChatRoom)
  turn?: number
  task?: string
  agents?: string[]
  max_turns?: number
  references?: number[]
  tools_used?: string[]
  evidence_items?: string[]
  is_final?: boolean
  total_turns?: number
  budget_remaining?: number
  convergence_score?: number
  concluded_by?: string
  action?: string
}

export interface StreamStepEvent {
  step: number
  total_steps: number
  icon: string
  title: string
  description: string
  status: 'active' | 'done' | 'error' | 'pending'
}

export interface StreamAgentEvent {
  event_type: 'tool_start' | 'tool_done' | 'agent_thinking' | 'agent_results' | 'agent_response'
    | 'deep_analysis' | 'completion_check' | 'debate_critique' | 'debate_revision'
    | 'debate_phase' | 'self_reflection' | 'auto_verify'
  agent_name?: string
  agent_role?: string
  tool?: string
  params_preview?: string
  output_preview?: string
  params?: any
  output?: any
  preview?: string
  content?: string
  success?: boolean
  execution_time_ms?: number
}

export interface StreamAgentSpawnEvent {
  agent_name: string
  agent_role: string
  system_prompt_preview?: string
  system_prompt?: string
}

export interface StreamSwarmRoundEvent {
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
}
