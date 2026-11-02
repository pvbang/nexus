// ============================================================
// Nexus - Notification Store (Zustand)
// ============================================================

import { create } from 'zustand'

export type NotificationType = 'success' | 'error' | 'info' | 'warning'

export interface Notification {
  id: string
  type: NotificationType
  title: string
  message?: string
  duration?: number // ms, 0 = persistent
  timestamp: number
}

interface NotificationState {
  notifications: Notification[]
  add: (type: NotificationType, title: string, message?: string, duration?: number) => void
  remove: (id: string) => void
  clear: () => void
}

export const useNotificationStore = create<NotificationState>((set) => ({
  notifications: [],

  add: (type, title, message, duration = 5000) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const notification: Notification = { id, type, title, message, duration, timestamp: Date.now() }
    set((state) => ({
      notifications: [...state.notifications.slice(-9), notification], // max 10
    }))

    // Auto-dismiss
    if (duration > 0) {
      setTimeout(() => {
        set((state) => ({
          notifications: state.notifications.filter((n) => n.id !== id),
        }))
      }, duration)
    }
  },

  remove: (id) => {
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    }))
  },

  clear: () => set({ notifications: [] }),
}))
