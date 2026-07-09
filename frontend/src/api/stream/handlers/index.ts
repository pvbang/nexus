// ============================================================
// Handler Map - Maps event type strings to handler functions
// ============================================================

import type { EventHandler } from '@/api/stream/types'
import { StreamEventType } from '@/api/stream/eventTypes'

import {
  handleToken,
  handleUsage,
  handleDone,
  handleError,
  handleHeartbeat,
  handleTaskHeartbeat,
  handleTaskStarted,
  handleSwarmError,
  handleUserInputRequired,
  handleUserInputResolved,
} from './chat'

import {
  handleStep,
  handleAgentActivity,
  handleToolCall,
  handleToolResult,
  handleAgentSpawn,
  handleSwarmActivated,
  handleSwarmRound,
  handleSystemEvent,
  handlePlanningStart,
  handlePlanningDone,
  handleMicroStep,
  handleEvaluationDone,
  handleDeepAnalysis,
  handleSynthesisStart,
  handleSynthesisProgress,
  handleSynthesisDone,
  handleCompletionCheck,
  handleXSkill,
  handleSelfReflection,
  handleAutoVerify,
  handleMidAnalysis,
  handleReactPhase,
  handleAgentCommunication,
  handleToolCreation,
  handleTaskUpdate,
  handleAgentStatus,
  handleAgentProposalStart,
  handleAgentProposalFailed,
  handleMiscAgentEvent,
  handleDocumentCreated,
  handleNextTurnPlan,
  handleEvolutionInsight,
} from './process'

import {
  handleDebateProposal,
  handleDebateRebuttal,
  handleDebateVerdict,
  handleDebateCritique,
  handleDebateRevision,
  handleDebatePhase,
  handleDebateStarted,
  handleDebateMessage,
  handleModeratorIntervention,
  handleDebateConcluded,
  handleDebateToolEvents,
  handleDebateError,
} from './execution'

import { handleProjectEvent } from './project'

/**
 * Map of SSE event type → handler function.
 * Used by the main stream orchestrator to dispatch events.
 */
export const handlerMap: Record<string, EventHandler> = {
  // Chat
  [StreamEventType.TOKEN]: handleToken,
  [StreamEventType.USAGE]: handleUsage,
  [StreamEventType.DONE]: handleDone,
  [StreamEventType.ERROR]: handleError,
  [StreamEventType.HEARTBEAT]: handleHeartbeat,
  [StreamEventType.TASK_HEARTBEAT]: handleTaskHeartbeat,
  [StreamEventType.TASK_STARTED]: handleTaskStarted,
  [StreamEventType.SWARM_ERROR]: handleSwarmError,
  [StreamEventType.USER_INPUT_REQUIRED]: handleUserInputRequired,
  [StreamEventType.USER_INPUT_RESOLVED]: handleUserInputResolved,

  // Process - steps & agents
  [StreamEventType.STEP]: handleStep,
  [StreamEventType.AGENT_THINKING]: handleAgentActivity,
  [StreamEventType.AGENT_RESPONSE]: handleAgentActivity,
  [StreamEventType.TOOL_START]: handleAgentActivity,
  [StreamEventType.TOOL_DONE]: handleAgentActivity,
  [StreamEventType.TOOL_CALL]: handleToolCall,
  [StreamEventType.TOOL_RESULT]: handleToolResult,
  [StreamEventType.AGENT_SPAWN]: handleAgentSpawn,
  [StreamEventType.SWARM_ACTIVATED]: handleSwarmActivated,
  [StreamEventType.SWARM_ROUND]: handleSwarmRound,
  [StreamEventType.SWARM_ROUND_START]: handleSwarmRound,
  [StreamEventType.EVENT]: handleSystemEvent,

  // Process - planning & analysis
  [StreamEventType.PLANNING_START]: handlePlanningStart,
  [StreamEventType.PLANNING_DONE]: handlePlanningDone,
  [StreamEventType.DEEP_ANALYSIS]: handleDeepAnalysis,
  [StreamEventType.SYNTHESIS_START]: handleSynthesisStart,
  [StreamEventType.SYNTHESIS_PROGRESS]: handleSynthesisProgress,
  [StreamEventType.SYNTHESIS_DONE]: handleSynthesisDone,
  [StreamEventType.COMPLETION_CHECK]: handleCompletionCheck,
  [StreamEventType.XSKILL_RETRIEVE]: handleXSkill,
  [StreamEventType.XSKILL_ACCUMULATE]: handleXSkill,
  [StreamEventType.SELF_REFLECTION]: handleSelfReflection,
  [StreamEventType.AUTO_VERIFY]: handleAutoVerify,
  [StreamEventType.MID_ANALYSIS]: handleMidAnalysis,
  [StreamEventType.REACT_PHASE]: handleReactPhase,
  [StreamEventType.AGENT_COMMUNICATION]: handleAgentCommunication,
  [StreamEventType.TOOL_CREATION]: handleToolCreation,

  // Process - orchestrator micro-events
  [StreamEventType.MICRO_STEP]: handleMicroStep,
  [StreamEventType.EVALUATION_DONE]: handleEvaluationDone,
  [StreamEventType.DOCUMENT_CREATED]: handleDocumentCreated,
  [StreamEventType.NEXT_TURN_PLAN]: handleNextTurnPlan,
  [StreamEventType.EVOLUTION_INSIGHT]: handleEvolutionInsight,

  // Process - monitoring
  [StreamEventType.TASK_UPDATE]: handleTaskUpdate,
  [StreamEventType.AGENT_STATUS]: handleAgentStatus,
  [StreamEventType.AGENT_PROPOSAL_START]: handleAgentProposalStart,
  [StreamEventType.AGENT_PROPOSAL_FAILED]: handleAgentProposalFailed,
  [StreamEventType.SWARM_ROUND_PROPOSALS_COMPLETE]: handleMiscAgentEvent,
  [StreamEventType.AGENT_PROPOSAL_RETRY]: handleMiscAgentEvent,
  [StreamEventType.DEPTH_ENFORCER_BLOCK]: handleMiscAgentEvent,
  [StreamEventType.DEVILS_ADVOCATE]: handleMiscAgentEvent,
  [StreamEventType.REPORT_COMPILATION_START]: handleMiscAgentEvent,

  // Execution - debate
  [StreamEventType.DEBATE_PROPOSAL]: handleDebateProposal,
  [StreamEventType.DEBATE_REBUTTAL]: handleDebateRebuttal,
  [StreamEventType.DEBATE_VERDICT]: handleDebateVerdict,
  [StreamEventType.DEBATE_CRITIQUE]: handleDebateCritique,
  [StreamEventType.CROSS_CRITIQUE]: handleDebateCritique,
  [StreamEventType.DEBATE_REVISION]: handleDebateRevision,
  [StreamEventType.DEBATE_PHASE]: handleDebatePhase,
  [StreamEventType.DEBATE_STARTED]: handleDebateStarted,
  [StreamEventType.DEBATE_MESSAGE]: handleDebateMessage,
  [StreamEventType.MODERATOR_INTERVENTION]: handleModeratorIntervention,
  [StreamEventType.DEBATE_CONCLUDED]: handleDebateConcluded,
  [StreamEventType.DEBATE_TURN]: handleDebateToolEvents,
  [StreamEventType.DEBATE_TOOL_CALL]: handleDebateToolEvents,
  [StreamEventType.ACTION_TOOL_CALL]: handleDebateToolEvents,
  [StreamEventType.DEBATE_TOOL_RESULT]: handleDebateToolEvents,
  [StreamEventType.ACTION_TOOL_RESULT]: handleDebateToolEvents,
  [StreamEventType.DEBATE_AGENT_PLAN]: handleDebateToolEvents,
  [StreamEventType.DEBATE_ACTION_PHASE]: handleDebateToolEvents,
  [StreamEventType.PREPARATION_RESEARCH]: handleDebateToolEvents,
  [StreamEventType.PREPARATION_STARTED]: handleDebateToolEvents,
  [StreamEventType.PREPARATION_COMPLETE]: handleDebateToolEvents,
  [StreamEventType.DEBATE_ERROR]: handleDebateError,

  // Project builder
  [StreamEventType.PROJECT_PHASE]: handleProjectEvent,
  [StreamEventType.PROJECT_BLUEPRINT]: handleProjectEvent,
  [StreamEventType.PROJECT_SERVER]: handleProjectEvent,
  [StreamEventType.FILE_IMPLEMENTED]: handleProjectEvent,
  [StreamEventType.ITERATION_COMPLETE]: handleProjectEvent,
  [StreamEventType.PROJECT_COMPLETE]: handleProjectEvent,
  [StreamEventType.PROJECT_ERROR]: handleProjectEvent,
}
