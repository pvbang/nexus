// ============================================================
// Nexus - ArtifactsPanel (Session-grouped view) [OPTIMIZED]
// - File content LRU cache (no re-fetch on switch back)
// - Reduced refresh: 30s + only when tab visible
// - Shallow compare before re-render
// - Skeleton loading UI
// - Large file protection (>500KB → metadata only)
// ============================================================

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  FolderOpen, Download, Eye, RefreshCw, Copy,
  ChevronDown, ChevronRight, HardDrive, Search,
  Brain, File, MessageSquare, Archive, List, FileText,
} from 'lucide-react'
import { apiGet, downloadWithAuth } from '../api'
import { MarkdownRenderer } from './ui/MarkdownRenderer'
import { FileTree } from './FileTree'
import { useFileContentCache, clearFileContentCache } from '../hooks/useFileContentCache'
import { MarkdownOutlineView } from './artifacts/MarkdownOutlineView'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ArtifactFile {
  path: string
  name: string
  size: number
  modified: number
  type: string
  is_binary?: boolean
  is_context_doc?: boolean
  task_id?: string
  download_url?: string
}

interface TaskGroup {
  task_id: string
  task_title?: string
  file_count: number
  total_size: number
  latest_modified: number
  context_docs: ArtifactFile[]
  artifacts: ArtifactFile[]
  files: ArtifactFile[]
}

interface SessionGroup {
  session_id: string
  session_title: string
  task_count: number
  file_count: number
  total_size: number
  latest_modified: number
  tasks: TaskGroup[]
}

interface GroupedResponse {
  sessions: SessionGroup[]
  orphan_tasks: TaskGroup[]
  global: ArtifactFile[]
  total_files: number
}

interface ArtifactContent {
  content: string | null
  is_binary: boolean
  download_url?: string
  message?: string
  type: string
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const REFRESH_INTERVAL_MS = 30_000 // 30 seconds instead of 8s
const LARGE_FILE_THRESHOLD = 500 * 1024 // 500KB

const typeConfig: Record<string, { icon: string; color: string }> = {
  markdown: { icon: '📝', color: 'text-blue-600 dark:text-blue-400' },
  text: { icon: '📄', color: 'text-text-secondary' },
  json: { icon: '📋', color: 'text-amber-600 dark:text-amber-400' },
  python: { icon: '🐍', color: 'text-emerald-600 dark:text-emerald-400' },
  javascript: { icon: '🟨', color: 'text-yellow-600' },
  typescript: { icon: '🔷', color: 'text-blue-600 dark:text-blue-400' },
  html: { icon: '🌐', color: 'text-orange-600 dark:text-orange-400' },
  css: { icon: '🎨', color: 'text-pink-600 dark:text-pink-400' },
  csv: { icon: '📊', color: 'text-emerald-600 dark:text-emerald-400' },
  yaml: { icon: '⚙️', color: 'text-purple-600 dark:text-purple-400' },
  bash: { icon: '💻', color: 'text-text-dark dark:text-[#e4e4ed]' },
  docx: { icon: '📄', color: 'text-blue-600 dark:text-blue-400' },
  excel: { icon: '📊', color: 'text-emerald-600 dark:text-emerald-400' },
  pdf: { icon: '📕', color: 'text-red-600 dark:text-red-400' },
  image: { icon: '🖼️', color: 'text-purple-600 dark:text-purple-400' },
  audio: { icon: '🎵', color: 'text-pink-600 dark:text-pink-400' },
  file: { icon: '📁', color: 'text-text-muted' },
}

import { API_URL } from '../lib/constants'
const BINARY_TYPES = new Set(['docx', 'excel', 'pdf', 'image', 'audio'])

const formatSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const formatTime = (ts: number) => {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
}

const getType = (type: string) => typeConfig[type] || { icon: '📄', color: 'text-text-muted' }

// ---------------------------------------------------------------------------
// Skeleton Component
// ---------------------------------------------------------------------------

function ContentSkeleton() {
  return (
    <div className="animate-pulse space-y-3 p-5">
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-3/4" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-full" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-5/6" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-2/3" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-full" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-4/5" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-1/2" />
      <div className="h-4 bg-gray-200 dark:bg-[#2a2a45] rounded w-full" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shallow compare for GroupedResponse to skip unnecessary re-renders
// ---------------------------------------------------------------------------

function isDataEqual(prev: GroupedResponse | null, next: GroupedResponse): boolean {
  if (!prev) return false
  if (prev.total_files !== next.total_files) return false
  if (prev.sessions.length !== next.sessions.length) return false
  if (prev.orphan_tasks.length !== next.orphan_tasks.length) return false
  if (prev.global.length !== next.global.length) return false

  // Compare latest_modified for each session
  for (let i = 0; i < prev.sessions.length; i++) {
    if (prev.sessions[i].session_id !== next.sessions[i].session_id) return false
    if (prev.sessions[i].latest_modified !== next.sessions[i].latest_modified) return false
    if (prev.sessions[i].file_count !== next.sessions[i].file_count) return false
  }

  return true
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ArtifactsPanel() {
  const [data, setData] = useState<GroupedResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  // Selected file state
  const [selectedFile, setSelectedFile] = useState<ArtifactFile | null>(null)
  const [fileContent, setFileContent] = useState('')
  const [contentInfo, setContentInfo] = useState<ArtifactContent | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [outlineMode, setOutlineMode] = useState(false)

  // Collapsed groups — default ALL collapsed
  const [collapsedSessions, setCollapsedSessions] = useState<Set<string>>(new Set())
  const [collapsedTasks, setCollapsedTasks] = useState<Set<string>>(new Set())
  const [initializedCollapse, setInitializedCollapse] = useState(false)

  // File content cache
  const { fetchContent } = useFileContentCache()

  // Ref for data comparison
  const dataRef = useRef<GroupedResponse | null>(null)

  const loadData = useCallback(async (showRefresh = false) => {
    if (showRefresh) setRefreshing(true)
    try {
      const result = await apiGet<GroupedResponse>('/artifacts/grouped')
      // Normalize
      if (!result.sessions) result.sessions = []
      if (!result.orphan_tasks) result.orphan_tasks = []
      if (!result.global) result.global = []
      // Sort sessions: newest first
      result.sessions.sort((a, b) => (b.latest_modified || 0) - (a.latest_modified || 0))

      // Shallow compare: skip re-render if nothing changed
      if (!isDataEqual(dataRef.current, result)) {
        setData(result)
        dataRef.current = result
      }

      // Default: all sessions collapsed on first load
      if (!initializedCollapse && result.sessions.length > 0) {
        setCollapsedSessions(new Set(result.sessions.map(s => s.session_id)))
        setInitializedCollapse(true)
      }
    } catch (e) {
      console.error('Failed to load artifacts:', e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [initializedCollapse])

  // Auto-refresh: 30s + only when tab visible
  useEffect(() => {
    void loadData()

    let interval: ReturnType<typeof setInterval> | null = null

    const startInterval = () => {
      if (interval) return
      interval = setInterval(() => {
        if (!document.hidden) {
          void loadData()
        }
      }, REFRESH_INTERVAL_MS)
    }

    const stopInterval = () => {
      if (interval) {
        clearInterval(interval)
        interval = null
      }
    }

    const handleVisibility = () => {
      if (document.hidden) {
        stopInterval()
      } else {
        // Refresh immediately when tab becomes visible
        void loadData()
        startInterval()
      }
    }

    startInterval()
    document.addEventListener('visibilitychange', handleVisibility)

    return () => {
      stopInterval()
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [loadData])

  const viewFile = useCallback(async (file: ArtifactFile) => {
    setSelectedFile(file)
    setCopied(false)
    setOutlineMode(false)

    // Large file protection: don't fetch content for files > 500KB
    if (file.size > LARGE_FILE_THRESHOLD && !BINARY_TYPES.has(file.type)) {
      setContentInfo({ content: null, is_binary: false, type: file.type, message: 'File quá lớn' })
      setFileContent('')
      setFileLoading(false)
      return
    }

    setFileLoading(true)
    setContentInfo(null)

    try {
      const result = await fetchContent(file)
      setContentInfo(result)
      setFileContent(result.content || '')
    } catch {
      setFileContent('Failed to load file content')
      setContentInfo(null)
    } finally {
      setFileLoading(false)
    }
  }, [fetchContent])

  const downloadFile = useCallback((file: ArtifactFile) => {
    const encodedPath = file.path.split('/').map(encodeURIComponent).join('/')
    const url = file.task_id
      ? `${API_URL}/api/v1/artifacts/task/${file.task_id}/download/${encodedPath}`
      : `${API_URL}/api/v1/artifacts/download/${encodedPath}`
    void downloadWithAuth(url, file.name)
  }, [])

  const downloadTaskZip = useCallback((taskId: string) => {
    void downloadWithAuth(
      `${API_URL}/api/v1/artifacts/task/${taskId}/download-zip`,
      `task-${taskId}.zip`,
    )
  }, [])

  const downloadSessionZip = useCallback((sessionId: string) => {
    void downloadWithAuth(
      `${API_URL}/api/v1/artifacts/session/${sessionId}/download-zip`,
      `session-${sessionId.slice(0, 8)}.zip`,
    )
  }, [])

  const copyContent = useCallback(() => {
    navigator.clipboard.writeText(fileContent)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [fileContent])

  const toggleSession = useCallback((sessionId: string) => {
    setCollapsedSessions(prev => {
      const next = new Set(prev)
      if (next.has(sessionId)) next.delete(sessionId)
      else next.add(sessionId)
      return next
    })
  }, [])

  const toggleTask = useCallback((taskId: string) => {
    setCollapsedTasks(prev => {
      const next = new Set(prev)
      if (next.has(taskId)) next.delete(taskId)
      else next.add(taskId)
      return next
    })
  }, [])

  const handleManualRefresh = useCallback(() => {
    clearFileContentCache()
    void loadData(true)
  }, [loadData])

  const isBinary = (file: ArtifactFile | null, info: ArtifactContent | null) => {
    if (info?.is_binary !== undefined) return info.is_binary
    if (file?.is_binary !== undefined) return file.is_binary
    if (file?.type && BINARY_TYPES.has(file.type)) return true
    return false
  }

  // Filter by search
  const filterFiles = (files: ArtifactFile[]) => {
    if (!searchQuery.trim()) return files
    const q = searchQuery.toLowerCase()
    return files.filter(f =>
      f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-sm text-text-muted dark:text-[#6b6b85]">Loading artifacts...</p>
        </div>
      </div>
    )
  }

  const fileBinary = isBinary(selectedFile, contentInfo)
  const isLargeFile = selectedFile && selectedFile.size > LARGE_FILE_THRESHOLD && !BINARY_TYPES.has(selectedFile.type)

  return (
    <div className="flex h-full bg-white dark:bg-[#0f0f1a]">
      {/* ── Left: File Tree ── */}
      <div className="w-full sm:w-80 border-r border-border-light dark:border-[#2a2a45] flex flex-col shrink-0 bg-gradient-to-b from-white to-gray-50/30 dark:from-[#0f0f1a] dark:to-[#0a0a14]">
        {/* Header */}
        <div className="px-4 py-3 border-b border-border-light dark:border-[#2a2a45] bg-white/60 dark:bg-[#0f0f1a]/60 backdrop-blur-sm">
          <div className="flex items-center justify-between mb-2.5">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-brand-500 to-brand-600 flex items-center justify-center shadow-sm shadow-brand-500/20">
                <HardDrive className="w-3.5 h-3.5 text-white" />
              </div>
              <h2 className="font-display text-base font-bold text-text-dark dark:text-[#e4e4ed]">
                Artifacts
              </h2>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-brand-50 dark:bg-brand-900/20 text-brand-600 dark:text-brand-400 font-semibold border border-brand-100 dark:border-brand-800/30">
                {data?.total_files ?? 0} files
              </span>
              <button
                onClick={handleManualRefresh}
                className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] transition-colors"
                title="Refresh (clear cache)"
              >
                <RefreshCw className={`w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85] ${refreshing ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
            <input
              type="text"
              placeholder="Search files..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-xs rounded-xl border border-border-light dark:border-[#2a2a45] bg-gray-50 dark:bg-[#1a1a2e] text-text-dark dark:text-[#e4e4ed] placeholder-text-muted dark:placeholder-[#6b6b85] focus:ring-2 focus:ring-brand-500/20 focus:border-brand-500 outline-none transition-all"
            />
          </div>
        </div>

        {/* File tree */}
        <div className="flex-1 overflow-y-auto">
          {(!data || (data.sessions.length === 0 && (!data.orphan_tasks || data.orphan_tasks.length === 0) && data.global.length === 0)) && (
            <div className="text-center py-16 animate-fade-in">
              <div className="text-4xl mb-3">📂</div>
              <p className="text-sm text-text-secondary dark:text-[#a0a0b8] font-medium">Chưa có artifacts</p>
              <p className="text-xs text-text-muted dark:text-[#6b6b85] mt-1">Agents sẽ tạo files khi xử lý task</p>
            </div>
          )}

          {/* Session groups */}
          {data?.sessions.map(session => {
            const isSessionCollapsed = collapsedSessions.has(session.session_id)

            // Check if any files match search
            const allSessionFiles = session.tasks.flatMap(t => [...t.artifacts, ...t.context_docs])
            const filteredSessionFiles = filterFiles(allSessionFiles)
            if (searchQuery && filteredSessionFiles.length === 0) return null

            return (
              <div key={session.session_id} className="mb-1">
                {/* Session card container */}
                <div className="mx-2 my-1.5 rounded-lg border border-border-light/60 dark:border-[#2a2a45]/80 bg-white/50 dark:bg-[#12121f]/50 overflow-hidden">
                  {/* Session header */}
                  <button
                    onClick={() => toggleSession(session.session_id)}
                    className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] transition-colors text-left"
                  >
                    {isSessionCollapsed
                      ? <ChevronRight className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85] shrink-0" />
                      : <ChevronDown className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85] shrink-0" />
                    }
                    <MessageSquare className="w-3.5 h-3.5 text-brand-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-semibold text-text-dark dark:text-[#e4e4ed] truncate block leading-tight">
                        {session.session_title}
                      </span>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                          {session.file_count} files · {formatSize(session.total_size)}
                        </span>
                        <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                          {formatTime(session.latest_modified)}
                        </span>
                      </div>
                    </div>
                    <span
                      role="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        if (session.tasks.length === 1) {
                          downloadTaskZip(session.tasks[0].task_id)
                        } else {
                          downloadSessionZip(session.session_id)
                        }
                      }}
                      className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors shrink-0"
                      title="Download all as ZIP"
                    >
                      <Archive className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
                    </span>
                  </button>

                  {/* Session content — tasks inside */}
                  {!isSessionCollapsed && (
                    <div className="pb-1.5 border-t border-border-light/30 dark:border-[#2a2a45]/40">
                      {session.tasks.map(task => {
                        const filteredArtifacts = filterFiles(task.artifacts)
                        const filteredContext = filterFiles(task.context_docs)
                        const totalVisible = filteredArtifacts.length + filteredContext.length
                        if (searchQuery && totalVisible === 0) return null

                        // If session has only 1 task, show files directly (no task sub-group)
                        if (session.tasks.length === 1) {
                          return (
                            <div key={task.task_id} className="pl-3">
                              {/* Context docs */}
                              {filteredContext.length > 0 && (
                                <div className="pl-2 pr-3 pt-2 pb-1">
                                  <div className="flex items-center gap-1.5 mb-1.5">
                                    <Brain className="w-3 h-3 text-purple-500" />
                                    <span className="text-[10px] font-medium text-purple-600 dark:text-purple-400 uppercase tracking-wider">
                                      Context Docs
                                    </span>
                                  </div>
                                  <div className="pl-1">
                                    {filteredContext.map(file => (
                                      <FileRow
                                        key={file.path}
                                        file={file}
                                        isSelected={selectedFile?.path === file.path && selectedFile?.task_id === file.task_id}
                                        onView={() => void viewFile(file)}
                                        onDownload={() => downloadFile(file)}
                                      />
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Artifacts - Tree View */}
                              {filteredArtifacts.length > 0 && (
                                <div className="pl-2 pr-3 pt-1.5 pb-1">
                                  <div className="flex items-center gap-1.5 mb-1.5">
                                    <File className="w-3 h-3 text-blue-500" />
                                    <span className="text-[10px] font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wider">
                                      Artifacts
                                    </span>
                                  </div>
                                  <FileTree
                                    files={filteredArtifacts}
                                    selectedFile={selectedFile}
                                    onView={(file) => void viewFile(file)}
                                    onDownload={(file) => downloadFile(file)}
                                  />
                                </div>
                              )}
                            </div>
                          )
                        }

                        // Multiple tasks: show task sub-group
                        const isTaskCollapsed = collapsedTasks.has(task.task_id)
                        return (
                          <div key={task.task_id} className="pl-5">
                            <div className="flex items-center w-full">
                              <button
                                onClick={() => toggleTask(task.task_id)}
                                className="flex-1 flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50/50 dark:hover:bg-[#1a1a2e]/50 transition-colors text-left"
                              >
                                {isTaskCollapsed
                                  ? <ChevronRight className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
                                  : <ChevronDown className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
                                }
                                <FolderOpen className="w-3 h-3 text-amber-500 shrink-0" />
                                <span className="text-[11px] font-medium text-text-dark dark:text-[#e4e4ed] truncate">
                                  {task.task_title || `Task #${task.task_id}`}
                                </span>
                                <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0">
                                  {task.file_count}
                                </span>
                              </button>
                              <button
                                onClick={() => downloadTaskZip(task.task_id)}
                                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors shrink-0 mr-2"
                                title="Download all as ZIP"
                              >
                                <Archive className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
                              </button>
                            </div>

                            {!isTaskCollapsed && (
                              <div className="pl-5">
                                {/* Context docs */}
                                {filteredContext.length > 0 && (
                                  <div className="pl-2 pr-3 pt-1 pb-1">
                                    <div className="flex items-center gap-1.5 mb-1">
                                      <Brain className="w-2.5 h-2.5 text-purple-500" />
                                      <span className="text-[9px] font-medium text-purple-600 dark:text-purple-400 uppercase tracking-wider">
                                        Context
                                      </span>
                                    </div>
                                    {filteredContext.map(file => (
                                      <FileRow
                                        key={file.path}
                                        file={file}
                                        isSelected={selectedFile?.path === file.path && selectedFile?.task_id === file.task_id}
                                        onView={() => void viewFile(file)}
                                        onDownload={() => downloadFile(file)}
                                      />
                                    ))}
                                  </div>
                                )}

                                {/* Artifacts - Tree View */}
                                {filteredArtifacts.length > 0 && (
                                  <div className="pl-2 pr-3 pt-1 pb-1">
                                    <div className="flex items-center gap-1.5 mb-1">
                                      <File className="w-2.5 h-2.5 text-blue-500" />
                                      <span className="text-[9px] font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wider">
                                        Files
                                      </span>
                                    </div>
                                    <FileTree
                                      files={filteredArtifacts}
                                      selectedFile={selectedFile}
                                      onView={(file) => void viewFile(file)}
                                      onDownload={(file) => downloadFile(file)}
                                    />
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {/* Orphan tasks (no session) */}
          {data?.orphan_tasks && data.orphan_tasks.length > 0 && data.orphan_tasks.map(task => {
            const filteredArtifacts = filterFiles(task.artifacts)
            const filteredContext = filterFiles(task.context_docs)
            const totalVisible = filteredArtifacts.length + filteredContext.length
            if (searchQuery && totalVisible === 0) return null

            const isTaskCollapsed = collapsedTasks.has(`orphan-${task.task_id}`)

            return (
              <div key={task.task_id} className="mb-1">
                <div className="mx-2 my-1.5 rounded-lg border border-border-light/60 dark:border-[#2a2a45]/80 bg-white/50 dark:bg-[#12121f]/50 overflow-hidden">
                  <div className="flex items-center w-full">
                    <button
                      onClick={() => toggleTask(`orphan-${task.task_id}`)}
                      className="flex-1 flex items-center gap-2 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] transition-colors text-left"
                    >
                      {isTaskCollapsed
                        ? <ChevronRight className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85] shrink-0" />
                        : <ChevronDown className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85] shrink-0" />
                      }
                      <FolderOpen className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <span className="text-xs font-semibold text-text-dark dark:text-[#e4e4ed] truncate block leading-tight">
                          {task.task_title || `Task #${task.task_id}`}
                        </span>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                            {task.file_count} files · {formatSize(task.total_size)}
                          </span>
                          <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                            {formatTime(task.latest_modified)}
                          </span>
                        </div>
                      </div>
                    </button>
                    <button
                      onClick={() => downloadTaskZip(task.task_id)}
                      className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors shrink-0 mr-2"
                      title="Download all as ZIP"
                    >
                      <Archive className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
                    </button>
                  </div>

                  {!isTaskCollapsed && (
                    <div className="pb-1.5 pl-5 border-t border-border-light/30 dark:border-[#2a2a45]/40">
                      {filteredContext.length > 0 && (
                        <div className="pl-4 pr-3 pt-2 pb-1">
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <Brain className="w-3 h-3 text-purple-500" />
                            <span className="text-[10px] font-medium text-purple-600 dark:text-purple-400 uppercase tracking-wider">
                              Context Docs
                            </span>
                          </div>
                          <div className="pl-1">
                            {filteredContext.map(file => (
                              <FileRow
                                key={file.path}
                                file={file}
                                isSelected={selectedFile?.path === file.path && selectedFile?.task_id === file.task_id}
                                onView={() => void viewFile(file)}
                                onDownload={() => downloadFile(file)}
                              />
                            ))}
                          </div>
                        </div>
                      )}
                      {filteredArtifacts.length > 0 && (
                        <div className="pl-4 pr-3 pt-1.5 pb-1">
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <File className="w-3 h-3 text-blue-500" />
                            <span className="text-[10px] font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wider">
                              Artifacts
                            </span>
                          </div>
                          <FileTree
                            files={filteredArtifacts}
                            selectedFile={selectedFile}
                            onView={(file) => void viewFile(file)}
                            onDownload={(file) => downloadFile(file)}
                          />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {/* Global files */}
          {data?.global && data.global.length > 0 && (
            <div className="mb-1">
              <div className="mx-2 my-1.5 rounded-lg border border-border-light/60 dark:border-[#2a2a45]/80 bg-white/50 dark:bg-[#12121f]/50 overflow-hidden">
                <div className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <FolderOpen className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
                    <span className="text-xs font-semibold text-text-dark dark:text-[#e4e4ed]">
                      Global Files
                    </span>
                    <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                      {data.global.length} files
                    </span>
                  </div>
                </div>
                <div className="px-4 pb-2.5 pl-4">
                  <FileTree
                    files={filterFiles(data.global)}
                    selectedFile={selectedFile}
                    onView={(file) => void viewFile(file)}
                    onDownload={(file) => downloadFile(file)}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Right: File Viewer ── */}
      <div className="hidden sm:flex flex-1 flex-col bg-gradient-to-br from-gray-50/80 via-white to-gray-50/50 dark:from-[#0a0a14] dark:via-[#0d0d1a] dark:to-[#0a0a14]">
        {selectedFile ? (
          <>
            {/* File header */}
            <div className="px-5 py-3 bg-white/80 dark:bg-[#0f0f1a]/80 backdrop-blur-sm border-b border-border-light dark:border-[#2a2a45] flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gray-100 to-gray-50 dark:from-[#1a1a2e] dark:to-[#151525] border border-gray-200/80 dark:border-[#2a2a45] flex items-center justify-center text-base shadow-sm">
                  {getType(selectedFile.type).icon}
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold text-text-dark dark:text-[#e4e4ed] truncate">
                    {selectedFile.name}
                  </h3>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-text-muted dark:text-[#6b6b85] font-medium">
                      {formatSize(selectedFile.size)}
                    </span>
                    {selectedFile.task_id && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-gray-100 dark:bg-[#1a1a2e] font-mono text-text-muted dark:text-[#6b6b85]">
                        task/{selectedFile.task_id}
                      </span>
                    )}
                    {selectedFile.is_context_doc && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 font-medium">
                        context doc
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex gap-2 shrink-0">
                {selectedFile.type === 'markdown' && !isLargeFile && fileContent && (
                  <button
                    onClick={() => setOutlineMode(v => !v)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border transition-all duration-200 ${
                      outlineMode
                        ? 'border-brand-300 dark:border-brand-700 bg-brand-50 dark:bg-brand-900/20 text-brand-700 dark:text-brand-300 shadow-sm shadow-brand-100 dark:shadow-brand-900/20'
                        : 'border-border-light dark:border-[#2a2a45] hover:bg-gray-50 dark:hover:bg-[#1a1a2e] text-text-dark dark:text-[#e4e4ed]'
                    }`}
                    title={outlineMode ? 'Switch to raw view' : 'Switch to outline view'}
                  >
                    {outlineMode ? <FileText className="w-3 h-3" /> : <List className="w-3 h-3" />}
                    {outlineMode ? 'Raw' : 'Outline'}
                  </button>
                )}
                {!fileBinary && !isLargeFile && (
                  <button
                    onClick={copyContent}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-border-light dark:border-[#2a2a45] hover:bg-gray-50 dark:hover:bg-[#1a1a2e] transition-colors text-text-dark dark:text-[#e4e4ed]"
                  >
                    <Copy className="w-3 h-3" />
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                )}
                <button
                  onClick={() => downloadFile(selectedFile)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-brand-600 text-white hover:bg-brand-700 transition-colors"
                >
                  <Download className="w-3 h-3" />
                  Download
                </button>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0 overflow-auto p-5">
              {fileLoading ? (
                <ContentSkeleton />
              ) : isLargeFile ? (
                // Large file: show metadata + download button
                <div className="flex flex-col items-center justify-center py-16">
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-50 to-orange-100 dark:from-[#1a1a2e] dark:to-[#22223a] border border-amber-200 dark:border-[#2a2a45] flex items-center justify-center text-3xl mb-4">
                    {getType(selectedFile.type).icon}
                  </div>
                  <h3 className="text-base font-semibold text-text-dark dark:text-[#e4e4ed] mb-1">{selectedFile.name}</h3>
                  <p className="text-xs text-text-muted dark:text-[#6b6b85] mb-2">{formatSize(selectedFile.size)}</p>
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg mb-4">
                    <Eye className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
                    <span className="text-xs text-amber-700 dark:text-amber-400">
                      File quá lớn ({formatSize(selectedFile.size)}) — không hiển thị preview
                    </span>
                  </div>
                  <button
                    onClick={() => downloadFile(selectedFile)}
                    className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-xl text-sm font-medium hover:bg-brand-700 transition-colors"
                  >
                    <Download className="w-4 h-4" /> Download File
                  </button>
                </div>
              ) : fileBinary ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-[#1a1a2e] dark:to-[#22223a] border border-blue-200 dark:border-[#2a2a45] flex items-center justify-center text-3xl mb-4">
                    {getType(selectedFile.type).icon}
                  </div>
                  <h3 className="text-base font-semibold text-text-dark dark:text-[#e4e4ed] mb-1">{selectedFile.name}</h3>
                  <p className="text-xs text-text-muted dark:text-[#6b6b85] mb-4">{formatSize(selectedFile.size)}</p>
                  {selectedFile.type === 'image' && (
                    <div className="mb-4 max-w-lg">
                      <img
                        src={(() => {
                          const ep = selectedFile.path.split('/').map(encodeURIComponent).join('/')
                          return selectedFile.task_id
                            ? `${API_URL}/api/v1/artifacts/task/${selectedFile.task_id}/download/${ep}`
                            : `${API_URL}/api/v1/artifacts/download/${ep}`
                        })()}
                        alt={selectedFile.name}
                        className="max-w-full rounded-xl border border-gray-200 dark:border-[#2a2a45]"
                      />
                    </div>
                  )}
                  <button
                    onClick={() => downloadFile(selectedFile)}
                    className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-xl text-sm font-medium hover:bg-brand-700 transition-colors"
                  >
                    <Download className="w-4 h-4" /> Download File
                  </button>
                </div>
              ) : selectedFile.type === 'markdown' ? (
                outlineMode ? (
                  <div className="bg-white dark:bg-[#0f0f1a] rounded-xl border border-border-light dark:border-[#2a2a45] overflow-hidden h-full">
                    <MarkdownOutlineView content={fileContent} />
                  </div>
                ) : (
                  <div className="bg-white dark:bg-[#0f0f1a] p-6 rounded-xl border border-border-light dark:border-[#2a2a45] prose prose-sm dark:prose-invert max-w-none overflow-x-auto [&_*]:max-w-full [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_code]:break-all [&_p]:break-words [&_td]:break-words [&_th]:break-words" style={{ overflowWrap: 'break-word', wordBreak: 'break-word' }}>
                    <MarkdownRenderer content={fileContent} />
                  </div>
                )
              ) : (
                <pre className="text-sm text-text-primary dark:text-[#a0a0b8] whitespace-pre-wrap break-words font-mono bg-white dark:bg-[#0f0f1a] p-5 rounded-xl border border-border-light dark:border-[#2a2a45] leading-relaxed overflow-x-auto max-w-full">
                  {fileContent}
                </pre>
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-gray-100 to-gray-50 dark:from-[#1a1a2e] dark:to-[#151525] border border-gray-200/80 dark:border-[#2a2a45] flex items-center justify-center text-4xl mx-auto mb-5 shadow-sm">
                📁
              </div>
              <p className="text-sm text-text-dark dark:text-[#e4e4ed] font-semibold">Chọn file để xem nội dung</p>
              <p className="text-xs text-text-muted dark:text-[#6b6b85] mt-1.5 max-w-[200px] mx-auto leading-relaxed">
                Click vào file ở sidebar bên trái để preview
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FileRow sub-component
// ---------------------------------------------------------------------------

function FileRow({
  file,
  isSelected,
  onView,
  onDownload,
}: {
  file: ArtifactFile
  isSelected: boolean
  onView: () => void
  onDownload: () => void
}) {
  const typeInfo = getType(file.type)

  return (
    <div
      className={`flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
        isSelected
          ? 'bg-brand-50 dark:bg-brand-900/20 border border-brand-200 dark:border-brand-800'
          : 'hover:bg-gray-50 dark:hover:bg-[#1a1a2e] border border-transparent'
      }`}
    >
      <button
        className="flex items-center gap-2 flex-1 min-w-0 text-left"
        onClick={onView}
      >
        <span className={`text-sm shrink-0 ${typeInfo.color}`}>{typeInfo.icon}</span>
        <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
          {file.name}
        </span>
        <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0">
          {formatSize(file.size)}
        </span>
      </button>

      <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        {!file.is_binary && (
          <button
            onClick={onView}
            className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
            title="Preview"
          >
            <Eye className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
          </button>
        )}
        <button
          onClick={onDownload}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
          title="Download"
        >
          <Download className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
        </button>
      </div>
    </div>
  )
}
