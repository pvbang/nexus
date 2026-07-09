// ============================================================
// MekongAI Nexus - Process Store Types & Constants
// ============================================================

import type {
  ProcessState,
  ProcessStage,
  ProcessPlan,
  AgentMessage,
  AgentInfo,
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
} from '@/types/process';

// ============================================================
// Constants
// ============================================================

export const MAX_PROCESSES = 20;
export const CLEANUP_AFTER_MS = 30 * 60 * 1000; // 30 minutes

/** Stage → progress mapping */
export const STAGE_PROGRESS: Record<ProcessStage, number> = {
  idle: 0,
  planning: 8,
  deep_analysis: 15,
  spawning: 22,
  communicating: 35,
  executing: 50,
  debating: 65,
  synthesizing: 78,
  completion_check: 85, // DISC-03: Completion verification
  learning: 90,
  responding: 95,
  completed: 100,
  failed: -1, // sentinel: keep current progress on failure
};

// ============================================================
// Action types
// ============================================================

export interface ProcessActions {
  // Lifecycle
  startProcess: (taskId: string, sessionId: string) => void;
  /** Start or resume process — does NOT reset if taskId already matches (F5-safe) */
  startOrResumeProcess: (taskId: string, sessionId: string) => void;
  setStage: (stage: ProcessStage) => void;
  endProcess: (success: boolean, error?: string) => void;
  resetProcess: () => void;
  /** Clear all data arrays for focused process — keeps taskId/sessionId/stage intact.
   *  Used to make populateProcessStore idempotent (reset before rebuild). */
  resetDataArrays: () => void;

  // Cleanup
  cleanupOldProcesses: () => void;

  // Session tracking (UI-01)
  setSession: (sessionId: string) => void;

  // Planning
  setPlan: (plan: ProcessPlan) => void;
  updatePlanStep: (stepId: string, status: ProcessPlan['steps'][0]['status']) => void;

  // Agent Communication
  addAgentMessage: (message: AgentMessage) => void;

  // Agent Tracking
  addAgent: (agent: AgentInfo) => void;
  updateAgentStatus: (agentName: string, status: AgentInfo['status']) => void;
  getAgents: () => AgentInfo[];

  // Agent Thinking
  addAgentThinking: (event: { agentName: string; agentRole: string; content: string }) => void;

  // Tool Execution
  addToolExecution: (execution: ToolExecution) => void;
  updateToolExecution: (id: string, updates: Partial<ToolExecution>) => void;

  // Debates
  addDebateRound: (round: DebateRound) => void;

  // Synthesis
  addSynthesisEvent: (event: SynthesisEvent) => void;

  // XSkill
  addXSkillEvent: (event: XSkillEvent) => void;

  // Tool Creation
  addToolCreation: (event: ToolCreationEvent) => void;
  updateToolCreation: (id: string, updates: Partial<ToolCreationEvent>) => void;

  // Deep Reasoning (new)
  setDeepAnalysis: (data: DeepAnalysisResult) => void;
  addSelfReflection: (data: SelfReflectionResult) => void;
  addVerification: (data: AutoVerifyResult) => void;
  setCompletionCheck: (data: CompletionCheckResult) => void;
  addMidAnalysis: (data: MidAnalysisResult) => void;
  setReactPhase: (phase: string) => void;
  addCritique: (data: CritiqueResult) => void;
  addRevision: (data: RevisionResult) => void;

  // Progress
  setProgress: (progress: number) => void;

  setRealTaskId: (taskId: string) => void;

  // Multi-task
  setFocusedTask: (taskId: string | null) => void;
  getActiveProcesses: () => ProcessState[];
  getActiveCount: () => number;
}

// ============================================================
// Multi-task store type
// Root-level ProcessState fields serve as "focused process" alias
// for backwards compatibility with existing selectors
// ============================================================

export interface MultiTaskFields {
  processes: Record<string, ProcessState>;
  focusedTaskId: string | null;
  /** Tracks which session the process store currently serves (UI-01) */
  currentSessionId: string | null;
}

export type ProcessStore = ProcessState & MultiTaskFields & ProcessActions;

// ============================================================
// Initial state
// ============================================================

export const initialProcessState: ProcessState = {
  taskId: null,
  sessionId: null,
  stage: 'idle',
  plan: null,
  agentMessages: [],
  toolExecutions: [],
  debateRounds: [],
  synthesisEvents: [],
  xskillEvents: [],
  toolCreations: [],
  agentThinkingEvents: [],
  agents: [],
  progress: 0,
  startTime: null,
  error: null,
  // Deep reasoning (new)
  deepAnalysis: null,
  selfReflections: [],
  verifications: [],
  completionCheck: null,
  midAnalyses: [],
  reactPhase: '',
  critiques: [],
  revisions: [],
};

export const initialState: ProcessState & MultiTaskFields = {
  ...initialProcessState,
  processes: {},
  focusedTaskId: null,
  currentSessionId: null,
};
