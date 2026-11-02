// ============================================================
// Nexus - LazyDirectoryTree Component
// Virtualized lazy-loading directory tree using useLazyTree hook.
// ============================================================

import { useEffect, useRef, useCallback, memo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ChevronRight, Download, Folder, FolderOpen, Loader2 } from 'lucide-react';
import { useLazyTree } from '../../hooks/useLazyTree';
import { getDownloadUrl, getZipDownloadUrl } from '../../api/artifacts';
import type { LazyTreeNode } from '../../types/artifacts';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LazyDirectoryTreeProps {
  taskId?: string;
  showHidden?: boolean;
  onFileSelect: (filePath: string) => void;
  selectedFile?: string | null;
  className?: string;
  maxHeight?: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ROW_HEIGHT = 28;

const extTypeConfig: Record<string, { icon: string; color: string }> = {
  py: { icon: '🐍', color: 'text-emerald-600' },
  js: { icon: '🟨', color: 'text-yellow-600' },
  jsx: { icon: '🟨', color: 'text-yellow-600' },
  ts: { icon: '🔷', color: 'text-blue-600' },
  tsx: { icon: '🔷', color: 'text-blue-600' },
  md: { icon: '📝', color: 'text-blue-600' },
  json: { icon: '📋', color: 'text-amber-600' },
  html: { icon: '🌐', color: 'text-orange-600' },
  css: { icon: '🎨', color: 'text-pink-600' },
  yaml: { icon: '⚙️', color: 'text-purple-600' },
  yml: { icon: '⚙️', color: 'text-purple-600' },
  sh: { icon: '💻', color: 'text-gray-700' },
  csv: { icon: '📊', color: 'text-emerald-600' },
  pdf: { icon: '📕', color: 'text-red-600' },
};

function getFileIcon(name: string): { icon: string; color: string } {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return extTypeConfig[ext] || { icon: '📄', color: 'text-gray-500' };
}

function formatSize(bytes?: number): string {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Row Components
// ---------------------------------------------------------------------------

interface DirRowProps {
  node: LazyTreeNode;
  onToggle: (path: string) => void;
  isSelected: boolean;
}

const DirRow = memo(function DirRow({ node, onToggle, isSelected }: DirRowProps) {
  const { entry, isExpanded, isLoading, depth } = node;
  const paddingLeft = depth * 16 + 4;

  return (
    <div
      className={`group w-full flex items-center gap-1.5 px-2 hover:bg-gray-100 rounded transition-colors ${
        isSelected ? 'bg-blue-50' : ''
      }`}
      style={{ paddingLeft: `${paddingLeft}px`, height: `${ROW_HEIGHT}px` }}
    >
      <button
        onClick={() => onToggle(entry.path)}
        className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
      >
        <ChevronRight
          className={`w-3 h-3 text-gray-400 shrink-0 transition-transform ${
            isExpanded ? 'rotate-90' : ''
          }`}
        />
        {isExpanded ? (
          <FolderOpen className="w-3.5 h-3.5 text-amber-500 shrink-0" />
        ) : (
          <Folder className="w-3.5 h-3.5 text-amber-500 shrink-0" />
        )}
        <span className="text-xs font-medium text-gray-900 truncate" title={entry.display_name ? entry.name : undefined}>
          {entry.display_name || entry.name}
        </span>
      </button>
      {entry.children_count != null && (
        <span className="text-[10px] text-gray-500 shrink-0">
          {entry.children_count}
        </span>
      )}
      <a
        href={getZipDownloadUrl(entry.path)}
        download
        onClick={(e) => e.stopPropagation()}
        title="Download as ZIP"
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all shrink-0"
      >
        <Download size={12} />
      </a>
      {isLoading && <Loader2 className="w-3 h-3 text-blue-600 animate-spin shrink-0" />}
    </div>
  );
});

interface FileRowProps {
  node: LazyTreeNode;
  onSelect: (path: string) => void;
  isSelected: boolean;
}

const FileRow = memo(function FileRow({ node, onSelect, isSelected }: FileRowProps) {
  const { entry, depth } = node;
  const paddingLeft = depth * 16 + 20;
  const typeInfo = getFileIcon(entry.name);

  return (
    <div
      className={`group w-full flex items-center gap-1.5 px-2 rounded transition-colors ${
        isSelected
          ? 'bg-blue-50 border border-blue-200'
          : 'hover:bg-gray-100 border border-transparent'
      }`}
      style={{ paddingLeft: `${paddingLeft}px`, height: `${ROW_HEIGHT}px` }}
    >
      <button
        onClick={() => onSelect(entry.path)}
        className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
      >
        <span className={`text-sm shrink-0 ${typeInfo.color}`}>{typeInfo.icon}</span>
        <span className={`text-xs font-medium truncate ${isSelected ? 'text-blue-700' : 'text-gray-900'}`}>
          {entry.name}
        </span>
      </button>
      {entry.size != null && (
        <span className="text-[10px] text-gray-500 shrink-0">
          {formatSize(entry.size)}
        </span>
      )}
      <a
        href={getDownloadUrl(entry.path)}
        download
        onClick={(e) => e.stopPropagation()}
        title="Download file"
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all shrink-0"
      >
        <Download size={12} />
      </a>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function LazyDirectoryTree({
  taskId,
  showHidden,
  onFileSelect,
  selectedFile,
  className,
  maxHeight: _maxHeight = 600,
}: LazyDirectoryTreeProps) {
  const { flatRows, isLoading, error, loadRoot, toggleNode } = useLazyTree({
    taskId,
    showHidden,
  });
  const parentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadRoot();
  }, [loadRoot]);

  const handleToggle = useCallback(
    (path: string) => {
      toggleNode(path);
    },
    [toggleNode],
  );

  const handleFileSelect = useCallback(
    (path: string) => {
      onFileSelect(path);
    },
    [onFileSelect],
  );

  const virtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 15,
  });

  if (error) {
    return (
      <div className={`p-4 text-sm text-red-600 ${className ?? ''}`}>
        Error: {error}
      </div>
    );
  }

  if (isLoading && flatRows.length === 0) {
    return (
      <div className={`flex items-center gap-2 p-4 text-sm text-gray-500 ${className ?? ''}`}>
        <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
        Loading directory tree…
      </div>
    );
  }

  if (flatRows.length === 0) {
    return (
      <div className={`p-4 text-sm text-gray-500 ${className ?? ''}`}>No files found.</div>
    );
  }

  return (
    <div
      ref={parentRef}
      className={`overflow-auto ${className ?? ''}`}
      style={{ maxHeight: 'auto' }}
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const node = flatRows[virtualRow.index];
          const isDir = node.entry.type === 'directory';
          const isSelected = isDir ? false : node.entry.path === selectedFile;

          return (
            <div
              key={node.entry.path || virtualRow.index}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              {isDir ? (
                <DirRow node={node} onToggle={handleToggle} isSelected={false} />
              ) : (
                <FileRow node={node} onSelect={handleFileSelect} isSelected={isSelected} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
