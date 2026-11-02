// ============================================================
// Nexus - Terminal Panel
// Web terminal emulator with persistent session tracking.
// Features: session-based cwd persistence, ANSI rendering,
// command history, font size control, file/dir autocomplete,
// keyboard shortcuts, localStorage persistence, auto-focus.
// ============================================================

import { useState, useEffect, useRef, useCallback } from 'react'
import { Trash2, Plus, Minus, Loader2, Copy, Check, RotateCw, FolderOpen, Folder, FileText } from 'lucide-react'
import { AnsiText, stripAnsi } from './AnsiRenderer'
import { apiPost } from '../../api'

// ── Types ──────────────────────────────────────────────────────

interface TerminalLine {
  id: string
  type: 'command' | 'output' | 'error' | 'system'
  text: string
  timestamp: number
}

interface TerminalResponse {
  exit_code: number
  stdout: string
  stderr: string
  success: boolean
  cwd: string
  session_id: string
}

interface CompleteItem {
  name: string
  is_dir: boolean
  full_path: string
}

interface CompleteResponse {
  items: CompleteItem[]
  base_dir: string
}

// ── Constants ──────────────────────────────────────────────────

const MAX_LINES = 1000
const MAX_HISTORY = 50
const COMMAND_TIMEOUT_MS = 90_000
const FONT_SIZE_MIN = 10
const FONT_SIZE_MAX = 20
const FONT_SIZE_DEFAULT = 14
const LS_HISTORY_KEY = 'nexus-terminal-history'
const LS_FONT_SIZE_KEY = 'nexus-terminal-font-size'
const LS_SESSION_KEY = 'nexus-terminal-session-id'
const AUTOCOMPLETE_DEBOUNCE_MS = 200

const COMMON_COMMANDS = [
  'ls', 'ls -la', 'pwd', 'cd', 'cat', 'echo', 'grep', 'find',
  'python', 'python3', 'pip', 'pip3', 'node', 'npm', 'git',
  'docker', 'curl', 'wget', 'mkdir', 'rm', 'cp', 'mv',
  'head', 'tail', 'wc', 'sort', 'uniq', 'chmod', 'chown',
  'ps', 'top', 'df', 'du', 'whoami', 'uname', 'date', 'clear',
]

// Commands that expect file/dir arguments
const FILE_ARG_COMMANDS = [
  'cd', 'ls', 'cat', 'less', 'more', 'head', 'tail', 'vi', 'vim', 'nano',
  'cp', 'mv', 'rm', 'mkdir', 'rmdir', 'chmod', 'chown', 'touch', 'find',
  'grep', 'code', 'python', 'python3', 'node', 'source', 'bash', 'sh',
  'diff', 'wc', 'file', 'stat', 'du', 'tar', 'zip', 'unzip', 'gzip',
]

// ── Helpers ────────────────────────────────────────────────────

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function generateSessionId(): string {
  return crypto.randomUUID()
}

function loadHistory(): string[] {
  try {
    const stored = localStorage.getItem(LS_HISTORY_KEY)
    return stored ? JSON.parse(stored) as string[] : []
  } catch {
    return []
  }
}

function saveHistory(history: string[]): void {
  try {
    localStorage.setItem(LS_HISTORY_KEY, JSON.stringify(history.slice(-MAX_HISTORY)))
  } catch {
    // localStorage full or unavailable — ignore
  }
}

function loadFontSize(): number {
  try {
    const stored = localStorage.getItem(LS_FONT_SIZE_KEY)
    if (stored) {
      const size = parseInt(stored, 10)
      if (size >= FONT_SIZE_MIN && size <= FONT_SIZE_MAX) return size
    }
  } catch {
    // ignore
  }
  return FONT_SIZE_DEFAULT
}

function saveFontSize(size: number): void {
  try {
    localStorage.setItem(LS_FONT_SIZE_KEY, String(size))
  } catch {
    // ignore
  }
}

function loadPersistedSessionId(): string | null {
  try {
    return localStorage.getItem(LS_SESSION_KEY)
  } catch {
    return null
  }
}

function persistSessionId(sessionId: string): void {
  try {
    localStorage.setItem(LS_SESSION_KEY, sessionId)
  } catch {
    // ignore
  }
}

/** Extract short directory name from full path for display */
function shortenCwd(cwd: string): string {
  if (!cwd) return '~'
  // Show last 2 segments for readability
  const parts = cwd.replace(/\\/g, '/').split('/')
  if (parts.length <= 2) return cwd
  return '…/' + parts.slice(-2).join('/')
}

/** Extract the last argument token from input for file completion */
function extractLastArg(input: string): { prefix: string; lastArg: string; isFileContext: boolean } {
  const trimmed = input.trimEnd()
  if (!trimmed) return { prefix: '', lastArg: '', isFileContext: false }

  // Split by spaces, respecting quoted strings
  const parts = trimmed.split(/\s+/)
  const command = parts[0]?.toLowerCase() ?? ''

  // If only command typed (no space after), it's command completion, not file
  if (!input.includes(' ')) {
    return { prefix: '', lastArg: trimmed, isFileContext: false }
  }

  // Check if the command expects file arguments
  const isFileContext = FILE_ARG_COMMANDS.includes(command)

  // Get everything before the last argument
  const lastSpaceIndex = trimmed.lastIndexOf(' ')
  const prefix = trimmed.substring(0, lastSpaceIndex + 1)
  const lastArg = trimmed.substring(lastSpaceIndex + 1)

  return { prefix, lastArg, isFileContext }
}

// ── Component ──────────────────────────────────────────────────

export function TerminalPanel() {
  // Session tracking
  const [sessionId] = useState<string>(() => loadPersistedSessionId() || generateSessionId())
  const [cwd, setCwd] = useState<string>('')
  const [commandCount, setCommandCount] = useState(0)

  const [lines, setLines] = useState<TerminalLine[]>(() => [{
    id: generateId(),
    type: 'system',
    text: '🖥️  Nexus Terminal — Type a command and press Enter. Use ↑↓ for history. Tab for autocomplete.',
    timestamp: Date.now(),
  }])
  const [input, setInput] = useState('')
  const [isExecuting, setIsExecuting] = useState(false)
  const [commandHistory, setCommandHistory] = useState<string[]>(loadHistory)
  const [historyIndex, setHistoryIndex] = useState(-1)
  const [fontSize, setFontSize] = useState(loadFontSize)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [autocomplete, setAutocomplete] = useState<(string | CompleteItem)[]>([])
  const [autocompleteIndex, setAutocompleteIndex] = useState(0)
  const [autocompleteMode, setAutocompleteMode] = useState<'command' | 'file'>('command')
  const [copiedLineId, setCopiedLineId] = useState<string | null>(null)

  const outputRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const completeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Persist session ID on mount
  useEffect(() => {
    persistSessionId(sessionId)
    // Ensure focus on mount
    requestAnimationFrame(() => {
      inputRef.current?.focus()
    })
  }, [sessionId])

  // Auto-scroll to bottom when new lines added
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [lines])

  // Cleanup countdown & autocomplete timer on unmount
  useEffect(() => {
    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current)
      if (completeTimerRef.current) clearTimeout(completeTimerRef.current)
    }
  }, [])

  // ── Auto-focus: regain focus whenever isExecuting changes ────
  useEffect(() => {
    if (!isExecuting) {
      // Use rAF + setTimeout to ensure DOM is settled after state updates
      requestAnimationFrame(() => {
        setTimeout(() => {
          inputRef.current?.focus()
        }, 0)
      })
    }
  }, [isExecuting])

  // ── Line Management ──────────────────────────────────────────

  const addLines = useCallback((newLines: TerminalLine[]) => {
    setLines(prev => {
      const combined = [...prev, ...newLines]
      return combined.length > MAX_LINES
        ? combined.slice(combined.length - MAX_LINES)
        : combined
    })
  }, [])

  const clearTerminal = useCallback(() => {
    setLines([{
      id: generateId(),
      type: 'system',
      text: '🖥️  Terminal cleared.',
      timestamp: Date.now(),
    }])
  }, [])

  // ── Font Size ────────────────────────────────────────────────

  const changeFontSize = useCallback((delta: number) => {
    setFontSize(prev => {
      const next = Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, prev + delta))
      saveFontSize(next)
      return next
    })
  }, [])

  // ── Copy Output Block ────────────────────────────────────────

  const copyLineText = useCallback((line: TerminalLine) => {
    const plainText = stripAnsi(line.text)
    navigator.clipboard.writeText(plainText).then(() => {
      setCopiedLineId(line.id)
      setTimeout(() => setCopiedLineId(null), 2000)
    })
  }, [])

  // ── Build prompt string with cwd ─────────────────────────────

  const promptString = cwd ? `user@nexus:${shortenCwd(cwd)}$` : 'user@nexus:~$'

  // ── File/Dir Autocomplete via API ─────────────────────────────

  const fetchFileCompletions = useCallback(async (partial: string): Promise<CompleteItem[]> => {
    try {
      const response = await apiPost<CompleteResponse>('/terminal/complete', {
        partial,
        session_id: sessionId,
        cwd: cwd || undefined,
      })
      return response.items || []
    } catch {
      return []
    }
  }, [sessionId, cwd])

  // ── Command Execution ────────────────────────────────────────

  const executeCommand = useCallback(async (command: string) => {
    const trimmed = command.trim()
    if (!trimmed) return

    // Add command line with current cwd prompt
    addLines([{
      id: generateId(),
      type: 'command',
      text: trimmed,
      timestamp: Date.now(),
    }])

    // Handle built-in commands
    if (trimmed === 'clear') {
      clearTerminal()
      return
    }

    // Update history
    setCommandHistory(prev => {
      const filtered = prev.filter(c => c !== trimmed)
      const updated = [...filtered, trimmed]
      saveHistory(updated)
      return updated
    })
    setHistoryIndex(-1)

    // Execute via API
    setIsExecuting(true)
    setCountdown(Math.ceil(COMMAND_TIMEOUT_MS / 1000))

    // Start countdown timer
    const startTime = Date.now()
    countdownRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime
      const remaining = Math.max(0, Math.ceil((COMMAND_TIMEOUT_MS - elapsed) / 1000))
      setCountdown(remaining)
      if (remaining <= 0 && countdownRef.current) {
        clearInterval(countdownRef.current)
        countdownRef.current = null
      }
    }, 1000)

    abortRef.current = new AbortController()

    try {
      const response = await Promise.race([
        apiPost<TerminalResponse>('/terminal/execute', {
          command: trimmed,
          timeout: COMMAND_TIMEOUT_MS / 1000,
          session_id: sessionId,
          cwd: cwd || undefined,
        }),
        new Promise<never>((_, reject) => {
          setTimeout(() => reject(new Error('Command timed out')), COMMAND_TIMEOUT_MS)
        }),
      ])

      const outputLines: TerminalLine[] = []

      // Update session state from response
      if (response.cwd) {
        setCwd(response.cwd)
      }
      setCommandCount(prev => prev + 1)

      // Handle stdout
      const stdout = response.stdout || ''
      if (stdout) {
        const stdoutStr = typeof stdout === 'string' ? stdout : JSON.stringify(stdout, null, 2)
        for (const line of stdoutStr.split('\n')) {
          outputLines.push({
            id: generateId(),
            type: 'output',
            text: line,
            timestamp: Date.now(),
          })
        }
      }

      // Handle stderr
      const stderr = response.stderr || ''
      if (stderr) {
        const stderrStr = typeof stderr === 'string' ? stderr : JSON.stringify(stderr, null, 2)
        for (const line of stderrStr.split('\n')) {
          if (line.trim()) {
            outputLines.push({
              id: generateId(),
              type: 'error',
              text: line,
              timestamp: Date.now(),
            })
          }
        }
      }

      // If no output at all, show empty result
      if (outputLines.length === 0 && response.exit_code === 0) {
        outputLines.push({
          id: generateId(),
          type: 'system',
          text: '(command completed with no output)',
          timestamp: Date.now(),
        })
      }

      // Show exit code if non-zero
      if (response.exit_code && response.exit_code !== 0) {
        outputLines.push({
          id: generateId(),
          type: 'error',
          text: `exit code: ${response.exit_code}`,
          timestamp: Date.now(),
        })
      }

      addLines(outputLines)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      addLines([{
        id: generateId(),
        type: 'error',
        text: `Error: ${message}`,
        timestamp: Date.now(),
      }])
    } finally {
      setIsExecuting(false)
      setCountdown(null)
      abortRef.current = null
      if (countdownRef.current) {
        clearInterval(countdownRef.current)
        countdownRef.current = null
      }
      // Focus is handled by the useEffect on isExecuting
    }
  }, [addLines, clearTerminal, sessionId, cwd])

  // ── Reset Session ────────────────────────────────────────────

  const resetSession = useCallback(() => {
    const newSid = generateSessionId()
    persistSessionId(newSid)
    setCwd('')
    setCommandCount(0)
    clearTerminal()
    addLines([{
      id: generateId(),
      type: 'system',
      text: `🔄 New session started (${newSid.slice(0, 8)}…)`,
      timestamp: Date.now(),
    }])
    // Force re-render with new session - we need to reload
    window.location.reload()
  }, [clearTerminal, addLines])

  // ── Autocomplete ─────────────────────────────────────────────

  const updateAutocomplete = useCallback((value: string) => {
    // Clear any pending file completion request
    if (completeTimerRef.current) {
      clearTimeout(completeTimerRef.current)
      completeTimerRef.current = null
    }

    if (!value.trim()) {
      setAutocomplete([])
      setAutocompleteIndex(0)
      setAutocompleteMode('command')
      return
    }

    const { prefix, lastArg, isFileContext } = extractLastArg(value)

    // If there's a space → we're in argument mode
    if (prefix && isFileContext) {
      // File/dir autocomplete — debounce the API call
      setAutocompleteMode('file')
      completeTimerRef.current = setTimeout(async () => {
        const items = await fetchFileCompletions(lastArg)
        if (items.length > 0) {
          setAutocomplete(items)
          setAutocompleteIndex(0)
        } else {
          setAutocomplete([])
        }
      }, AUTOCOMPLETE_DEBOUNCE_MS)
      return
    }

    // Command autocomplete (static list + history)
    setAutocompleteMode('command')
    const lower = value.toLowerCase()
    const matches = COMMON_COMMANDS.filter(cmd => cmd.startsWith(lower))
    // Also match from history
    const historyMatches = commandHistory
      .filter(cmd => cmd.toLowerCase().startsWith(lower) && !matches.includes(cmd))
      .slice(-5)
    const combined = [...historyMatches, ...matches].slice(0, 8)
    setAutocomplete(combined)
    setAutocompleteIndex(0)
  }, [commandHistory, fetchFileCompletions])

  // ── Accept autocomplete selection ─────────────────────────────

  const acceptAutocomplete = useCallback((item: string | CompleteItem) => {
    if (autocompleteMode === 'file' && typeof item !== 'string') {
      // File mode: replace the last argument with the completed name
      const { prefix } = extractLastArg(input)
      const completedName = item.name
      setInput(prefix + completedName)
    } else {
      // Command mode: replace entire input
      const text = typeof item === 'string' ? item : item.name
      setInput(text)
    }
    setAutocomplete([])
    setAutocompleteIndex(0)
    // Keep focus on input
    inputRef.current?.focus()
  }, [autocompleteMode, input])

  // ── Keyboard Handler ─────────────────────────────────────────

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    // Tab → autocomplete
    if (e.key === 'Tab') {
      e.preventDefault()
      if (autocomplete.length > 0) {
        acceptAutocomplete(autocomplete[autocompleteIndex])
      } else {
        // Trigger file completion even if dropdown not showing yet
        const { prefix, lastArg, isFileContext } = extractLastArg(input)
        if (prefix && isFileContext) {
          fetchFileCompletions(lastArg).then(items => {
            if (items.length === 1) {
              // Single match: auto-complete immediately
              setInput(prefix + items[0].name)
            } else if (items.length > 1) {
              setAutocompleteMode('file')
              setAutocomplete(items)
              setAutocompleteIndex(0)
            }
          })
        } else if (!prefix) {
          // Try command completion
          const lower = input.toLowerCase()
          const matches = COMMON_COMMANDS.filter(cmd => cmd.startsWith(lower))
          if (matches.length === 1) {
            setInput(matches[0])
          } else if (matches.length > 1) {
            setAutocompleteMode('command')
            setAutocomplete(matches.slice(0, 8))
            setAutocompleteIndex(0)
          }
        }
      }
      return
    }

    // Enter → execute or accept autocomplete
    if (e.key === 'Enter') {
      e.preventDefault()
      if (autocomplete.length > 0) {
        // Accept the currently highlighted item and stay in input
        acceptAutocomplete(autocomplete[autocompleteIndex])
        return
      }
      if (!isExecuting) {
        executeCommand(input)
        setInput('')
      }
      return
    }

    // Arrow Up → previous history or navigate autocomplete
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (autocomplete.length > 0) {
        setAutocompleteIndex(prev => Math.max(0, prev - 1))
        return
      }
      if (commandHistory.length > 0) {
        const newIndex = historyIndex === -1
          ? commandHistory.length - 1
          : Math.max(0, historyIndex - 1)
        setHistoryIndex(newIndex)
        setInput(commandHistory[newIndex] ?? '')
      }
      return
    }

    // Arrow Down → next history or navigate autocomplete
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (autocomplete.length > 0) {
        setAutocompleteIndex(prev => Math.min(autocomplete.length - 1, prev + 1))
        return
      }
      if (historyIndex === -1) return
      const newIndex = historyIndex + 1
      if (newIndex >= commandHistory.length) {
        setHistoryIndex(-1)
        setInput('')
      } else {
        setHistoryIndex(newIndex)
        setInput(commandHistory[newIndex] ?? '')
      }
      return
    }

    // Ctrl+C → cancel or clear input
    if (e.ctrlKey && e.key === 'c') {
      e.preventDefault()
      if (isExecuting && abortRef.current) {
        abortRef.current.abort()
        addLines([{
          id: generateId(),
          type: 'system',
          text: '^C (request cancelled — server process may still be running)',
          timestamp: Date.now(),
        }])
      } else {
        setInput('')
        setAutocomplete([])
      }
      return
    }

    // Ctrl+L → clear terminal
    if (e.ctrlKey && e.key === 'l') {
      e.preventDefault()
      clearTerminal()
      return
    }

    // Escape → close autocomplete
    if (e.key === 'Escape') {
      setAutocomplete([])
    }
  }, [
    input, isExecuting, commandHistory, historyIndex,
    autocomplete, autocompleteIndex, autocompleteMode,
    executeCommand, clearTerminal, addLines,
    acceptAutocomplete, fetchFileCompletions,
  ])

  // ── Input Change ─────────────────────────────────────────────

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setInput(value)
    setHistoryIndex(-1)
    updateAutocomplete(value)
  }, [updateAutocomplete])

  // ── Click anywhere on terminal → focus input ─────────────────

  const handleContainerClick = useCallback((e: React.MouseEvent) => {
    // Don't steal focus from buttons in the header
    const target = e.target as HTMLElement
    if (target.closest('button')) return

    // Only focus if no text is selected
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) {
      inputRef.current?.focus()
    }
  }, [])

  // ── Line Color ───────────────────────────────────────────────

  function getLineColor(type: TerminalLine['type']): string {
    switch (type) {
      case 'command': return 'text-cyan-400'
      case 'output': return 'text-green-400'
      case 'error': return 'text-red-400'
      case 'system': return 'text-yellow-400'
    }
  }

  // ── Get display text for autocomplete item ────────────────────

  function getAutocompleteText(item: string | CompleteItem): string {
    return typeof item === 'string' ? item : item.name
  }

  function isCompleteItem(item: string | CompleteItem): item is CompleteItem {
    return typeof item !== 'string' && 'is_dir' in item
  }

  // ── Render ───────────────────────────────────────────────────

  return (
    <div
      ref={containerRef}
      onClick={handleContainerClick}
      className="flex flex-col h-full bg-[#0a0a0f] text-green-400 font-mono select-text cursor-text"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0f0f1a] border-b border-[#1a1a2e] shrink-0 cursor-default">
        <div className="flex items-center gap-2">
          <span className="text-[15px]">🖥️</span>
          <span className="text-[14px] font-semibold text-gray-200">Terminal</span>
          {/* Session info badge */}
          <span className="text-[10px] text-gray-500 font-normal" title={`Session: ${sessionId}`}>
            {sessionId.slice(0, 8)}…
          </span>
          {commandCount > 0 && (
            <span className="text-[10px] text-gray-600">
              ({commandCount} cmd{commandCount !== 1 ? 's' : ''})
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* cwd display */}
          {cwd && (
            <div
              className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-blue-400 bg-blue-900/20 rounded mr-1"
              title={`Working directory: ${cwd}`}
            >
              <FolderOpen className="w-3 h-3" />
              <span className="max-w-[200px] truncate">{shortenCwd(cwd)}</span>
            </div>
          )}

          {/* New session button */}
          <button
            onClick={resetSession}
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-400 hover:text-gray-200 hover:bg-[#1a1a2e] rounded transition-colors"
            title="New terminal session"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>

          {/* Clear button */}
          <button
            onClick={clearTerminal}
            className="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-400 hover:text-gray-200 hover:bg-[#1a1a2e] rounded transition-colors"
            title="Clear terminal (Ctrl+L)"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Clear</span>
          </button>

          {/* Font size controls */}
          <button
            onClick={() => changeFontSize(1)}
            className="flex items-center justify-center w-7 h-7 text-gray-400 hover:text-gray-200 hover:bg-[#1a1a2e] rounded transition-colors"
            title="Increase font size"
            disabled={fontSize >= FONT_SIZE_MAX}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
          <span className="text-[11px] text-gray-500 w-6 text-center tabular-nums">{fontSize}</span>
          <button
            onClick={() => changeFontSize(-1)}
            className="flex items-center justify-center w-7 h-7 text-gray-400 hover:text-gray-200 hover:bg-[#1a1a2e] rounded transition-colors"
            title="Decrease font size"
            disabled={fontSize <= FONT_SIZE_MIN}
          >
            <Minus className="w-3.5 h-3.5" />
          </button>

          {/* Execution status */}
          {isExecuting && (
            <div className="flex items-center gap-1.5 ml-2 px-2 py-0.5 bg-blue-900/30 rounded text-blue-400">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span className="text-[11px] tabular-nums">
                {countdown !== null ? `${countdown}s` : '...'}
              </span>
            </div>
          )}

          {/* Live indicator */}
          {!isExecuting && (
            <div className="flex items-center gap-1.5 ml-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]" />
              <span className="text-[11px] text-gray-500">Ready</span>
            </div>
          )}
        </div>
      </div>

      {/* Output Area */}
      <div
        ref={outputRef}
        className="flex-1 overflow-y-auto overflow-x-hidden p-3 min-h-0"
        style={{ fontSize: `${fontSize}px`, lineHeight: '1.5' }}
      >
        {lines.map((line) => (
          <div
            key={line.id}
            className="group flex items-start gap-0 hover:bg-white/[0.02] relative"
          >
            {/* Line content */}
            <div className={`flex-1 whitespace-pre-wrap break-all ${getLineColor(line.type)}`}>
              {line.type === 'command' && (
                <span className="text-cyan-400 select-none">
                  {promptString + ' '}
                </span>
              )}
              {line.type === 'output' || line.type === 'error' ? (
                <AnsiText text={line.text} />
              ) : line.type !== 'command' ? (
                line.text
              ) : (
                line.text
              )}
            </div>

            {/* Copy button (visible on hover) */}
            {(line.type === 'output' || line.type === 'error') && (
              <button
                onClick={(e) => { e.stopPropagation(); copyLineText(line) }}
                className="opacity-0 group-hover:opacity-100 shrink-0 ml-2 p-1 text-gray-600 hover:text-gray-300 transition-opacity"
                title="Copy line"
              >
                {copiedLineId === line.id
                  ? <Check className="w-3 h-3 text-emerald-400" />
                  : <Copy className="w-3 h-3" />
                }
              </button>
            )}
          </div>
        ))}

        {/* Executing indicator in output area */}
        {isExecuting && (
          <div className="flex items-center gap-2 text-gray-500 mt-1">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <span className="text-[12px]">Executing command...</span>
          </div>
        )}
      </div>

      {/* Input Area */}
      <div className="relative shrink-0 border-t border-[#1a1a2e] bg-[#0a0a0f]">
        {/* Autocomplete dropdown */}
        {autocomplete.length > 0 && (
          <div className="absolute bottom-full left-0 right-0 bg-[#12121f] border border-[#1a1a2e] shadow-lg max-h-48 overflow-y-auto z-10">
            {autocomplete.map((item, i) => {
              const text = getAutocompleteText(item)
              const isFile = isCompleteItem(item)
              const isDir = isFile && item.is_dir
              return (
                <button
                  key={text + i}
                  className={`w-full text-left px-4 py-1.5 text-[13px] font-mono transition-colors flex items-center gap-2 ${
                    i === autocompleteIndex
                      ? 'bg-blue-900/40 text-blue-300'
                      : 'text-gray-400 hover:bg-[#1a1a2e] hover:text-gray-200'
                  }`}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    acceptAutocomplete(item)
                  }}
                >
                  {/* Icon for file/dir suggestions */}
                  {isFile ? (
                    isDir ? (
                      <Folder className="w-3.5 h-3.5 text-yellow-400 shrink-0" />
                    ) : (
                      <FileText className="w-3.5 h-3.5 text-gray-500 shrink-0" />
                    )
                  ) : (
                    <span className="w-3.5 h-3.5 shrink-0 text-center text-[10px] text-gray-600">$</span>
                  )}
                  <span className={isDir ? 'text-yellow-300' : ''}>{text}</span>
                  {isFile && (
                    <span className="ml-auto text-[10px] text-gray-600">
                      {isDir ? 'dir' : 'file'}
                    </span>
                  )}
                </button>
              )
            })}
            <div className="px-4 py-1 text-[10px] text-gray-600 border-t border-[#1a1a2e]">
              ↑↓ navigate · Tab/Enter accept · Esc close
            </div>
          </div>
        )}

        <div className="flex items-center px-3 py-2" style={{ fontSize: `${fontSize}px` }}>
          <span className="text-cyan-400 shrink-0 select-none mr-1">{promptString}</span>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            disabled={isExecuting}
            className="flex-1 bg-transparent text-green-400 outline-none caret-green-400 font-mono placeholder:text-gray-700 disabled:opacity-50"
            style={{ fontSize: `${fontSize}px` }}
            placeholder={isExecuting ? 'Waiting for command to finish...' : 'Type a command...'}
            spellCheck={false}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            autoFocus
          />
          {/* Blinking cursor indicator when input is empty and focused */}
          {!input && !isExecuting && (
            <span className="w-[8px] h-[1.2em] bg-green-400 animate-pulse shrink-0" />
          )}
        </div>
      </div>
    </div>
  )
}
