// ============================================================
// Nexus - ArtifactsPage
// Enhanced artifacts page with:
// - Route-aware task scoping (/artifacts/:taskId)
// - Session-grouped overview when no taskId
// - Lazy-loading tree and multi-format viewer
// ============================================================

import { useState, useCallback, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Eye, EyeOff, RefreshCw, FileText, Download, FolderTree, ChevronRight, List } from 'lucide-react';
import { cn } from '../../lib/cn';
import { getDownloadUrl } from '../../api/artifacts';
import { LazyDirectoryTree } from './LazyDirectoryTree';
import { FileViewer } from './FileViewer';
import { SessionGroupedTree } from './SessionGroupedTree';

// ---------------------------------------------------------------------------
// Empty state when no file is selected
// ---------------------------------------------------------------------------

function EmptyFileState() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400">
      <FileText className="w-12 h-12 opacity-40" />
      <p className="text-sm">Select a file to preview</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function ArtifactsPage() {
  const { taskId: routeTaskId } = useParams<{ taskId?: string }>();
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [treeKey, setTreeKey] = useState(0);
  const [viewMode, setViewMode] = useState<'tree' | 'grouped'>(routeTaskId ? 'tree' : 'grouped');
  const [outlineMode, setOutlineMode] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const isResizing = useRef(false);

  const effectiveTaskId = routeTaskId || undefined;
  const isMarkdownFile = selectedFile?.match(/\.(md|markdown)$/i) != null;

  const handleFileSelect = useCallback((file: string | null) => {
    setSelectedFile(file);
    setOutlineMode(false);
  }, []);

  const handleRefresh = useCallback(() => {
    setTreeKey((k) => k + 1);
  }, []);

  const handleToggleHidden = useCallback(() => {
    setShowHidden((v) => !v);
  }, []);

  const handleToggleView = useCallback(() => {
    setViewMode((v) => v === 'tree' ? 'grouped' : 'tree');
  }, []);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      const newWidth = Math.min(Math.max(startWidth + ev.clientX - startX, 200), 600);
      setSidebarWidth(newWidth);
    };
    const onUp = () => {
      isResizing.current = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [sidebarWidth]);

  const breadcrumb = useMemo(() => {
    if (!effectiveTaskId) return null;
    return (
      <div className="flex items-center gap-1 px-6 py-2 border-b border-gray-200 text-xs text-gray-500 bg-gray-50">
        <Link to="/artifacts" className="hover:text-blue-600 transition-colors">
          All Artifacts
        </Link>
        <ChevronRight className="w-3 h-3" />
        <span className="text-gray-700 font-medium">Task {effectiveTaskId.slice(0, 8)}</span>
      </div>
    );
  }, [effectiveTaskId]);

  return (
    <div className="flex h-full w-full overflow-hidden bg-white">
      {/* Left panel: Directory Tree */}
      <div
        className="border-r border-gray-200 overflow-hidden flex flex-col shrink-0 bg-gray-50 relative"
        style={{ width: `${sidebarWidth}px` }}
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-200 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-gray-900 flex-1">
            {effectiveTaskId ? `Task Files` : 'Files'}
          </h2>
          {!effectiveTaskId && (
            <button
              type="button"
              title={viewMode === 'grouped' ? 'Switch to flat tree' : 'Switch to grouped view'}
              onClick={handleToggleView}
              className={cn(
                'p-1 rounded hover:bg-gray-100 transition-colors',
                viewMode === 'grouped' ? 'text-blue-600' : 'text-gray-500',
              )}
            >
              <FolderTree className="w-4 h-4" />
            </button>
          )}
          <button
            type="button"
            title={showHidden ? 'Hide hidden files' : 'Show hidden files'}
            onClick={handleToggleHidden}
            className={cn(
              'p-1 rounded hover:bg-gray-100 transition-colors',
              showHidden ? 'text-blue-600' : 'text-gray-500',
            )}
          >
            {showHidden ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
          </button>
          <button
            type="button"
            title="Refresh"
            onClick={handleRefresh}
            className="p-1 rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {/* Tree content */}
        <div className="flex-1 overflow-auto py-2">
          {viewMode === 'grouped' && !effectiveTaskId ? (
            <SessionGroupedTree
              key={treeKey}
              onFileSelect={handleFileSelect}
              selectedFile={selectedFile}
            />
          ) : (
            <LazyDirectoryTree
              key={treeKey}
              taskId={effectiveTaskId}
              showHidden={showHidden}
              onFileSelect={handleFileSelect}
              selectedFile={selectedFile}
            />
          )}
        </div>
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={handleResizeStart}
        className="w-1 cursor-col-resize hover:bg-blue-400 active:bg-blue-500 transition-colors shrink-0"
      />

      {/* Right panel: File Viewer */}
      <div className="flex-1 overflow-hidden flex flex-col min-w-0">
        {breadcrumb}
        {selectedFile ? (
          <>
            {/* Breadcrumb / path bar */}
            <div className="px-6 py-3 border-b border-gray-200 flex items-center gap-2 shrink-0 bg-white">
              <span className="text-xs text-gray-600 truncate flex-1">
                {selectedFile}
              </span>
              {isMarkdownFile && (
                <button
                  type="button"
                  onClick={() => setOutlineMode(v => !v)}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md font-medium transition-all duration-200 shrink-0',
                    outlineMode
                      ? 'bg-blue-50 text-blue-700 border border-blue-200 shadow-sm'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200 border border-transparent',
                  )}
                  title={outlineMode ? 'Switch to rendered view' : 'Switch to outline view'}
                >
                  {outlineMode ? <FileText size={14} /> : <List size={14} />}
                  {outlineMode ? 'Rendered' : 'Outline'}
                </button>
              )}
              <a
                href={getDownloadUrl(selectedFile, effectiveTaskId)}
                download
                className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors shrink-0"
              >
                <Download size={14} />
                Download
              </a>
            </div>
            {/* Viewer */}
            <div className="flex-1 min-h-0">
              <FileViewer filePath={selectedFile} taskId={effectiveTaskId} outlineMode={outlineMode} />
            </div>
          </>
        ) : (
          <EmptyFileState />
        )}
      </div>
    </div>
  );
}
