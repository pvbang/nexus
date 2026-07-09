// ============================================================
// VirtualTextViewer - Virtual line-by-line renderer for large files
// Uses @tanstack/react-virtual for virtualization and useChunkedContent for data.
// ============================================================

import { useRef, useEffect, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Loader2, FileText, CheckCircle2 } from 'lucide-react';
import { useChunkedContent } from '../../../hooks/useChunkedContent';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VirtualTextViewerProps {
  filePath: string;
  taskId?: string;
  language?: string;
  className?: string;
  showLineNumbers?: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LINE_HEIGHT = 20;
const OVERSCAN = 20;
const LOAD_THRESHOLD = 50; // Load more when within 50 lines of end

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VirtualTextViewer({
  filePath,
  taskId,
  language,
  className,
  showLineNumbers = true,
}: VirtualTextViewerProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const { lines, totalSize, totalLines, loadedBytes, hasMore, isLoading, error, loadMore } =
    useChunkedContent({ filePath, taskId });

  // Virtualizer setup
  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => LINE_HEIGHT,
    overscan: OVERSCAN,
  });

  // Infinite scroll: load more when near end
  const virtualItems = virtualizer.getVirtualItems();
  useEffect(() => {
    const lastItem = virtualItems.at(-1);
    if (!lastItem) return;
    if (lastItem.index >= lines.length - LOAD_THRESHOLD && hasMore && !isLoading) {
      loadMore();
    }
  }, [virtualItems, lines.length, hasMore, isLoading, loadMore]);

  // Gutter width based on total line count
  const gutterWidth = useMemo(() => {
    const digits = Math.max(String(totalLines ?? lines.length).length, 3);
    return `${digits + 1}ch`;
  }, [totalLines, lines.length]);

  // Progress percentage
  const progress = totalSize > 0 ? Math.min((loadedBytes / totalSize) * 100, 100) : 0;

  // File name from path
  const fileName = filePath.split('/').pop() || filePath;

  // ---------------------------------------------------------------------------
  // Error state
  // ---------------------------------------------------------------------------

  if (error && lines.length === 0) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <p className="text-sm text-red-600">{error}</p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className={cn('flex flex-col h-full w-full', className)}>
      {/* Header bar */}
      <div
        className={cn(
          'flex items-center gap-3 px-3 py-1.5 text-xs shrink-0',
          'border-b border-gray-200 bg-gray-50',
          'text-gray-600',
        )}
      >
        <FileText className="w-3.5 h-3.5 shrink-0" />
        <span className="font-medium truncate">{fileName}</span>
        {language && (
          <span className="px-1.5 py-0.5 rounded bg-gray-200 text-[10px] uppercase">
            {language}
          </span>
        )}
        <span className="ml-auto whitespace-nowrap">
          {lines.length.toLocaleString()} lines loaded
        </span>
        <span className="text-gray-400">•</span>
        <span className="whitespace-nowrap">
          {formatBytes(loadedBytes)} / {formatBytes(totalSize)}
        </span>
        {/* Progress bar */}
        <div className="w-16 h-1.5 rounded-full bg-gray-200 overflow-hidden">
          <div
            className="h-full rounded-full bg-blue-500 transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Virtualized content area */}
      <div ref={parentRef} className="flex-1 min-h-0 overflow-auto font-mono text-sm">
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualItems.map((virtualItem) => (
            <div
              key={virtualItem.index}
              className="flex absolute top-0 left-0 w-full px-3"
              style={{
                height: `${virtualItem.size}px`,
                transform: `translateY(${virtualItem.start}px)`,
              }}
            >
              {showLineNumbers && (
                <span
                  className="inline-block text-right pr-3 text-gray-400 select-none shrink-0"
                  style={{ width: gutterWidth }}
                >
                  {virtualItem.index + 1}
                </span>
              )}
              <span className="whitespace-pre-wrap break-all">
                {lines[virtualItem.index]}
              </span>
            </div>
          ))}
        </div>

        {/* Loading indicator at bottom */}
        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-3 text-xs text-gray-500">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <span>Loading more...</span>
          </div>
        )}

        {/* All content loaded indicator */}
        {!hasMore && lines.length > 0 && !isLoading && (
          <div className="flex items-center justify-center gap-2 py-3 text-xs text-gray-400">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>All content loaded</span>
          </div>
        )}
      </div>
    </div>
  );
}
