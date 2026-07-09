// ============================================================
// MekongAI Nexus - Process Store Selectors
// Selector functions for useProcessStore(selector) pattern
// ============================================================

import type { ProcessState } from '@/types/process';
import type { ProcessStore } from './types';

// ============================================================
// Basic selectors (stable references for shallow equality)
// ============================================================

/** Select the focused process state (root-level alias) */
export const selectFocusedProcess = (state: ProcessStore): ProcessState => ({
  taskId: state.taskId,
  sessionId: state.sessionId,
  stage: state.stage,
  plan: state.plan,
  agentMessages: state.agentMessages,
  toolExecutions: state.toolExecutions,
  debateRounds: state.debateRounds,
  synthesisEvents: state.synthesisEvents,
  xskillEvents: state.xskillEvents,
  toolCreations: state.toolCreations,
  agentThinkingEvents: state.agentThinkingEvents,
  agents: state.agents,
  progress: state.progress,
  startTime: state.startTime,
  error: state.error,
  deepAnalysis: state.deepAnalysis,
  selfReflections: state.selfReflections,
  verifications: state.verifications,
  completionCheck: state.completionCheck,
  midAnalyses: state.midAnalyses,
  reactPhase: state.reactPhase,
  critiques: state.critiques,
  revisions: state.revisions,
});

export const selectStage = (state: ProcessStore) => state.stage;
export const selectProgress = (state: ProcessStore) => state.progress;
export const selectTaskId = (state: ProcessStore) => state.taskId;
export const selectSessionId = (state: ProcessStore) => state.sessionId;
export const selectFocusedTaskId = (state: ProcessStore) => state.focusedTaskId;
export const selectCurrentSessionId = (state: ProcessStore) => state.currentSessionId;
export const selectError = (state: ProcessStore) => state.error;
export const selectPlan = (state: ProcessStore) => state.plan;

// Arrays
export const selectAgents = (state: ProcessStore) => state.agents;
export const selectAgentMessages = (state: ProcessStore) => state.agentMessages;
export const selectToolExecutions = (state: ProcessStore) => state.toolExecutions;
export const selectDebateRounds = (state: ProcessStore) => state.debateRounds;
export const selectSynthesisEvents = (state: ProcessStore) => state.synthesisEvents;
export const selectXSkillEvents = (state: ProcessStore) => state.xskillEvents;
export const selectToolCreations = (state: ProcessStore) => state.toolCreations;
export const selectAgentThinkingEvents = (state: ProcessStore) => state.agentThinkingEvents;

// Deep reasoning
export const selectDeepAnalysis = (state: ProcessStore) => state.deepAnalysis;
export const selectSelfReflections = (state: ProcessStore) => state.selfReflections;
export const selectVerifications = (state: ProcessStore) => state.verifications;
export const selectCompletionCheck = (state: ProcessStore) => state.completionCheck;
export const selectMidAnalyses = (state: ProcessStore) => state.midAnalyses;
export const selectReactPhase = (state: ProcessStore) => state.reactPhase;
export const selectCritiques = (state: ProcessStore) => state.critiques;
export const selectRevisions = (state: ProcessStore) => state.revisions;

// Multi-task
export const selectProcesses = (state: ProcessStore) => state.processes;

/** Select whether any process is actively running */
export const selectIsProcessing = (state: ProcessStore) =>
  state.stage !== 'idle' && state.stage !== 'completed' && state.stage !== 'failed';

/** Select count of active (non-idle, non-terminal) processes */
export const selectActiveCount = (state: ProcessStore) =>
  Object.values(state.processes).filter(
    (p) => p.stage !== 'idle' && p.stage !== 'completed' && p.stage !== 'failed',
  ).length;
