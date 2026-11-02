// ============================================================
// Nexus - Tab Store (Zustand)
// Manages multi-tab chat sessions with tab bar state
// ============================================================

import { create } from 'zustand'

export interface ChatTab {
  id: string
  sessionId: string
  title: string
  isActive: boolean
  isPinned: boolean
  hasUnread: boolean
  isProcessing: boolean
  createdAt: number
}

interface TabState {
  tabs: ChatTab[]
  activeTabId: string | null
  maxTabs: number

  // Actions
  addTab: (sessionId: string, title?: string) => string
  removeTab: (tabId: string) => string | null
  setActiveTab: (tabId: string) => void
  updateTabTitle: (tabId: string, title: string) => void
  updateTabProcessing: (tabId: string, isProcessing: boolean) => void
  setTabUnread: (tabId: string, hasUnread: boolean) => void
  pinTab: (tabId: string) => void
  unpinTab: (tabId: string) => void
  reorderTabs: (fromIndex: number, toIndex: number) => void
  getTabBySessionId: (sessionId: string) => ChatTab | undefined
  closeOtherTabs: (tabId: string) => void
  closeTabsToRight: (tabId: string) => void
}

const MAX_TABS = 12
const STORAGE_KEY = 'nexus_tabs'

function loadTabs(): ChatTab[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored)
      if (!Array.isArray(parsed)) {
        console.warn('[TabStore] Corrupted tab data in localStorage — resetting')
        localStorage.removeItem(STORAGE_KEY)
        return []
      }
      return parsed
    }
  } catch (err) {
    console.warn('[TabStore] Failed to load tabs from localStorage:', err)
    // Remove corrupted data to prevent repeated failures
    try { localStorage.removeItem(STORAGE_KEY) } catch { /* ignore */ }
  }
  return []
}

function saveTabs(tabs: ChatTab[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs))
  } catch (err) {
    console.warn('[TabStore] Failed to save tabs to localStorage:', err)
  }
}

// BUG-20 FIX: Debounced save for high-frequency updates (processing state)
let _saveTimeout: ReturnType<typeof setTimeout> | null = null
function debouncedSaveTabs(tabs: ChatTab[]) {
  if (_saveTimeout) clearTimeout(_saveTimeout)
  _saveTimeout = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs))
    } catch {
      // Ignore localStorage quota errors
    }
  }, 500)
}

const _initialTabs = loadTabs()

export const useTabStore = create<TabState>((set, get) => ({
  tabs: _initialTabs,
  activeTabId: _initialTabs.find(t => t.isActive)?.id || null,
  maxTabs: MAX_TABS,

  addTab: (sessionId: string, title?: string) => {
    const { tabs, maxTabs } = get()

    // Check if tab already exists for this session
    const existing = tabs.find(t => t.sessionId === sessionId)
    if (existing) {
      get().setActiveTab(existing.id)
      return existing.id
    }

    // Enforce max tabs
    if (tabs.length >= maxTabs) {
      // Close oldest non-pinned tab
      const unpinnedTabs = tabs.filter(t => !t.isPinned)
      if (unpinnedTabs.length > 0) {
        get().removeTab(unpinnedTabs[0].id)
      }
    }

    const tabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const newTab: ChatTab = {
      id: tabId,
      sessionId,
      title: title || 'New Chat',
      isActive: true,
      isPinned: false,
      hasUnread: false,
      isProcessing: false,
      createdAt: Date.now(),
    }

    set(state => {
      const updatedTabs = state.tabs.map(t => ({ ...t, isActive: false }))
      const result = [...updatedTabs, newTab]
      saveTabs(result)
      return { tabs: result, activeTabId: tabId }
    })

    return tabId
  },

  removeTab: (tabId: string) => {
    const { tabs, activeTabId } = get()
    const tabIndex = tabs.findIndex(t => t.id === tabId)
    if (tabIndex === -1) return activeTabId

    const isActiveTab = tabId === activeTabId
    let remainingTabs = tabs.filter(t => t.id !== tabId)

    if (remainingTabs.length === 0) {
      set({ tabs: [], activeTabId: null })
      saveTabs([])
      return null
    }

    let newActiveId = activeTabId
    if (isActiveTab) {
      // Activate nearest tab (prefer right, then left)
      const newIndex = Math.min(tabIndex, remainingTabs.length - 1)
      newActiveId = remainingTabs[newIndex].id
      // Create new object references so React detects the isActive change
      remainingTabs = remainingTabs.map(t => ({ ...t, isActive: t.id === newActiveId }))
    }

    set({ tabs: remainingTabs, activeTabId: newActiveId })
    saveTabs(remainingTabs)
    return newActiveId
  },

  setActiveTab: (tabId: string) => {
    set(state => {
      const updatedTabs = state.tabs.map(t => ({
        ...t,
        isActive: t.id === tabId,
        hasUnread: t.id === tabId ? false : t.hasUnread,
      }))
      saveTabs(updatedTabs)
      return { tabs: updatedTabs, activeTabId: tabId }
    })
  },

  updateTabTitle: (tabId: string, title: string) => {
    set(state => {
      const updatedTabs = state.tabs.map(t =>
        t.id === tabId ? { ...t, title: title.slice(0, 60) } : t
      )
      saveTabs(updatedTabs)
      return { tabs: updatedTabs }
    })
  },

  updateTabProcessing: (tabId: string, isProcessing: boolean) => {
    set(state => {
      const updatedTabs = state.tabs.map(t =>
        t.id === tabId ? { ...t, isProcessing } : t
      )
      debouncedSaveTabs(updatedTabs)
      return { tabs: updatedTabs }
    })
  },

  setTabUnread: (tabId: string, hasUnread: boolean) => {
    set(state => {
      const updatedTabs = state.tabs.map(t =>
        t.id === tabId ? { ...t, hasUnread } : t
      )
      debouncedSaveTabs(updatedTabs)
      return { tabs: updatedTabs }
    })
  },

  pinTab: (tabId: string) => {
    set(state => {
      const updatedTabs = state.tabs.map(t =>
        t.id === tabId ? { ...t, isPinned: true } : t
      )
      // Move pinned tabs to front
      updatedTabs.sort((a, b) => {
        if (a.isPinned && !b.isPinned) return -1
        if (!a.isPinned && b.isPinned) return 1
        return 0
      })
      saveTabs(updatedTabs)
      return { tabs: updatedTabs }
    })
  },

  unpinTab: (tabId: string) => {
    set(state => {
      const updatedTabs = state.tabs.map(t =>
        t.id === tabId ? { ...t, isPinned: false } : t
      )
      saveTabs(updatedTabs)
      return { tabs: updatedTabs }
    })
  },

  reorderTabs: (fromIndex: number, toIndex: number) => {
    set(state => {
      const newTabs = [...state.tabs]
      const [moved] = newTabs.splice(fromIndex, 1)
      newTabs.splice(toIndex, 0, moved)
      saveTabs(newTabs)
      return { tabs: newTabs }
    })
  },

  getTabBySessionId: (sessionId: string) => {
    return get().tabs.find(t => t.sessionId === sessionId)
  },

  closeOtherTabs: (tabId: string) => {
    set(state => {
      // Use .map() to create new object references (React reactivity requirement)
      const kept = state.tabs
        .filter(t => t.id === tabId || t.isPinned)
        .map(t => ({ ...t, isActive: t.id === tabId }))
      saveTabs(kept)
      return { tabs: kept, activeTabId: tabId }
    })
  },

  closeTabsToRight: (tabId: string) => {
    set(state => {
      const idx = state.tabs.findIndex(t => t.id === tabId)
      if (idx === -1) return state
      const kept = state.tabs.filter((t, i) => i <= idx || t.isPinned)
      saveTabs(kept)
      return { tabs: kept }
    })
  },
}))
