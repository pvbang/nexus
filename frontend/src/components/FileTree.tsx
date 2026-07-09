// ============================================================
// MekongAI Nexus - FileTree Component (Optimized)
// Virtualized tree using @tanstack/react-virtual.
// - Flattened tree → only visible nodes rendered (~30-50)
// - Heavy directories auto-collapsed with warning badge
// - React.memo on tree nodes to prevent sibling re-renders
// ============================================================

import { useState, useMemo, useCallback, useRef, memo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ChevronRight, ChevronDown, Folder, FolderOpen,
  Download, Eye, AlertTriangle,
} from 'lucide-react'

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

interface TreeNode {
  name: string
  path: string
  isDir: boolean
  children: TreeNode[]
  file?: ArtifactFile
  fileCount?: number
  totalSize?: number
}

/** Flat row for virtualization */
interface FlatRow {
  id: string
  node: TreeNode
  depth: number
  isExpanded: boolean
  isHeavyDir: boolean
}

interface FileTreeProps {
  files: ArtifactFile[]
  selectedFile?: ArtifactFile | null
  selectedPath?: string | null
  onView: (file: ArtifactFile) => void
  onDownload: (file: ArtifactFile) => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const HEAVY_DIRS = new Set([
  'node_modules', 'venv', '.venv', 'env', '.git',
  '__pycache__', 'dist', 'build', '.next', '.cache', 'target',
])

const ROW_HEIGHT = 28

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

const getType = (type: string) => typeConfig[type] || { icon: '📄', color: 'text-text-muted' }

const formatSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ---------------------------------------------------------------------------
// Tree Building
// ---------------------------------------------------------------------------

function buildTree(files: ArtifactFile[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', isDir: true, children: [] }

  for (const file of files) {
    const parts = file.path.replace(/\\/g, '/').split('/').filter(Boolean)
    let current = root

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isLast = i === parts.length - 1
      const currentPath = parts.slice(0, i + 1).join('/')

      if (isLast) {
        current.children.push({
          name: part,
          path: currentPath,
          isDir: false,
          children: [],
          file,
        })
      } else {
        let dirNode = current.children.find(c => c.isDir && c.name === part)
        if (!dirNode) {
          dirNode = { name: part, path: currentPath, isDir: true, children: [] }
          current.children.push(dirNode)
        }
        current = dirNode
      }
    }
  }

  // Sort and compute metadata
  const processNode = (node: TreeNode): void => {
    node.children.sort((a, b) => {
      if (a.isDir && !b.isDir) return -1
      if (!a.isDir && b.isDir) return 1
      return a.name.localeCompare(b.name)
    })

    if (node.isDir) {
      let count = 0
      let size = 0
      for (const child of node.children) {
        processNode(child)
        if (child.isDir) {
          count += child.fileCount || 0
          size += child.totalSize || 0
        } else {
          count += 1
          size += child.file?.size || 0
        }
      }
      node.fileCount = count
      node.totalSize = size
    }
  }
  processNode(root)

  return root.children
}

/** Flatten tree into a flat array based on expanded state */
function flattenTree(
  nodes: TreeNode[],
  expandedPaths: Set<string>,
  depth: number = 0,
): FlatRow[] {
  const rows: FlatRow[] = []

  for (const node of nodes) {
    const isHeavy = node.isDir && HEAVY_DIRS.has(node.name)
    const isExpanded = expandedPaths.has(node.path)

    rows.push({
      id: node.path || node.name,
      node,
      depth,
      isExpanded,
      isHeavyDir: isHeavy,
    })

    // Only include children if expanded
    if (node.isDir && isExpanded) {
      const childRows = flattenTree(node.children, expandedPaths, depth + 1)
      rows.push(...childRows)
    }
  }

  return rows
}

// ---------------------------------------------------------------------------
// Memoized Row Components
// ---------------------------------------------------------------------------

interface DirRowProps {
  node: TreeNode
  depth: number
  isExpanded: boolean
  isHeavy: boolean
  onToggle: (path: string) => void
}

const DirRow = memo(function DirRow({ node, depth, isExpanded, isHeavy, onToggle }: DirRowProps) {
  const paddingLeft = depth * 16 + 4

  return (
    <button
      onClick={() => onToggle(node.path)}
      className="w-full flex items-center gap-1.5 py-1 px-2 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] rounded transition-colors text-left"
      style={{ paddingLeft: `${paddingLeft}px`, height: `${ROW_HEIGHT}px` }}
    >
      {isExpanded
        ? <ChevronDown className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
        : <ChevronRight className="w-3 h-3 text-text-muted dark:text-[#6b6b85] shrink-0" />
      }
      {isExpanded
        ? <FolderOpen className="w-3.5 h-3.5 text-amber-500 shrink-0" />
        : <Folder className="w-3.5 h-3.5 text-amber-500 shrink-0" />
      }
      <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
        {node.name}
      </span>
      {isHeavy && (
        <span className="flex items-center gap-0.5 ml-1 shrink-0">
          <AlertTriangle className="w-3 h-3 text-amber-500" />
          <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 font-medium">
            {node.fileCount} files
          </span>
        </span>
      )}
      {!isHeavy && (
        <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0 ml-auto">
          {node.fileCount}
        </span>
      )}
    </button>
  )
})

interface FileRowProps {
  node: TreeNode
  depth: number
  isSelected: boolean
  onView: (file: ArtifactFile) => void
  onDownload: (file: ArtifactFile) => void
}

const FileRow = memo(function FileRow({ node, depth, isSelected, onView, onDownload }: FileRowProps) {
  const file = node.file!
  const typeInfo = getType(file.type)
  const paddingLeft = depth * 16 + 20

  return (
    <div
      className={`group flex items-center gap-1.5 py-1 px-2 rounded transition-colors cursor-pointer ${
        isSelected
          ? 'bg-brand-50 dark:bg-brand-900/20 border border-brand-200 dark:border-brand-800'
          : 'hover:bg-gray-50 dark:hover:bg-[#1a1a2e] border border-transparent'
      }`}
      style={{ paddingLeft: `${paddingLeft}px`, height: `${ROW_HEIGHT}px` }}
    >
      <button
        className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
        onClick={() => onView(file)}
      >
        <span className={`text-sm shrink-0 ${typeInfo.color}`}>{typeInfo.icon}</span>
        <span className="text-xs font-medium text-text-dark dark:text-[#e4e4ed] truncate">
          {node.name}
        </span>
        <span className="text-[10px] text-text-muted dark:text-[#6b6b85] shrink-0 ml-auto">
          {formatSize(file.size)}
        </span>
      </button>

      <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        {!file.is_binary && (
          <button
            onClick={() => onView(file)}
            className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
            title="Preview"
          >
            <Eye className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
          </button>
        )}
        <button
          onClick={() => onDownload(file)}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-[#22223a] transition-colors"
          title="Download"
        >
          <Download className="w-3 h-3 text-text-muted dark:text-[#6b6b85]" />
        </button>
      </div>
    </div>
  )
})

// ---------------------------------------------------------------------------
// Main FileTree Component (Virtualized)
// ---------------------------------------------------------------------------

export function FileTree({ files, selectedFile, selectedPath, onView, onDownload }: FileTreeProps) {
  const parentRef = useRef<HTMLDivElement>(null)

  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => {
    // Auto-expand first level directories EXCEPT heavy ones
    const initial = new Set<string>()
    const seen = new Set<string>()
    for (const file of files) {
      const parts = file.path.replace(/\\/g, '/').split('/').filter(Boolean)
      if (parts.length > 1 && !seen.has(parts[0])) {
        seen.add(parts[0])
        // Don't auto-expand heavy directories
        if (!HEAVY_DIRS.has(parts[0])) {
          initial.add(parts[0])
        }
      }
    }
    return initial
  })

  const tree = useMemo(() => buildTree(files), [files])
  const flatRows = useMemo(() => flattenTree(tree, expandedPaths), [tree, expandedPaths])

  const toggleExpand = useCallback((path: string) => {
    setExpandedPaths(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  const virtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  })

  // For small trees (< 50 items), skip virtualization overhead
  if (flatRows.length < 50) {
    return (
      <div className="space-y-0.5">
        {flatRows.map(row => {
          if (row.node.isDir) {
            return (
              <DirRow
                key={row.id}
                node={row.node}
                depth={row.depth}
                isExpanded={row.isExpanded}
                isHeavy={row.isHeavyDir}
                onToggle={toggleExpand}
              />
            )
          }
          const file = row.node.file!
          const isSelected = selectedPath
            ? file.path === selectedPath
            : (selectedFile?.path === file.path && selectedFile?.task_id === file.task_id)

          return (
            <FileRow
              key={row.id}
              node={row.node}
              depth={row.depth}
              isSelected={isSelected}
              onView={onView}
              onDownload={onDownload}
            />
          )
        })}
      </div>
    )
  }

  // Virtualized rendering for large trees
  return (
    <div
      ref={parentRef}
      className="overflow-auto"
      style={{ maxHeight: '600px' }}
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map(virtualRow => {
          const row = flatRows[virtualRow.index]

          if (row.node.isDir) {
            return (
              <div
                key={row.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: `${virtualRow.size}px`,
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                <DirRow
                  node={row.node}
                  depth={row.depth}
                  isExpanded={row.isExpanded}
                  isHeavy={row.isHeavyDir}
                  onToggle={toggleExpand}
                />
              </div>
            )
          }

          const file = row.node.file!
          const isSelected = selectedPath
            ? file.path === selectedPath
            : (selectedFile?.path === file.path && selectedFile?.task_id === file.task_id)

          return (
            <div
              key={row.id}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              <FileRow
                node={row.node}
                depth={row.depth}
                isSelected={isSelected}
                onView={onView}
                onDownload={onDownload}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
