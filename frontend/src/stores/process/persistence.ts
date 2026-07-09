// ============================================================
// MekongAI Nexus - Process Store Persistence (localStorage)
// F5 recovery: taskId → { sessionId, stage } mapping
// ============================================================
// Multi-tab safe: stage is keyed by taskId (not sessionId) so two tabs
// running different tasks of the SAME session no longer overwrite each
// other's stage. Session-scoped lookups resolve to the most recently
// updated task of that session within this tab/origin.

import type { ProcessStage } from '@/types/process';

const LS_KEY_TASK_STATE = 'mekongai_task_state';
const MAX_ENTRIES = 50;

interface TaskStateEntry {
  sessionId: string;
  stage: ProcessStage;
  updatedAt: number;
}

type TaskStateMap = Record<string, TaskStateEntry>;

// Monotonic counter as a tie-breaker so entries persisted within the same
// millisecond keep a deterministic ordering (matters for cap eviction and
// "latest task of session" resolution).
let writeCounter = 0;
function nextStamp(): number {
  // Date.now() dominates across real time; counter disambiguates same-ms writes.
  return Date.now() * 1000 + (writeCounter++ % 1000);
}

function readMap(): TaskStateMap {
  try {
    const raw = localStorage.getItem(LS_KEY_TASK_STATE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) {
      console.warn('[ProcessStore] Corrupted task state in localStorage — resetting');
      return {};
    }
    return parsed as TaskStateMap;
  } catch (err) {
    console.warn('[ProcessStore] readMap parse error:', err);
    return {};
  }
}

function writeMap(map: TaskStateMap): void {
  // Enforce cap: keep the MAX_ENTRIES newest entries by updatedAt.
  const keys = Object.keys(map);
  if (keys.length > MAX_ENTRIES) {
    keys
      .sort((a, b) => map[a].updatedAt - map[b].updatedAt)
      .slice(0, keys.length - MAX_ENTRIES)
      .forEach((k) => delete map[k]);
  }
  try {
    localStorage.setItem(LS_KEY_TASK_STATE, JSON.stringify(map));
  } catch (err) {
    console.warn('[ProcessStore] writeMap failed:', err);
  }
}

/** Resolve the taskId of the most recently updated entry for a session. */
function latestTaskIdForSession(map: TaskStateMap, sessionId: string): string | null {
  let bestTask: string | null = null;
  let bestStamp = -Infinity;
  for (const taskId of Object.keys(map)) {
    const entry = map[taskId];
    if (entry.sessionId === sessionId && entry.updatedAt > bestStamp) {
      bestStamp = entry.updatedAt;
      bestTask = taskId;
    }
  }
  return bestTask;
}

// ============================================================
// Per-task primitives (key by taskId)
// ============================================================

/** Persist stage for a specific task (per-task, never cross-overwrites). */
export function persistTaskStage(taskId: string, sessionId: string, stage: ProcessStage): void {
  if (!taskId || !sessionId) return;
  const map = readMap();
  map[taskId] = { sessionId, stage, updatedAt: nextStamp() };
  writeMap(map);
}

/** Get the stage persisted for a specific task (null if unknown). */
export function getStageByTask(taskId: string): ProcessStage | null {
  if (!taskId) return null;
  const map = readMap();
  return map[taskId]?.stage ?? null;
}

// ============================================================
// Legacy session-keyed API (preserved signatures)
// ============================================================

/** Save sessionId → taskId mapping so right panel can recover on F5 */
export function persistTaskMapping(sessionId: string, taskId: string): void {
  if (!sessionId || !taskId) {
    console.warn('[ProcessStore] persistTaskMapping called with empty sessionId or taskId');
    return;
  }
  const map = readMap();
  const existing = map[taskId];
  map[taskId] = {
    sessionId,
    stage: existing?.stage ?? 'planning',
    updatedAt: nextStamp(),
  };
  writeMap(map);
}

/** Get persisted taskId for a session (latest task in this origin, null if none) */
export function getPersistedTaskId(sessionId: string): string | null {
  if (!sessionId) return null;
  return latestTaskIdForSession(readMap(), sessionId);
}

/** Persist process stage for the current (latest) task of a session. */
export function persistStage(sessionId: string, stage: ProcessStage): void {
  if (!sessionId) return;
  const map = readMap();
  const taskId = latestTaskIdForSession(map, sessionId);
  // Stage is bound to a task; only update if the session has a known task.
  if (!taskId) return;
  map[taskId] = { ...map[taskId], stage, updatedAt: nextStamp() };
  writeMap(map);
}

/** Get persisted stage for the current (latest) task of a session */
export function getPersistedStage(sessionId: string): ProcessStage | null {
  if (!sessionId) return null;
  const map = readMap();
  const taskId = latestTaskIdForSession(map, sessionId);
  return taskId ? map[taskId].stage : null;
}
