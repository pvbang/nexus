import { useShallow } from 'zustand/react/shallow'
import type { StoreApi, UseBoundStore } from 'zustand'

/**
 * Helper hook for Zustand stores that uses shallow comparison
 * to prevent re-renders when selecting multiple fields.
 *
 * Usage:
 *   const { events, active } = useShallowStore(useExecutionStore, (s) => ({
 *     events: s.eventsBySession[sessionId] || [],
 *     active: s.activeBySession[sessionId] || false,
 *   }))
 */
export function useShallowStore<S, T>(
  store: UseBoundStore<StoreApi<S>>,
  selector: (state: S) => T,
): T {
  return store(useShallow(selector))
}
