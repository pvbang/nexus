// ============================================================
// MekongAI Nexus - Process Store Actions
// All action implementations for the Zustand store
// ============================================================

import type { ProcessState } from '@/types/process';
import type { ProcessStore } from './types';
import { initialProcessState, STAGE_PROGRESS, MAX_PROCESSES, CLEANUP_AFTER_MS } from './types';
import { persistTaskMapping, persistStage } from './persistence';

// ============================================================
// Module-level timer map for deferred cleanup (not stored in Zustand)
// ============================================================

export const cleanupTimers = new Map<string, ReturnType<typeof setTimeout>>();

// ============================================================
// Batching: accumulate agent thinking events and flush per-frame
// ============================================================

let _batchedAgentThinking: Array<{ agentName: string; agentRole: string; content: string }> = [];
let _batchFlushScheduled = false;

/** Reference to the store getter — set by index.ts after store creation */
let _storeGetter: (() => ProcessStore) | null = null;

export function setStoreGetter(getter: () => ProcessStore): void {
  _storeGetter = getter;
}

function _scheduleBatchFlush(): void {
  if (_batchFlushScheduled) return;
  _batchFlushScheduled = true;
  setTimeout(() => {
    _batchFlushScheduled = false;
    if (_batchedAgentThinking.length > 0) {
      const batch = _batchedAgentThinking.slice();
      _batchedAgentThinking = [];
      if (_storeGetter) {
        const store = _storeGetter();
        for (const event of batch) {
          store.addAgentThinking(event);
        }
      }
    }
  }, 16);
}

/** Batched version of addAgentThinking — accumulates and flushes per-frame.
 * Use this from SSE hot path (tool_start, tool_done, agent_thinking)
 * to avoid 20+ re-renders per second. */
export function batchedAddAgentThinking(event: {
  agentName: string;
  agentRole: string;
  content: string;
}): void {
  _batchedAgentThinking.push(event);
  _scheduleBatchFlush();
}

// ============================================================
// Helpers
// ============================================================

/** Extract root-level ProcessState fields from a ProcessState object */
export function toRootFields(process: ProcessState): ProcessState {
  return {
    taskId: process.taskId,
    sessionId: process.sessionId,
    stage: process.stage,
    plan: process.plan,
    agentMessages: process.agentMessages,
    toolExecutions: process.toolExecutions,
    debateRounds: process.debateRounds,
    synthesisEvents: process.synthesisEvents,
    xskillEvents: process.xskillEvents,
    toolCreations: process.toolCreations,
    agentThinkingEvents: process.agentThinkingEvents,
    agents: process.agents,
    progress: process.progress,
    startTime: process.startTime,
    error: process.error,
    // Deep reasoning (new)
    deepAnalysis: process.deepAnalysis,
    selfReflections: process.selfReflections,
    verifications: process.verifications,
    completionCheck: process.completionCheck,
    midAnalyses: process.midAnalyses,
    reactPhase: process.reactPhase,
    critiques: process.critiques,
    revisions: process.revisions,
  };
}

/** Update focused process in the processes map and sync root fields */
export function updateFocused(
  get: () => ProcessStore,
  patch: Partial<ProcessState>,
): Partial<ProcessStore> {
  const { focusedTaskId, processes } = get();
  if (!focusedTaskId || !processes[focusedTaskId]) {
    return {};
  }

  const updated: ProcessState = {
    ...processes[focusedTaskId],
    ...patch,
  };

  return {
    ...patch,
    processes: {
      ...processes,
      [focusedTaskId]: updated,
    },
  };
}

// ============================================================
// Action creators (called from index.ts store definition)
// ============================================================

export function createActions(
  set: (partial: Partial<ProcessStore> | ((state: ProcessStore) => Partial<ProcessStore>)) => void,
  get: () => ProcessStore,
) {
  return {
    startProcess: (taskId: string, sessionId: string) => {
      const newProcess: ProcessState = {
        ...initialProcessState,
        taskId,
        sessionId,
        stage: 'planning',
        startTime: Date.now(),
      };
      // Persist mapping + stage for F5 recovery
      persistTaskMapping(sessionId, taskId);
      persistStage(sessionId, 'planning');
      set({
        // Root fields (focused process alias)
        ...newProcess,
        // Multi-task
        focusedTaskId: taskId,
        currentSessionId: sessionId,
        processes: {
          ...get().processes,
          [taskId]: newProcess,
        },
      });
      // Trigger cleanup to enforce limits
      get().cleanupOldProcesses();
    },

    startOrResumeProcess: (taskId: string, sessionId: string) => {
      // DEFENSIVE: Ensure taskId and sessionId are valid strings
      const safeTaskId = String(taskId || '');
      const safeSessionId = String(sessionId || '');
      if (!safeTaskId) return; // Cannot proceed without a taskId

      const { focusedTaskId, processes, currentSessionId } = get();
      // If already focused on this task, just ensure sessionId is synced — NO reset
      if (focusedTaskId === safeTaskId && processes[safeTaskId]) {
        const current = processes[safeTaskId];
        if (current.sessionId !== safeSessionId || currentSessionId !== safeSessionId) {
          const updated = { ...current, sessionId: safeSessionId };
          set({
            sessionId: safeSessionId,
            currentSessionId: safeSessionId,
            processes: { ...processes, [safeTaskId]: updated },
          });
        }
        return;
      }
      // If process already exists for this taskId (e.g. from a previous populate), focus it
      if (processes[safeTaskId]) {
        const process = processes[safeTaskId];
        set({
          focusedTaskId: safeTaskId,
          ...toRootFields(process),
          currentSessionId: safeSessionId,
        });
        return;
      }
      get().startProcess(safeTaskId, safeSessionId);
    },

    setStage: (stage: Parameters<ProcessStore['setStage']>[0]) => {
      const mapped = STAGE_PROGRESS[stage];
      const progress = mapped === -1 ? get().progress : mapped;
      set(updateFocused(get, { stage, progress }));
    },

    setRealTaskId: (taskId: string) => {
      const { focusedTaskId, processes, sessionId } = get();
      if (focusedTaskId && focusedTaskId !== taskId) {
        // Migrate process entry from fake UUID to real backend ID
        const current = processes[focusedTaskId];
        if (current) {
          // Clear old cleanup timer to prevent orphaned map entries
          if (cleanupTimers.has(focusedTaskId)) {
            clearTimeout(cleanupTimers.get(focusedTaskId)!);
            cleanupTimers.delete(focusedTaskId);
          }
          const updated = { ...current, taskId };
          const { [focusedTaskId]: _, ...rest } = processes;
          set({
            taskId,
            focusedTaskId: taskId,
            processes: { ...rest, [taskId]: updated },
          });
          // Update persistence with real task_id
          if (sessionId) {
            persistTaskMapping(sessionId, taskId);
          }
          return;
        }
      }
      // Fallback: just update root taskId
      set({ taskId, focusedTaskId: taskId });
    },

    endProcess: (success: boolean, error?: string) => {
      const { focusedTaskId, processes } = get();
      if (!focusedTaskId) {
        // Fallback: update root fields only
        set({
          stage: success ? 'completed' : 'failed',
          progress: success ? 100 : get().progress,
          error: error ?? null,
        });
        return;
      }

      const current = processes[focusedTaskId];
      const updatedFields: Partial<ProcessState> = {
        stage: success ? 'completed' : 'failed',
        progress: success ? 100 : (current?.progress ?? get().progress),
        error: error ?? null,
      };

      const updated: ProcessState = {
        ...(current ?? initialProcessState),
        ...updatedFields,
      };

      set({
        ...updatedFields,
        processes: { ...processes, [focusedTaskId]: updated },
      });

      // Persist completed/failed stage for F5 recovery
      const sessionId = current?.sessionId ?? get().sessionId;
      if (sessionId) {
        persistStage(sessionId, success ? 'completed' : 'failed');
      }

      // Schedule deferred cleanup: remove from store after 30 minutes
      const taskToCleanup = focusedTaskId;
      if (cleanupTimers.has(taskToCleanup)) {
        clearTimeout(cleanupTimers.get(taskToCleanup)!);
      }
      const MAX_CLEANUP_TIMERS = 30;
      if (cleanupTimers.size >= MAX_CLEANUP_TIMERS) {
        const oldestEntry = cleanupTimers.entries().next().value;
        if (oldestEntry) {
          clearTimeout(oldestEntry[1]);
          cleanupTimers.delete(oldestEntry[0]);
        }
      }
      cleanupTimers.set(
        taskToCleanup,
        setTimeout(() => {
          cleanupTimers.delete(taskToCleanup);
          const { processes: currentProcesses } = get();
          if (currentProcesses[taskToCleanup]) {
            const proc = currentProcesses[taskToCleanup];
            if (proc.stage === 'completed' || proc.stage === 'failed') {
              const { [taskToCleanup]: _, ...remaining } = currentProcesses;
              set({ processes: remaining });
            }
          }
        }, CLEANUP_AFTER_MS),
      );

      // Also run immediate cleanup to enforce max limit
      get().cleanupOldProcesses();
    },

    resetProcess: () => {
      const { focusedTaskId, processes } = get();
      if (focusedTaskId && processes[focusedTaskId]) {
        const { [focusedTaskId]: _, ...remaining } = processes;
        set({
          ...initialProcessState,
          processes: remaining,
          focusedTaskId: null,
        });
      } else {
        set({
          ...initialProcessState,
          processes: get().processes,
          focusedTaskId: null,
        });
      }
    },

    resetDataArrays: () => {
      const dataReset: Partial<ProcessState> = {
        agentMessages: [],
        toolExecutions: [],
        debateRounds: [],
        synthesisEvents: [],
        xskillEvents: [],
        toolCreations: [],
        agentThinkingEvents: [],
        agents: [],
        plan: null,
        deepAnalysis: null,
        selfReflections: [],
        verifications: [],
        completionCheck: null,
        midAnalyses: [],
        critiques: [],
        revisions: [],
      };
      set(updateFocused(get, dataReset));
    },

    // -- Cleanup ----------------------------------------------------

    cleanupOldProcesses: () => {
      const { processes, focusedTaskId } = get();
      const entries = Object.entries(processes);
      const now = Date.now();

      // 1. Remove completed/error processes older than 30 minutes
      const toRemove: string[] = [];
      for (const [taskId, proc] of entries) {
        if (
          (proc.stage === 'completed' || proc.stage === 'failed') &&
          proc.startTime &&
          now - proc.startTime > CLEANUP_AFTER_MS
        ) {
          toRemove.push(taskId);
        }
      }

      // 2. If still over MAX_PROCESSES, remove oldest completed/failed first
      const remainingAfterTimeCleanup = entries.length - toRemove.length;
      if (remainingAfterTimeCleanup > MAX_PROCESSES) {
        const completedEntries = entries
          .filter(
            ([taskId, proc]) =>
              !toRemove.includes(taskId) &&
              (proc.stage === 'completed' || proc.stage === 'failed'),
          )
          .sort((a, b) => (a[1].startTime ?? 0) - (b[1].startTime ?? 0));

        const excessCount = remainingAfterTimeCleanup - MAX_PROCESSES;
        for (let i = 0; i < Math.min(excessCount, completedEntries.length); i++) {
          toRemove.push(completedEntries[i][0]);
        }
      }

      // 3. If STILL over limit, remove oldest regardless of status (but not focused)
      const remainingAfterAll = entries.length - toRemove.length;
      if (remainingAfterAll > MAX_PROCESSES) {
        const sortedByAge = entries
          .filter(([taskId]) => !toRemove.includes(taskId) && taskId !== focusedTaskId)
          .sort((a, b) => (a[1].startTime ?? 0) - (b[1].startTime ?? 0));

        const stillExcess = remainingAfterAll - MAX_PROCESSES;
        for (let i = 0; i < Math.min(stillExcess, sortedByAge.length); i++) {
          toRemove.push(sortedByAge[i][0]);
        }
      }

      if (toRemove.length === 0) return;

      // Clear any pending timers for removed processes
      for (const taskId of toRemove) {
        if (cleanupTimers.has(taskId)) {
          clearTimeout(cleanupTimers.get(taskId)!);
          cleanupTimers.delete(taskId);
        }
      }

      const cleaned = { ...processes };
      for (const taskId of toRemove) {
        delete cleaned[taskId];
      }
      set({ processes: cleaned });
    },

    // UI-01: Session-aware focus switch
    setSession: (sessionId: string) => {
      const safeSessionId = String(sessionId || '');
      if (!safeSessionId) return;
      const { currentSessionId, processes } = get();
      if (currentSessionId !== safeSessionId) {
        const sessionTask = Object.entries(processes).find(
          ([, p]) => p.sessionId === safeSessionId && p.stage !== 'idle',
        );
        if (sessionTask) {
          const [taskId, process] = sessionTask;
          set({
            currentSessionId: safeSessionId,
            focusedTaskId: taskId,
            ...toRootFields(process),
          });
        } else {
          set({
            currentSessionId: safeSessionId,
            focusedTaskId: null,
            ...initialProcessState,
            sessionId: safeSessionId,
          });
        }
      }
    },

    // -- Planning --------------------------------------------------

    setPlan: (plan: Parameters<ProcessStore['setPlan']>[0]) => {
      const normalizedPlan = { ...plan, steps: plan.steps ?? [] };
      set(updateFocused(get, { plan: normalizedPlan }));
    },

    updatePlanStep: (
      stepId: string,
      status: Parameters<ProcessStore['updatePlanStep']>[1],
    ) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const plan = source?.plan ?? get().plan;
      if (!plan) return;

      const updatedPlan = {
        ...plan,
        steps: plan.steps.map((s) => (s.id === stepId ? { ...s, status } : s)),
      };
      set(updateFocused(get, { plan: updatedPlan }));
    },

    // -- Agent Communication ---------------------------------------

    addAgentMessage: (message: Parameters<ProcessStore['addAgentMessage']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.agentMessages ?? get().agentMessages;
      const updated = [...current, message];
      set(updateFocused(get, { agentMessages: updated }));
    },

    // -- Agent Tracking ---------------------------------------------

    addAgent: (agent: Parameters<ProcessStore['addAgent']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.agents ?? get().agents;
      const exists = current.some((a) => a.name === agent.name);
      if (exists) {
        const updated = current.map((a) =>
          a.name === agent.name ? { ...a, ...agent, lastActivityAt: Date.now() } : a,
        );
        set(updateFocused(get, { agents: updated }));
      } else {
        const updated = [...current, agent];
        set(updateFocused(get, { agents: updated }));
      }
    },

    updateAgentStatus: (
      agentName: string,
      status: Parameters<ProcessStore['updateAgentStatus']>[1],
    ) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.agents ?? get().agents;
      const updated = current.map((a) =>
        a.name === agentName ? { ...a, status, lastActivityAt: Date.now() } : a,
      );
      set(updateFocused(get, { agents: updated }));
    },

    getAgents: () => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      return source?.agents ?? get().agents;
    },

    // -- Agent Thinking ---------------------------------------------

    addAgentThinking: (event: Parameters<ProcessStore['addAgentThinking']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.agentThinkingEvents ?? get().agentThinkingEvents;

      if (current.length > 0 && event.content.length > 0) {
        const last = current[current.length - 1];
        if (last.agentName === event.agentName) {
          if (last.content === event.content) {
            return;
          }
          const isStreamContinuation =
            event.content.startsWith(last.content) ||
            (event.content.length > last.content.length &&
              last.content.length > 20 &&
              event.content.includes(last.content.slice(0, 50)));
          if (isStreamContinuation) {
            const updated = [
              ...current.slice(0, -1),
              { ...last, content: event.content, timestamp: Date.now() },
            ];
            set(updateFocused(get, { agentThinkingEvents: updated }));
            return;
          }
        }
      }
      const updated = [...current, { ...event, timestamp: Date.now() }];
      set(updateFocused(get, { agentThinkingEvents: updated }));
    },

    // -- Tool Execution --------------------------------------------

    addToolExecution: (execution: Parameters<ProcessStore['addToolExecution']>[0]) => {
      // BUG-25 FIX: Auto-init process if events arrive before startProcess()
      const state = get();
      if (!state.focusedTaskId && state.currentSessionId) {
        get().startProcess(`auto-${Date.now()}`, state.currentSessionId);
      }
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.toolExecutions ?? get().toolExecutions;
      // DEDUP: Skip if same id already exists OR same toolName+agentName+timestamp combo
      const isDuplicate = current.some(
        (t) =>
          t.id === execution.id ||
          (t.toolName === execution.toolName &&
            t.agentName === execution.agentName &&
            Math.abs(t.timestamp - execution.timestamp) < 1000 &&
            t.status === execution.status),
      );
      if (isDuplicate) return;
      const updated = [...current, execution];
      set(updateFocused(get, { toolExecutions: updated }));
    },

    updateToolExecution: (id: string, updates: Partial<Parameters<ProcessStore['addToolExecution']>[0]>) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.toolExecutions ?? get().toolExecutions;
      const updated = current.map((t) => (t.id === id ? { ...t, ...updates } : t));
      set(updateFocused(get, { toolExecutions: updated }));
    },

    // -- Debates ---------------------------------------------------

    addDebateRound: (round: Parameters<ProcessStore['addDebateRound']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.debateRounds ?? get().debateRounds;
      const existing = current.find((r) => r.round === round.round);
      if (existing) {
        if (
          round.proposals.length > existing.proposals.length ||
          (round.verdict && !existing.verdict)
        ) {
          const updated = current.map((r) =>
            r.round === round.round ? { ...r, ...round } : r,
          );
          set(updateFocused(get, { debateRounds: updated }));
        }
        return;
      }
      const updated = [...current, round];
      set(updateFocused(get, { debateRounds: updated }));
    },

    // -- Synthesis -------------------------------------------------

    addSynthesisEvent: (event: Parameters<ProcessStore['addSynthesisEvent']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.synthesisEvents ?? get().synthesisEvents;
      const updated = [...current, event];
      set(updateFocused(get, { synthesisEvents: updated }));
    },

    // -- XSkill ----------------------------------------------------

    addXSkillEvent: (event: Parameters<ProcessStore['addXSkillEvent']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.xskillEvents ?? get().xskillEvents;
      const isDuplicate = current.some(
        (x) =>
          x.id === event.id ||
          (x.skillName === event.skillName &&
            x.type === event.type &&
            Math.abs(x.timestamp - event.timestamp) < 1000),
      );
      if (isDuplicate) return;
      const updated = [...current, event];
      set(updateFocused(get, { xskillEvents: updated }));
    },

    // -- Tool Creation ---------------------------------------------

    addToolCreation: (event: Parameters<ProcessStore['addToolCreation']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.toolCreations ?? get().toolCreations;
      const updated = [...current, event];
      set(updateFocused(get, { toolCreations: updated }));
    },

    updateToolCreation: (
      id: string,
      updates: Partial<Parameters<ProcessStore['addToolCreation']>[0]>,
    ) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.toolCreations ?? get().toolCreations;
      const updated = current.map((t) => (t.id === id ? { ...t, ...updates } : t));
      set(updateFocused(get, { toolCreations: updated }));
    },

    // -- Deep Reasoning (new) ---------------------------------------

    setDeepAnalysis: (data: Parameters<ProcessStore['setDeepAnalysis']>[0]) => {
      set(updateFocused(get, { deepAnalysis: data }));
    },

    addSelfReflection: (data: Parameters<ProcessStore['addSelfReflection']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.selfReflections ?? get().selfReflections;
      if (data.round != null && current.some((r) => r.round === data.round)) return;
      set(updateFocused(get, { selfReflections: [...current, data] }));
    },

    addVerification: (data: Parameters<ProcessStore['addVerification']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.verifications ?? get().verifications;
      if (data.tool_name && current.some((v) => v.tool_name === data.tool_name)) return;
      set(updateFocused(get, { verifications: [...current, data] }));
    },

    setCompletionCheck: (data: Parameters<ProcessStore['setCompletionCheck']>[0]) => {
      set(updateFocused(get, { completionCheck: data }));
    },

    addMidAnalysis: (data: Parameters<ProcessStore['addMidAnalysis']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.midAnalyses ?? get().midAnalyses;
      if (data.round != null && current.some((m) => m.round === data.round)) return;
      set(updateFocused(get, { midAnalyses: [...current, data] }));
    },

    setReactPhase: (phase: string) => {
      set(updateFocused(get, { reactPhase: phase }));
    },

    addCritique: (data: Parameters<ProcessStore['addCritique']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.critiques ?? get().critiques;
      if (
        data.agent_name &&
        data.timestamp &&
        current.some(
          (c) => c.agent_name === data.agent_name && c.timestamp === data.timestamp,
        )
      )
        return;
      set(updateFocused(get, { critiques: [...current, data] }));
    },

    addRevision: (data: Parameters<ProcessStore['addRevision']>[0]) => {
      const { focusedTaskId, processes } = get();
      const source = focusedTaskId ? processes[focusedTaskId] : null;
      const current = source?.revisions ?? get().revisions;
      if (
        data.agent_name &&
        data.timestamp &&
        current.some(
          (r) => r.agent_name === data.agent_name && r.timestamp === data.timestamp,
        )
      )
        return;
      set(updateFocused(get, { revisions: [...current, data] }));
    },

    // -- Progress --------------------------------------------------

    setProgress: (progress: number) => {
      const clamped = Math.min(100, Math.max(0, progress));
      set(updateFocused(get, { progress: clamped }));
    },

    // -- Multi-task ------------------------------------------------

    setFocusedTask: (taskId: string | null) => {
      if (!taskId) {
        set({ focusedTaskId: null });
        return;
      }
      const process = get().processes[taskId];
      if (!process) {
        set({ focusedTaskId: taskId });
        return;
      }
      set({
        focusedTaskId: taskId,
        ...toRootFields(process),
      });
    },

    getActiveProcesses: () => {
      const { processes } = get();
      return Object.values(processes).filter(
        (p) => p.stage !== 'idle' && p.stage !== 'completed' && p.stage !== 'failed',
      );
    },

    getActiveCount: () => {
      return get().getActiveProcesses().length;
    },
  };
}

// ============================================================
// HMR cleanup helper
// ============================================================

export function clearBatchState(): void {
  _batchedAgentThinking = [];
  _batchFlushScheduled = false;
}
