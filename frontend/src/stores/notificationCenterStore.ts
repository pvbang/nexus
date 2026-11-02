// ============================================================
// Nexus - Notification Center Store (Zustand)
// Persistent notification center — separate from toast notifications
// ============================================================

import { create } from 'zustand'

const STORAGE_KEY = 'nexus-notifications'
const MAX_NOTIFICATIONS = 50

export type NotificationKind =
  | 'task_complete'
  | 'task_failed'
  | 'agent_spawn'
  | 'swarm_done'
  | 'tool_created'
  | 'xskill_learned'
  | 'system'
  | 'info'

export interface CenterNotification {
  id: string
  type: NotificationKind
  title: string
  description?: string
  timestamp: number
  read: boolean
  link?: string
}

interface NotificationCenterState {
  notifications: CenterNotification[]
  unreadCount: number
  /** Notification types that are muted by the user */
  mutedTypes: NotificationKind[]
}

interface NotificationCenterActions {
  addNotification: (notif: Omit<CenterNotification, 'id' | 'timestamp' | 'read'>) => void
  markAsRead: (id: string) => void
  markAllAsRead: () => void
  clearAll: () => void
  removeNotification: (id: string) => void
  muteType: (type: NotificationKind) => void
  unmuteType: (type: NotificationKind) => void
  isMuted: (type: NotificationKind) => boolean
}

function loadFromStorage(): CenterNotification[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as CenterNotification[]
    return Array.isArray(parsed) ? parsed.slice(0, MAX_NOTIFICATIONS) : []
  } catch {
    return []
  }
}

function saveToStorage(notifications: CenterNotification[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(notifications))
  } catch {
    // Storage full or unavailable — silently ignore
  }
}

// ── Muted types persistence ────────────────────────────────────

const PREFS_KEY = 'nexus-notification-prefs'

function loadMutedTypes(): NotificationKind[] {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function saveMutedTypes(types: NotificationKind[]): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(types))
  } catch { /* ignore */ }
}

function computeUnread(notifications: CenterNotification[]): number {
  return notifications.filter((n) => !n.read).length
}

const initialNotifications = loadFromStorage()

export const useNotificationCenterStore = create<NotificationCenterState & NotificationCenterActions>(
  (set, get) => ({
    notifications: initialNotifications,
    unreadCount: computeUnread(initialNotifications),
    mutedTypes: loadMutedTypes(),

    addNotification: (notif) => {
      // Skip muted notification types
      const { mutedTypes } = get()
      if (mutedTypes.includes(notif.type)) return

      const newNotif: CenterNotification = {
        ...notif,
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: Date.now(),
        read: false,
      }
      set((state) => {
        const updated = [newNotif, ...state.notifications].slice(0, MAX_NOTIFICATIONS)
        saveToStorage(updated)
        return { notifications: updated, unreadCount: computeUnread(updated) }
      })
    },

    markAsRead: (id) => {
      set((state) => {
        const updated = state.notifications.map((n) =>
          n.id === id ? { ...n, read: true } : n,
        )
        saveToStorage(updated)
        return { notifications: updated, unreadCount: computeUnread(updated) }
      })
    },

    markAllAsRead: () => {
      set((state) => {
        const updated = state.notifications.map((n) => ({ ...n, read: true }))
        saveToStorage(updated)
        return { notifications: updated, unreadCount: 0 }
      })
    },

    clearAll: () => {
      saveToStorage([])
      set({ notifications: [], unreadCount: 0 })
    },

    removeNotification: (id) => {
      set((state) => {
        const updated = state.notifications.filter((n) => n.id !== id)
        saveToStorage(updated)
        return { notifications: updated, unreadCount: computeUnread(updated) }
      })
    },

    muteType: (type) => {
      set((state) => {
        const updated = [...new Set([...state.mutedTypes, type])]
        saveMutedTypes(updated)
        return { mutedTypes: updated }
      })
    },

    unmuteType: (type) => {
      set((state) => {
        const updated = state.mutedTypes.filter((t) => t !== type)
        saveMutedTypes(updated)
        return { mutedTypes: updated }
      })
    },

    isMuted: (type) => {
      return get().mutedTypes.includes(type)
    },
  }),
)
