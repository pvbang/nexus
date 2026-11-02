// ============================================================
// Nexus - Process Store (barrel re-export)
// Keeps backwards-compatible import path: @/stores/processStore
// ============================================================

export {
  useProcessStore,
  batchedAddAgentThinking,
  persistTaskMapping,
  getPersistedTaskId,
  getPersistedStage,
} from './process';

export type { ProcessStore, ProcessActions, MultiTaskFields } from './process';
