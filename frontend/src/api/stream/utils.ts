// ============================================================
// Stream Utils - Store accessors and shared helpers
// ============================================================

import { useProcessStore } from '@/stores/processStore'
import { useExecutionStore } from '@/stores/executionStore'
import { useProjectStore } from '@/stores/projectStore'
import { useRealtimeStore } from '@/stores/realtimeStore'
import { useNotificationStore } from '@/stores/notificationStore'

export function _getPS() {
  return useProcessStore.getState()
}

export function _getES() {
  return useExecutionStore.getState()
}

export function _getProjStore() {
  return useProjectStore
}

export function _getRT() {
  return useRealtimeStore.getState()
}

export function _getNotify() {
  return useNotificationStore.getState()
}

/**
 * Safely cast unknown value to typed array, returning empty array if not an array.
 */
export function safeArr<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : []
}
