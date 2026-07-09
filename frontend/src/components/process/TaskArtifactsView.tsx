// ============================================================
// MekongAI Nexus - TaskArtifactsView
// Compact file list for a specific task's workspace.
// Shown inside UnifiedProcessPanel "Files" tab.
// Polls /artifacts/task/{taskId} for live updates.
//
// Separates context docs (_context/) from task artifacts
// for clear visual distinction.
// ============================================================

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  FileText, Download, Eye, FolderOpen, RefreshCw,
  Brain, File, ChevronDown, ChevronRight,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { apiGet, downloadWithAuth } from '../../api'
import { EmptyState } from '../ui/EmptyState'
import { Spinner } from '../ui/Spinner'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
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

export interface TaskArtifactsViewProps {
  taskId: string
  isRunning?: boolean
  /** Increment to trigger an immediate re-fetch (SSE-driven file refresh). */
  refreshSignal?: number
  /** If a specific file path is requested to be viewed (from chat link click) */
  openFilePath?: string | null
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TaskArtifactsView({ taskId, isRunning = false, refreshSignal, openFilePath }: TaskArtifactsViewProps) {
  const [artifacts, setArtifacts] = useState<TaskArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [fileLoading, setFileLoading] = useState(false)
  const [contentInfo, setContentInfo] = useState<ArtifactContent | null>(null)
  const [contextCollapsed, setContextCollapsed] = useState(false)

  const shortTaskId = taskId.slice(0, 8)

  // Separate context docs from regular artifacts
  const contextDocs = artifacts.filter(a => a.path.startsWith('_context'))
  const regularArtifacts = artifacts.filter(a => !a.path.startsWith('_context'))

  const loadArtifacts = useCallback(async () => {
    try {
      const data = await apiGet<TaskArtifact[]>(`/artifacts/task/${shortTaskId}`)
      setArtifacts(data)
    } catch {
      // Task might not have artifacts yet
      setArtifacts([])
    } finally {
      setLoading(false)
    }
  }, [shortTaskId])

  // Initial load + polling when task is running
  // Also do a final refresh when isRunning transitions false → catches files created at completion
  const prevRunningRef = useRef(isRunning)
  useEffect(() => {
    void loadArtifacts()
    if (isRunning) {
      // Longer polling interval since SSE-driven refreshSignal is the primary mechanism
      const interval = setInterval(loadArtifacts, 5000)
      return () => clearInterval(interval)
    } else if (prevRunningRef.current && !isRunning) {
      // Task just finished — do one more refresh after a short delay
      // to catch any files written during final synthesis
      const timer = setTimeout(loadArtifacts, 1500)
      return () => clearTimeout(timer)
    }
    prevRunningRef.current = isRunning
  }, [loadArtifacts, isRunning])

  // SSE-driven refresh: re-fetch immediately when a file-related tool completes
  useEffect(() => {
    if (refreshSignal && refreshSignal > 0) {
      void loadArtifacts()
    }
  }, [refreshSignal, loadArtifacts])

  const viewFile = useCallback(async (path: string) => {
    setSelectedFile(path)
    setFileLoading(true)
    setContentInfo(null)
    try {
      const data = await apiGet<ArtifactContent>(`/artifacts/task/${shortTaskId}/read/${path}`)
      setContentInfo(data)
      setFileContent(data.content || '')
    } catch {
      setFileContent('Failed to load file')
      setContentInfo(null)
    } finally {
      setFileLoading(false)
    }
  }, [shortTaskId])

  // Handle openFilePath prop (from chat link clicks)
  useEffect(() => {
    if (!openFilePath) return
    // Find matching artifact and open it
    const found = artifacts.find((f) => f.name === openFilePath || f.path === openFilePath)
    if (found) {
      void viewFile(found.path)
    }
  }, [openFilePath, artifacts, viewFile])

  const downloadFile = useCallback((path: string) => {
    const filename = path.split('/').pop() || 'download'
    void downloadWithAuth(`${API_URL}/api/v1/artifacts/task/${shortTaskId}/download/${path}`, filename)
  }, [shortTaskId])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Spinner size="md" className="text-brand-500" />
      </div>
    )
  }

  if (artifacts.length === 0) {
    return (
      <EmptyState
        icon="📁"
        title="Chưa có files nào"
        description={isRunning ? 'Files sẽ xuất hiện khi agent tạo ra...' : 'Task này chưa tạo file nào'}
      />
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* File list header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-light dark:border-[#2a2a45]">
        <div className="flex items-center gap-2">
          <FolderOpen className="w-4 h-4 text-text-muted dark:text-[#6b6b85]" />
          <span className="text-xs font-medium text-text-muted dark:text-[#6b6b85]">
            {artifacts.length} file{artifacts.length !== 1 ? 's' : ''}
          </span>
          <span className="text-[10px] text-text-muted dark:text-[#6b6b85] font-mono">
            task/{shortTaskId}
          </span>
        </div>
        <button
          onClick={() => void loadArtifacts()}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-[#1a1a2e] transition-colors"
          title="Refresh files"
        >
          <RefreshCw className={cn('w-3.5 h-3.5 text-text-muted dark:text-[#6b6b85]', loading && 'animate-spin')} />
        </button>
      </div>

      {/* File list */}
      <div className="flex-1 overflow-y-auto">
        {/* Context Docs Section */}
        {contextDocs.length > 0 && (
          <div className="border-b border-border-light/50 dark:border-[#2a2a45]/50">
            <button
              onClick={() => setContextCollapsed(!contextCollapsed)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] transition-colors text-left"
            >
              {contextCollapsed
                ? <ChevronRight className="w-3 h-3 text-purple-500 shrink-0" />
                : <ChevronDown className="w-3 h-3 text-purple-500 shrink-0" />
              }
              <Brain className="w-3.5 h-3.5 text-purple-500 shrink-0" />
              <span className="text-[10px] font-semibold text-purple-600 dark:text-purple-400 uppercase tracking-wider">
                Context Docs
              </span>
              <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                ({contextDocs.length})
              </span>
            </button>

            {!contextCollapsed && contextDocs.map((artifact) => (
              <FileRow
                key={artifact.path}
                artifact={artifact}
                isSelected={selectedFile === artifact.path}
                onView={() => void viewFile(artifact.path)}
                onDownload={() => downloadFile(artifact.path)}
              />
            ))}
          </div>
        )}

        {/* Regular Artifacts Section - Tree View */}
        {regularArtifacts.length > 0 && (
          <div>
            {contextDocs.length > 0 && (
              <div className="flex items-center gap-2 px-3 py-2">
                <File className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                <span className="text-[10px] font-semibold text-blue-600 dark:text-blue-400 uppercase tracking-wider">
                  Artifacts
                </span>
                <span className="text-[10px] text-text-muted dark:text-[#6b6b85]">
                  ({regularArtifacts.length})
                </span>
              </div>
            )}

            <div className="px-2">
              <FileTree
                files={regularArtifacts}
                selectedPath={selectedFile}
                onView={(file) => void viewFile(file.path)}
                onDownload={(file) => downloadFile(file.path)}
              />
            </div>
          </div>
        )}
      </div>

      {/* File preview pane */}
      {selectedFile && (
        <div className="border-t border-border-light dark:border-[#2a2a45] max-h-[50%] overflow-y-auto">
          <div className="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-[#1a1a2e]">
            <div className="flex items-center gap-2">
              <FileText className="w-3.5 h-3.5 text-brand-500" />
              <span className="text-[11px] font-medium text-text-dark dark:text-[#e4e4ed]">
                {selectedFile}
              </span>
              {selectedFile.startsWith('_context') && (
                <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 font-medium">
                  context
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => void viewFile(selectedFile)}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
                title="Reload"
              >
                <RefreshCw className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
              </button>
              <button
                onClick={() => setSelectedFile(null)}
                className="text-[10px] text-text-muted dark:text-[#6b6b85] hover:text-text-dark dark:hover:text-[#e4e4ed] px-1"
              >
                ✕
              </button>
            </div>
          </div>

          {fileLoading ? (
            <div className="flex items-center justify-center py-6">
              <Spinner size="sm" className="text-brand-500" />
            </div>
          ) : contentInfo?.is_binary ? (
            <div className="px-3 py-4 text-center">
              <p className="text-xs text-text-muted dark:text-[#6b6b85]">{contentInfo.message}</p>
              <button
                onClick={() => downloadFile(selectedFile)}
                className="mt-2 px-3 py-1.5 text-xs font-medium rounded-lg bg-brand-600 text-white hover:bg-brand-700 transition-colors"
              >
                ⬇ Download
              </button>
            </div>
          ) : contentInfo?.type === 'markdown' ? (
            <div className="px-3 py-2 text-xs">
              <MarkdownRenderer content={fileContent} />
            </div>
          ) : (
            <pre className="px-3 py-2 text-[11px] font-mono text-text-secondary dark:text-[#a0a0b8] whitespace-pre-wrap leading-relaxed overflow-x-auto">
              {fileContent}
            </pre>
          )}
        </div>
      )}
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
}: {
  artifact: TaskArtifact
  isSelected: boolean
  onView: () => void
  onDownload: () => void
}) {
  const isContext = artifact.path.startsWith('_context')

  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors',
        'hover:bg-gray-50 dark:hover:bg-[#1a1a2e]',
        'border-b border-border-light/50 dark:border-[#2a2a45]/50',
        isSelected && 'bg-brand-50 dark:bg-brand-900/20',
        isContext && 'pl-6',
      )}
    >
      {/* Icon + Name */}
      <button
        className="flex items-center gap-2 flex-1 min-w-0 text-left"
        onClick={onView}
      >
        <span className="text-sm shrink-0">
          {isContext ? '🧠' : (FILE_ICONS[artifact.type] ?? '📄')}
        </span>
        <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
          {artifact.name}
        </span>
        <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0">
          {formatFileSize(artifact.size)}
        </span>
      </button>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
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
