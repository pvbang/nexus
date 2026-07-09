// ============================================================
// MekongAI Nexus - TabBar Component
// Multi-tab chat management with context menu, drag reorder
// ============================================================

import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Plus, X, Pin, PinOff, MessageSquare, Loader2,
  MoreHorizontal, CircleDot,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../lib/cn'
import { useTabStore, type ChatTab } from '../../stores/tabStore'
import { useSessionStore } from '../../stores/sessionStore'

// ── TabItem ───────────────────────────────────────────────────

interface TabItemProps {
  tab: ChatTab
  onActivate: (tabId: string) => void
  onClose: (tabId: string) => void
  onContextMenu: (e: React.MouseEvent, tabId: string) => void
}

function TabItem({ tab, onActivate, onClose, onContextMenu }: TabItemProps) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9, width: 0 }}
      transition={{ duration: 0.15 }}
      className={cn(
        'group relative flex items-center gap-1.5 px-3 py-1.5 rounded-md cursor-pointer',
        'text-[12px] font-medium transition-all duration-150 select-none',
        'min-w-0 max-w-[180px] shrink-0',
        tab.isActive
          ? 'bg-white dark:bg-[#252545] text-gray-900 dark:text-white shadow-sm border border-gray-200 dark:border-[#3a3a5a]'
          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1e1e35] hover:text-gray-700 dark:hover:text-gray-200'
      )}
      onClick={() => onActivate(tab.id)}
      onContextMenu={(e) => onContextMenu(e, tab.id)}
    >
      {/* Status indicator */}
      {tab.isProcessing ? (
        <Loader2 className="w-3 h-3 text-blue-500 animate-spin shrink-0" />
      ) : tab.isPinned ? (
        <Pin className="w-3 h-3 text-amber-500 shrink-0" />
      ) : tab.hasUnread ? (
        <CircleDot className="w-3 h-3 text-emerald-500 shrink-0" />
      ) : (
        <MessageSquare className="w-3 h-3 shrink-0 opacity-60" />
      )}

      {/* Title */}
      <span className="truncate flex-1 min-w-0">
        {tab.title}
      </span>

      {/* Close button */}
      {!tab.isPinned && (
        <button
          onClick={(e) => { e.stopPropagation(); onClose(tab.id) }}
          className={cn(
            'w-4 h-4 rounded flex items-center justify-center shrink-0',
            'opacity-0 group-hover:opacity-100 transition-opacity',
            'hover:bg-gray-200 dark:hover:bg-[#3a3a5a]'
          )}
        >
          <X className="w-3 h-3" />
        </button>
      )}

      {/* Active indicator line */}
      {tab.isActive && (
        <motion.div
          layoutId="tab-active-indicator"
          className="absolute bottom-0 left-2 right-2 h-[2px] bg-blue-500 rounded-full"
        />
      )}
    </motion.div>
  )
}

// ── Context Menu ──────────────────────────────────────────────

interface ContextMenuProps {
  x: number
  y: number
  tabId: string
  isPinned: boolean
  onClose: () => void
  onPin: () => void
  onUnpin: () => void
  onCloseTab: () => void
  onCloseOthers: () => void
  onCloseRight: () => void
}

function TabContextMenu({
  x, y, isPinned, onClose, onPin, onUnpin,
  onCloseTab, onCloseOthers, onCloseRight,
}: ContextMenuProps) {
  return (
    <>
      <div className="fixed inset-0 z-[100]" onClick={onClose} />
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={{ duration: 0.1 }}
        className="fixed z-[101] bg-white dark:bg-[#1e1e35] border border-gray-200 dark:border-[#3a3a5a] rounded-lg shadow-xl py-1 w-44"
        style={{ left: x, top: y }}
      >
        {isPinned ? (
          <button onClick={onUnpin} className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-gray-100 dark:hover:bg-[#252545] text-left">
            <PinOff className="w-3.5 h-3.5" /> Unpin Tab
          </button>
        ) : (
          <button onClick={onPin} className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-gray-100 dark:hover:bg-[#252545] text-left">
            <Pin className="w-3.5 h-3.5" /> Pin Tab
          </button>
        )}
        <div className="h-px bg-gray-200 dark:bg-[#3a3a5a] my-0.5" />
        <button onClick={onCloseTab} className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-gray-100 dark:hover:bg-[#252545] text-left">
          <X className="w-3.5 h-3.5" /> Close Tab
        </button>
        <button onClick={onCloseOthers} className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-gray-100 dark:hover:bg-[#252545] text-left">
          <MoreHorizontal className="w-3.5 h-3.5" /> Close Other Tabs
        </button>
        <button onClick={onCloseRight} className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-gray-100 dark:hover:bg-[#252545] text-left">
          <X className="w-3.5 h-3.5" /> Close Tabs to Right
        </button>
      </motion.div>
    </>
  )
}

// ── TabBar ────────────────────────────────────────────────────

export function TabBar() {
  const navigate = useNavigate()
  const tabs = useTabStore(s => s.tabs)
  const addTab = useTabStore(s => s.addTab)
  const removeTab = useTabStore(s => s.removeTab)
  const setActiveTab = useTabStore(s => s.setActiveTab)
  const pinTab = useTabStore(s => s.pinTab)
  const unpinTab = useTabStore(s => s.unpinTab)
  const closeOtherTabs = useTabStore(s => s.closeOtherTabs)
  const closeTabsToRight = useTabStore(s => s.closeTabsToRight)
  const createNewSession = useSessionStore(s => s.createNewSession)

  const [contextMenu, setContextMenu] = useState<{
    x: number; y: number; tabId: string
  } | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)

  const handleNewTab = useCallback(() => {
    const sessionId = createNewSession()
    addTab(sessionId, 'New Chat')
    navigate(`/chat/${sessionId}`)
  }, [createNewSession, addTab, navigate])

  const handleActivate = useCallback((tabId: string) => {
    setActiveTab(tabId)
    const tab = useTabStore.getState().tabs.find(t => t.id === tabId)
    if (tab) {
      navigate(`/chat/${tab.sessionId}`)
    }
  }, [setActiveTab, navigate])

  const handleClose = useCallback((tabId: string) => {
    const nextActiveId = removeTab(tabId)
    if (nextActiveId) {
      const nextTab = useTabStore.getState().tabs.find(t => t.id === nextActiveId)
      if (nextTab) navigate(`/chat/${nextTab.sessionId}`)
    } else {
      // No tabs left — create new
      handleNewTab()
    }
  }, [removeTab, navigate, handleNewTab])

  const handleContextMenu = useCallback((e: React.MouseEvent, tabId: string) => {
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY, tabId })
  }, [])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  if (tabs.length <= 1) return null // Don't show tab bar for single tab

  const contextTab = contextMenu ? tabs.find(t => t.id === contextMenu.tabId) : null

  return (
    <div className="flex items-center gap-1 px-2 py-1 bg-gray-50 dark:bg-[#121225] border-b border-gray-200 dark:border-[#2a2a45] overflow-hidden">
      {/* Scrollable tab list */}
      <div
        ref={scrollRef}
        className="flex items-center gap-0.5 overflow-x-auto scrollbar-none flex-1 min-w-0"
      >
        <AnimatePresence mode="popLayout">
          {tabs.map(tab => (
            <TabItem
              key={tab.id}
              tab={tab}
              onActivate={handleActivate}
              onClose={handleClose}
              onContextMenu={handleContextMenu}
            />
          ))}
        </AnimatePresence>
      </div>

      {/* New tab button */}
      <button
        onClick={handleNewTab}
        className={cn(
          'w-6 h-6 rounded-md flex items-center justify-center shrink-0',
          'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300',
          'hover:bg-gray-200 dark:hover:bg-[#252545] transition-colors'
        )}
        title="New Tab (Ctrl+N)"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>

      {/* Context Menu */}
      <AnimatePresence>
        {contextMenu && contextTab && (
          <TabContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            tabId={contextMenu.tabId}
            isPinned={contextTab.isPinned}
            onClose={closeContextMenu}
            onPin={() => { pinTab(contextMenu.tabId); closeContextMenu() }}
            onUnpin={() => { unpinTab(contextMenu.tabId); closeContextMenu() }}
            onCloseTab={() => { handleClose(contextMenu.tabId); closeContextMenu() }}
            onCloseOthers={() => { closeOtherTabs(contextMenu.tabId); closeContextMenu() }}
            onCloseRight={() => { closeTabsToRight(contextMenu.tabId); closeContextMenu() }}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
