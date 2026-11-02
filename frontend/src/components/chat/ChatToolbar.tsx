// ============================================================
// Nexus - ChatToolbar
// Compact toolbar above chat input for profile, model, reasoning
// Batch 7: Added ExportDialog trigger
// ============================================================

import { useState, useEffect, useRef, useCallback } from 'react'
import { ChevronDown, Brain, Cpu, User, Download } from 'lucide-react'
import { PluginSlot } from '../../plugins'
import { cn } from '../../lib/cn'
import { apiGet, apiPost } from '../../api'
import type { ConversationProfile, LLMModel, SessionMessage } from '../../types/api'
import { useSessionStore } from '../../stores/sessionStore'
import { ExportDialog } from '../export'

// Stable empty array to avoid re-render loops from ?? creating new refs
const EMPTY_MESSAGES: never[] = []

// ── Props ─────────────────────────────────────────────────────

export interface ChatToolbarProps {
  /** Current session ID */
  sessionId: string
  /** Callback when profile changes */
  onProfileChange?: (profile: string) => void
  /** Callback when model changes (sends /model command) */
  onModelChange?: (model: string) => void
}

// ── Reasoning levels ──────────────────────────────────────────

const REASONING_LEVELS = ['off', 'low', 'medium', 'high', 'xhigh'] as const
type ReasoningLevel = (typeof REASONING_LEVELS)[number]

const REASONING_LABELS: Record<ReasoningLevel, string> = {
  off: 'Off',
  low: 'Low',
  medium: 'Med',
  high: 'High',
  xhigh: 'Max',
}

const REASONING_COLORS: Record<ReasoningLevel, string> = {
  off: 'text-gray-400 dark:text-gray-600',
  low: 'text-blue-400 dark:text-blue-500',
  medium: 'text-blue-500 dark:text-blue-400',
  high: 'text-violet-500 dark:text-violet-400',
  xhigh: 'text-amber-500 dark:text-amber-400',
}

// ── Dropdown component ────────────────────────────────────────

interface DropdownProps {
  items: Array<{ value: string; label: string; description?: string }>
  onSelect: (value: string) => void
  onClose: () => void
  /** Horizontal alignment relative to parent */
  align?: 'left' | 'right'
}

function Dropdown({ items, onSelect, onClose, align = 'left' }: DropdownProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  return (
    <div
      ref={ref}
      className={cn(
        'absolute bottom-full mb-1 min-w-[220px] max-w-[340px] max-h-[280px] overflow-y-auto z-[100]',
        'bg-white border border-[#e5e7eb] rounded-[10px] shadow-lg py-1',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
        align === 'right' ? 'right-0' : 'left-0',
      )}
    >
      {items.map((item) => (
        <button
          key={item.value}
          onClick={() => {
            onSelect(item.value)
            onClose()
          }}
          className={cn(
            'w-full text-left px-3 py-1.5 text-[12px] transition-colors',
            'hover:bg-[#f4f5f7] dark:hover:bg-[#2a2a45]',
          )}
        >
          <div className="font-medium text-text-primary dark:text-gray-200">{item.label}</div>
          {item.description && (
            <div className="text-[10px] text-text-muted dark:text-gray-500 truncate">
              {item.description}
            </div>
          )}
        </button>
      ))}
      {items.length === 0 && (
        <div className="px-3 py-2 text-[11px] text-text-muted dark:text-gray-500 italic">
          No options available
        </div>
      )}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

export function ChatToolbar({ sessionId, onProfileChange, onModelChange }: ChatToolbarProps) {
  const [profiles, setProfiles] = useState<ConversationProfile[]>([])
  const [models, setModels] = useState<LLMModel[]>([])
  const [activeProfile, setActiveProfile] = useState<string>('')
  const [activeModel, setActiveModel] = useState<string>('')
  const [reasoningLevel, setReasoningLevel] = useState<ReasoningLevel>('xhigh')
  const [showProfiles, setShowProfiles] = useState(false)
  const [showModels, setShowModels] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [isVisible, setIsVisible] = useState(false)

  // Get current session messages for export (stable ref to avoid re-render loops)
  const sessionMessages = useSessionStore((s) => s.messagesBySession[sessionId]) ?? EMPTY_MESSAGES
  const sessionTitle = useSessionStore((s) => {
    const session = s.sessions.find((sess) => sess.session_id === sessionId)
    return session?.title ?? 'Untitled'
  })

  // Fetch profiles and models on mount
  useEffect(() => {
    apiGet<ConversationProfile[]>('/chat/profiles')
      .then((data) => {
        setProfiles(data)
        if (data.length > 0 && !activeProfile) {
          setActiveProfile(data[0].name)
        }
      })
      .catch(() => { /* silent — profiles not available */ })

    apiGet<LLMModel[]>('/chat/models')
      .then((data) => {
        setModels(data)
        if (data.length > 0 && !activeModel) {
          setActiveModel(data[0].name)
        }
      })
      .catch(() => { /* silent — models not available */ })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Profile change handler
  const handleProfileChange = useCallback(
    async (profileName: string) => {
      setActiveProfile(profileName)
      try {
        await apiPost(`/chat/profiles/${sessionId}`, { profile_name: profileName })
        onProfileChange?.(profileName)
      } catch {
        /* silent fail */
      }
    },
    [sessionId, onProfileChange],
  )

  // Model change handler — sends /model command as chat message
  const handleModelChange = useCallback(
    (modelName: string) => {
      setActiveModel(modelName)
      onModelChange?.(modelName)
    },
    [onModelChange],
  )

  // Reasoning level cycle
  const cycleReasoning = useCallback(() => {
    setReasoningLevel((prev) => {
      const idx = REASONING_LEVELS.indexOf(prev)
      const next = REASONING_LEVELS[(idx + 1) % REASONING_LEVELS.length]
      return next
    })
  }, [])

  return (
    <div
      onMouseEnter={() => setIsVisible(true)}
      onMouseLeave={() => {
        if (!showProfiles && !showModels) setIsVisible(false)
      }}
      className={cn(
        'flex items-center gap-3 px-4 sm:px-6 py-1.5 transition-all duration-200 border-t',
        'border-[#e5e7eb] dark:border-[#2a2a45]',
        isVisible || showProfiles || showModels
          ? 'opacity-100 bg-[#fafbfc] dark:bg-[#141425]'
          : 'opacity-40 bg-transparent hover:opacity-70',
      )}
    >
      {/* Left: Profile badge */}
      <div className="relative">
        <button
          onClick={() => {
            setShowProfiles(!showProfiles)
            setShowModels(false)
          }}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[11px] font-medium transition-colors',
            'bg-white border border-[#e5e7eb] text-text-secondary hover:border-[#bfdbfe] hover:text-text-dark',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:text-gray-400 dark:hover:border-[#3b82f6]/40 dark:hover:text-gray-200',
          )}
          title="Switch conversation profile"
        >
          <User className="w-3 h-3" />
          <span className="max-w-[80px] truncate">{activeProfile || 'Default'}</span>
          <ChevronDown className="w-3 h-3 opacity-50" />
        </button>
        {showProfiles && (
          <Dropdown
            items={profiles.map((p) => ({
              value: p.name,
              label: p.name,
              description: p.description,
            }))}
            onSelect={handleProfileChange}
            onClose={() => setShowProfiles(false)}
          />
        )}
      </div>

      {/* Center: Reasoning level indicator */}
      <div className="flex-1 flex justify-center">
        <button
          onClick={cycleReasoning}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[11px] font-medium transition-colors',
            'bg-white border border-[#e5e7eb] hover:border-[#bfdbfe]',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:hover:border-[#3b82f6]/40',
            REASONING_COLORS[reasoningLevel],
          )}
          title={`Reasoning: ${REASONING_LABELS[reasoningLevel]} (click to cycle)`}
        >
          <Brain className="w-3 h-3" />
          <span>Think: {REASONING_LABELS[reasoningLevel]}</span>
        </button>
      </div>

      {/* Export button */}
      <div>
        <button
          onClick={() => setShowExport(true)}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[11px] font-medium transition-colors',
            'bg-white border border-[#e5e7eb] text-text-secondary hover:border-[#bfdbfe] hover:text-text-dark',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:text-gray-400 dark:hover:border-[#3b82f6]/40 dark:hover:text-gray-200',
          )}
          title="Export conversation"
        >
          <Download className="w-3 h-3" />
          <span>Export</span>
        </button>
      </div>

      {/* Plugin slot: chat-toolbar — plugins can inject buttons here */}
      <PluginSlot name="chat-toolbar" wrapper="fragment" />

      {/* Right: Model indicator */}
      <div className="relative">
        <button
          onClick={() => {
            setShowModels(!showModels)
            setShowProfiles(false)
          }}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[11px] font-medium transition-colors',
            'bg-white border border-[#e5e7eb] text-text-secondary hover:border-[#bfdbfe] hover:text-text-dark',
            'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:text-gray-400 dark:hover:border-[#3b82f6]/40 dark:hover:text-gray-200',
          )}
          title="Switch LLM model"
        >
          <Cpu className="w-3 h-3" />
          <span className="max-w-[100px] truncate">{activeModel || 'Auto'}</span>
          <ChevronDown className="w-3 h-3 opacity-50" />
        </button>
        {showModels && (
          <Dropdown
            items={models.map((m) => ({
              value: m.name,
              label: m.name,
              description: `${m.provider} — ${m.description}`,
            }))}
            onSelect={handleModelChange}
            onClose={() => setShowModels(false)}
            align="right"
          />
        )}
      </div>

      {/* Export Dialog */}
      {showExport && (
        <ExportDialog
          open={showExport}
          onClose={() => setShowExport(false)}
          sessionId={sessionId}
          sessionTitle={sessionTitle}
          messages={sessionMessages as unknown as SessionMessage[]}
        />
      )}
    </div>
  )
}
