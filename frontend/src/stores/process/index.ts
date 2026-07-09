// ============================================================
// MekongAI Nexus - Process Store (Zustand)
// Tracks real-time orchestration state for transparency UI
// Supports multiple concurrent tasks with focused-task pattern
// ============================================================

import { create } from 'zustand';
import type { ProcessStore } from './types';
import { initialState } from './types';
import { createActions, setStoreGetter, cleanupTimers, clearBatchState } from './actions';

// ============================================================
// Store
// ============================================================

export const useProcessStore = create<ProcessStore>((set, get) => ({
  ...initialState,
  ...createActions(set, get),
}));

// Wire up store getter for batching logic
setStoreGetter(() => useProcessStore.getState());

// ============================================================
// Auto-recover session from localStorage tabs on module load
// ============================================================
try {
  const _tabsRaw = localStorage.getItem('nexus_tabs');
  if (_tabsRaw) {
    const _tabs = JSON.parse(_tabsRaw);
    if (Array.isArray(_tabs)) {
      const _activeTab = _tabs.find((t: { isActive?: boolean }) => t.isActive);
      if (_activeTab?.sessionId && typeof _activeTab.sessionId === 'string') {
        const _ps = useProcessStore.getState();
        if (!_ps.currentSessionId) {
          _ps.setSession(_activeTab.sessionId);
        }
      }
    }
  }
} catch {
  /* ignore corrupt localStorage data */
}

// ============================================================
// Prevents leaked timers when module is hot-reloaded during dev.
// ============================================================
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    cleanupTimers.forEach((timer) => clearTimeout(timer));
    cleanupTimers.clear();
    clearBatchState();
  });
}

// ============================================================
// Re-export public API
// ============================================================
export type { ProcessStore, ProcessActions, MultiTaskFields } from './types';
export { initialProcessState, initialState } from './types';
export { batchedAddAgentThinking } from './actions';
export { persistTaskMapping, getPersistedTaskId, getPersistedStage } from './persistence';
export * from './selectors';
