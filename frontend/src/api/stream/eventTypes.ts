// ============================================================
// Nexus - Stream Event Type Constants
// Single source of truth for all SSE event type strings
// ============================================================

export const StreamEventType = {
  // Chat
  TOKEN: 'token',
  USAGE: 'usage',
  DONE: 'done',
  ERROR: 'error',
  HEARTBEAT: 'heartbeat',
  TASK_HEARTBEAT: 'task_heartbeat',
  TASK_STARTED: 'task_started',
  SWARM_ERROR: 'swarm_error',
  USER_INPUT_REQUIRED: 'user_input_required',
  USER_INPUT_RESOLVED: 'user_input_resolved',

  // Process - steps & agents
  STEP: 'step',
  AGENT_THINKING: 'agent_thinking',
  AGENT_RESPONSE: 'agent_response',
  TOOL_START: 'tool_start',
  TOOL_DONE: 'tool_done',
  TOOL_CALL: 'tool_call',
  TOOL_RESULT: 'tool_result',
  AGENT_SPAWN: 'agent_spawn',
  SWARM_ACTIVATED: 'swarm_activated',
  SWARM_ROUND: 'swarm_round',
  SWARM_ROUND_START: 'swarm_round_start',
  EVENT: 'event',

  // Process - planning & analysis
  PLANNING_START: 'planning_start',
  PLANNING_DONE: 'planning_done',
  DEEP_ANALYSIS: 'deep_analysis',
  SYNTHESIS_START: 'synthesis_start',
  SYNTHESIS_PROGRESS: 'synthesis_progress',
  SYNTHESIS_DONE: 'synthesis_done',
  COMPLETION_CHECK: 'completion_check',
  XSKILL_RETRIEVE: 'xskill_retrieve',
  XSKILL_ACCUMULATE: 'xskill_accumulate',
  SELF_REFLECTION: 'self_reflection',
  AUTO_VERIFY: 'auto_verify',
  MID_ANALYSIS: 'mid_analysis',
  REACT_PHASE: 'react_phase',
  AGENT_COMMUNICATION: 'agent_communication',
  TOOL_CREATION: 'tool_creation',

  // Process - orchestrator micro-events
  MICRO_STEP: 'micro_step',
  EVALUATION_DONE: 'evaluation_done',
  DOCUMENT_CREATED: 'document_created',
  NEXT_TURN_PLAN: 'next_turn_plan',
  EVOLUTION_INSIGHT: 'evolution_insight',

  // Process - monitoring
  TASK_UPDATE: 'task_update',
  AGENT_STATUS: 'agent_status',
  AGENT_PROPOSAL_START: 'agent_proposal_start',
  AGENT_PROPOSAL_FAILED: 'agent_proposal_failed',
  SWARM_ROUND_PROPOSALS_COMPLETE: 'swarm_round_proposals_complete',
  AGENT_PROPOSAL_RETRY: 'agent_proposal_retry',
  DEPTH_ENFORCER_BLOCK: 'depth_enforcer_block',
  DEVILS_ADVOCATE: 'devils_advocate',
  REPORT_COMPILATION_START: 'report_compilation_start',

  // Execution - debate
  DEBATE_PROPOSAL: 'debate_proposal',
  DEBATE_REBUTTAL: 'debate_rebuttal',
  DEBATE_VERDICT: 'debate_verdict',
  DEBATE_CRITIQUE: 'debate_critique',
  CROSS_CRITIQUE: 'cross_critique',
  DEBATE_REVISION: 'debate_revision',
  DEBATE_PHASE: 'debate_phase',
  DEBATE_STARTED: 'debate_started',
  DEBATE_MESSAGE: 'debate_message',
  MODERATOR_INTERVENTION: 'moderator_intervention',
  DEBATE_CONCLUDED: 'debate_concluded',
  DEBATE_TURN: 'debate_turn',
  DEBATE_TOOL_CALL: 'debate_tool_call',
  ACTION_TOOL_CALL: 'action_tool_call',
  DEBATE_TOOL_RESULT: 'debate_tool_result',
  ACTION_TOOL_RESULT: 'action_tool_result',
  DEBATE_AGENT_PLAN: 'debate_agent_plan',
  DEBATE_ACTION_PHASE: 'debate_action_phase',
  PREPARATION_RESEARCH: 'preparation_research',
  PREPARATION_STARTED: 'preparation_started',
  PREPARATION_COMPLETE: 'preparation_complete',
  DEBATE_ERROR: 'debate_error',

  // Project builder
  PROJECT_PHASE: 'project_phase',
  PROJECT_BLUEPRINT: 'project_blueprint',
  PROJECT_SERVER: 'project_server',
  FILE_IMPLEMENTED: 'file_implemented',
  ITERATION_COMPLETE: 'iteration_complete',
  PROJECT_COMPLETE: 'project_complete',
  PROJECT_ERROR: 'project_error',
} as const

export type StreamEventType = (typeof StreamEventType)[keyof typeof StreamEventType]
