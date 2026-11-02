// ============================================================
// Nexus - SessionArtifactsView
// Shows ALL artifacts for a chat session by scanning task_ids
// from message metadata. Supports inline file preview.
// Used as fallback when no active process taskId is available.
// ============================================================

import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  FileText, Download, Eye, FolderOpen, RefreshCw,
  Brain, File, ChevronDown, ChevronRight, X,
  Copy, Check,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { apiGet, downloadWithAuth } from '../../api'
import { EmptyState } from '../ui/EmptyState'
import { Spinner } from '../ui/Spinner'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { useSessionStore } from '../../stores/sessionStore'
import { FileTree } from '../FileTree'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TaskArtifact {
  path: string
  name: string
  size: number
  modified: number
  type: string
  is_binary?: boolean
  task_id?: string
  download_url?: string
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

const FILE_ICONS: Record<string, string> = {
  markdown: '📝',
  text: '📄',
  json: '📋',
  python: '🐍',
  javascript: '🟨',
  typescript: '🔷',
  html: '🌐',
  css: '🎨',
  csv: '📊',
  yaml: '⚙️',
  bash: '💻',
  docx: '📄',
  excel: '📊',
  pdf: '📕',
  image: '🖼️',
  audio: '🎵',
  file: '📁',
}

// BUG-A02 fix: Use centralized API_URL from lib/constants (single source of truth)
import { API_URL } from '../../lib/constants'

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SessionArtifactsViewProps {
  sessionId: string
  /** Increment to trigger an immediate re-fetch */
  refreshSignal?: number
  /** If a specific file path is requested to be viewed (from chat link click) */
  openFilePath?: string | null
  onFileOpen?: (taskId: string, path: string) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SessionArtifactsView({
  sessionId,
  refreshSignal,
  openFilePath,
}: SessionArtifactsViewProps) {
  const [artifactsByTask, setArtifactsByTask] = useState<Record<string, TaskArtifact[]>>({})
  const [loading, setLoading] = useState(true)
  const [selectedFile, setSelectedFile] = useState<{ taskId: string; path: string } | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [fileLoading, setFileLoading] = useState(false)
  const [contentInfo, setContentInfo] = useState<ArtifactContent | null>(null)
  const [collapsedTasks, setCollapsedTasks] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState(false)

  // Get task IDs from message metadata
  const messagesMap = useSessionStore((s) => s.messagesBySession)
  const messages = messagesMap[sessionId] ?? []

  const taskIds = useMemo(() => {
    const ids = new Set<string>()
    for (const msg of messages) {
      if (msg.metadata?.task_id) {
        ids.add(String(msg.metadata.task_id))
      }
    }
    return Array.from(ids)
  }, [messages])

  const loadAllArtifacts = useCallback(async () => {
    if (taskIds.length === 0) {
      setLoading(false)
      return
    }

    try {
      const results: Record<string, TaskArtifact[]> = {}
      await Promise.all(
        taskIds.map(async (taskId) => {
          const shortId = taskId.slice(0, 8)
          try {
            const data = await apiGet<TaskArtifact[]>(`/artifacts/task/${shortId}`)
            if (data.length > 0) {
              results[taskId] = data.map((f) => ({ ...f, task_id: taskId }))
            }
          } catch {
            // Task might not have artifacts — skip
          }
        })
      )
      setArtifactsByTask(results)
    } catch {
      setArtifactsByTask({})
    } finally {
      setLoading(false)
    }
  }, [taskIds])

  // Initial load
  useEffect(() => {
    void loadAllArtifacts()
  }, [loadAllArtifacts])

  // Refresh signal
  useEffect(() => {
    if (refreshSignal && refreshSignal > 0) {
      void loadAllArtifacts()
    }
  }, [refreshSignal, loadAllArtifacts])

  // Handle openFilePath prop (from chat link clicks)
  useEffect(() => {
    if (!openFilePath) return
    // Find which task has this file
    for (const [taskId, files] of Object.entries(artifactsByTask)) {
      const found = files.find((f) => f.name === openFilePath || f.path === openFilePath)
      if (found) {
        void viewFile(taskId, found.path)
        return
      }
    }
  }, [openFilePath, artifactsByTask]) // eslint-disable-line react-hooks/exhaustive-deps

  const viewFile = useCallback(async (taskId: string, path: string) => {
    const shortId = taskId.slice(0, 8)
    setSelectedFile({ taskId, path })
    setFileLoading(true)
    setContentInfo(null)
    setCopied(false)
    try {
      const data = await apiGet<ArtifactContent>(`/artifacts/task/${shortId}/read/${path}`)
      setContentInfo(data)
      setFileContent(data.content || '')
    } catch {
      setFileContent('Failed to load file')
      setContentInfo(null)
    } finally {
      setFileLoading(false)
    }
  }, [])

  const downloadFile = useCallback((taskId: string, path: string) => {
    const shortId = taskId.slice(0, 8)
    const filename = path.split('/').pop() || 'download'
    void downloadWithAuth(`${API_URL}/api/v1/artifacts/task/${shortId}/download/${path}`, filename)
  }, [])

  const copyContent = useCallback(() => {
    navigator.clipboard.writeText(fileContent)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [fileContent])

  const toggleTask = useCallback((taskId: string) => {
    setCollapsedTasks((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) next.delete(taskId)
      else next.add(taskId)
      return next
    })
  }, [])

  // Total files count
  const totalFiles = Object.values(artifactsByTask).reduce((sum, files) => sum + files.length, 0)

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Spinner size="md" className="text-brand-500" />
      </div>
    )
  }

  if (taskIds.length === 0 || totalFiles === 0) {
    return (
      <EmptyState
        icon="📁"
        title="Chưa có files nào"
        description="Task này chưa tạo file nào"
      />
    )
  }

  // If a file is selected, show the preview
  if (selectedFile) {
    const artifact = Object.values(artifactsByTask)
      .flat()
      .find((f) => f.path === selectedFile.path && f.task_id === selectedFile.taskId)

    return (
      <div className="flex flex-col h-full">
        {/* File viewer header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-border-light dark:border-[#2a2a45] bg-gray-50 dark:bg-[#1a1a2e]">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-4 h-4 text-brand-500 shrink-0" />
            <div className="min-w-0">
              <p className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
                {artifact?.name || selectedFile.path}
              </p>
              <p className="text-[10px] text-text-muted dark:text-[#6b6b85] font-mono">
                task/{selectedFile.taskId.slice(0, 8)}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {!contentInfo?.is_binary && fileContent && (
              <button
                onClick={copyContent}
                className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
                title={copied ? 'Copied!' : 'Copy content'}
              >
                {copied
                  ? <Check className="w-3.5 h-3.5 text-green-500" />
                  : <Copy className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
                }
              </button>
            )}
            <button
              onClick={() => downloadFile(selectedFile.taskId, selectedFile.path)}
              className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
              title="Download"
            >
              <Download className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
            </button>
            <button
              onClick={() => setSelectedFile(null)}
              className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
              title="Back to file list"
            >
              <X className="w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]" />
            </button>
          </div>
        </div>

        {/* File content */}
        <div className="flex-1 overflow-y-auto">
          {fileLoading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner size="sm" className="text-brand-500" />
            </div>
          ) : contentInfo?.is_binary ? (
            <div className="px-4 py-8 text-center">
              <div className="text-3xl mb-3">
                {FILE_ICONS[artifact?.type || 'file'] ?? '📄'}
              </div>
              <p className="text-xs text-text-muted dark:text-[#6b6b85] mb-3">
                {contentInfo.message || 'Binary file — download to view'}
              </p>
              {artifact?.type === 'image' && (
                <div className="mb-4 px-4">
                  <img
                    src={`${API_URL}/api/v1/artifacts/task/${selectedFile.taskId.slice(0, 8)}/download/${selectedFile.path}`}
                    alt={artifact.name}
                    className="max-w-full rounded-lg border border-gray-200 dark:border-[#2a2a45] mx-auto"
                  />
                </div>
              )}
              <button
                onClick={() => downloadFile(selectedFile.taskId, selectedFile.path)}
                className="px-4 py-2 text-xs font-medium rounded-lg bg-brand-600 text-white hover:bg-brand-700 transition-colors"
              >
                ⬇ Download
              </button>
            </div>
          ) : contentInfo?.type === 'markdown' ? (
            <div className="px-4 py-3 prose prose-sm dark:prose-invert max-w-none text-xs">
              <MarkdownRenderer content={fileContent} />
            </div>
          ) : (
            <pre className="px-4 py-3 text-[11px] font-mono text-text-secondary dark:text-[#a0a0b8] whitespace-pre-wrap leading-relaxed overflow-x-auto">
              {fileContent}
            </pre>
          )}
        </div>
      </div>
    )
  }

  // File list view
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-light dark:border-[#2a2a45]">
        <div className="flex items-center gap-2">
          <FolderOpen className="w-4 h-4 text-brand-500" />
          <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed]">
            {totalFiles} file{totalFiles !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={() => { setLoading(true); void loadAllArtifacts() }}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-[#1a1a2e] transition-colors"
          title="Refresh files"
        >
          <RefreshCw className={cn('w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]', loading && 'animate-spin')} />
        </button>
      </div>

      {/* File list by task */}
      <div className="flex-1 overflow-y-auto">
        {Object.entries(artifactsByTask).map(([taskId, files]) => {
          const isCollapsed = collapsedTasks.has(taskId)
          const contextDocs = files.filter((f) => f.path.startsWith('_context'))
          const regularArtifacts = files.filter((f) => !f.path.startsWith('_context'))

          return (
            <div key={taskId} className="border-b border-border-light/50 dark:border-[#2a2a45]/50">
              {/* Task header (only show if multiple tasks) */}
              {Object.keys(artifactsByTask).length > 1 && (
                <button
                  onClick={() => toggleTask(taskId)}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] transition-colors text-left"
                >
                  {isCollapsed
                    ? <ChevronRight className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
                    : <ChevronDown className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
                  }
                  <FolderOpen className="w-3.5 h-3.5 text-brand-500 shrink-0" />
                  <span className="text-[11px] font-mono font-medium text-text-dark dark:text-[#e4e4ed]">
                    task/{taskId.slice(0, 8)}
                  </span>
                  <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                    ({files.length})
                  </span>
                </button>
              )}

              {!isCollapsed && (
                <>
                  {/* Context docs */}
                  {contextDocs.length > 0 && (
                    <div className="px-2 py-1">
                      <div className="flex items-center gap-1.5 px-2 mb-0.5">
                        <Brain className="w-3 h-3 text-purple-500" />
                        <span className="text-[10px] font-semibold text-purple-600 dark:text-purple-400 uppercase tracking-wider">
                          Context
                        </span>
                      </div>
                      {contextDocs.map((f) => (
                        <FileRow
                          key={f.path}
                          artifact={f}
                          isSelected={false}
                          onView={() => void viewFile(taskId, f.path)}
                          onDownload={() => downloadFile(taskId, f.path)}
                          indent
                        />
                      ))}
                    </div>
                  )}

                  {/* Regular artifacts - Tree View */}
                  {regularArtifacts.length > 0 && (
                    <div className="px-2 py-1">
                      {contextDocs.length > 0 && (
                        <div className="flex items-center gap-1.5 px-2 mb-0.5">
                          <File className="w-3 h-3 text-blue-500" />
                          <span className="text-[10px] font-semibold text-blue-600 dark:text-blue-400 uppercase tracking-wider">
                            Artifacts
                          </span>
                        </div>
                      )}
                      <FileTree
                        files={regularArtifacts}
                        onView={(file) => void viewFile(taskId, file.path)}
                        onDownload={(file) => downloadFile(taskId, file.path)}
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FileRow sub-component
// ---------------------------------------------------------------------------

function FileRow({
  artifact,
  isSelected,
  onView,
  onDownload,
  indent = false,
}: {
  artifact: TaskArtifact
  isSelected: boolean
  onView: () => void
  onDownload: () => void
  indent?: boolean
}) {
  const isContext = artifact.path.startsWith('_context')
  const icon = isContext ? '🧠' : (FILE_ICONS[artifact.type] ?? '📄')

  return (
    <div
      className={cn(
        'group flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer transition-colors',
        indent ? 'ml-2' : '',
        isSelected
          ? 'bg-brand-50 dark:bg-brand-900/20 border border-brand-200 dark:border-brand-800'
          : 'hover:bg-gray-50 dark:hover:bg-[#1a1a2e] border border-transparent',
      )}
    >
      <button
        className="flex items-center gap-2 flex-1 min-w-0 text-left"
        onClick={onView}
      >
        <span className="text-sm shrink-0">{icon}</span>
        <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
          {artifact.name}
        </span>
        <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0">
          {formatFileSize(artifact.size)}
        </span>
      </button>

      <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        {!artifact.is_binary && (
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
