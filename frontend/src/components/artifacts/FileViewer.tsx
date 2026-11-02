// ============================================================
// Nexus - FileViewer Orchestrator
// Routes file paths to appropriate viewer based on FileMeta preview_type.
// Heavy viewers (PDF, DOCX) are lazy-loaded via React.lazy().
// ============================================================

import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { fetchFileMeta, fetchFileChunk } from '../../api/artifacts';
import { cn } from '../../lib/cn';
import type { FileMeta, PreviewType } from '../../types/artifacts';
import {
  ImageViewer,
  VideoViewer,
  AudioViewer,
  HtmlViewer,
  MarkdownViewer,
  CodeViewer,
  TextViewer,
  FallbackViewer,
  VirtualTextViewer,
} from './viewers';
import { MarkdownOutlineView } from './MarkdownOutlineView';

// ---------------------------------------------------------------------------
// Lazy-loaded heavy viewers
// ---------------------------------------------------------------------------

const PdfViewerLazy = lazy(() =>
  import('./viewers/PdfViewer').then((m) => ({ default: m.PdfViewer })),
);
const DocxViewerLazy = lazy(() =>
  import('./viewers/DocxViewer').then((m) => ({ default: m.DocxViewer })),
);
const SpreadsheetViewerLazy = lazy(() =>
  import('./viewers/SpreadsheetViewer').then((m) => ({ default: m.SpreadsheetViewer })),
);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileViewerProps {
  filePath: string;
  taskId?: string;
  className?: string;
  outlineMode?: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Text-based types that need content fetching */
const TEXT_PREVIEW_TYPES: PreviewType[] = ['markdown', 'code', 'text'];

/** Size threshold: files >= this use VirtualTextViewer (500KB) */
const VIRTUAL_VIEWER_THRESHOLD = 500 * 1024;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FileViewer({ filePath, taskId, className, outlineMode }: FileViewerProps) {
  const [meta, setMeta] = useState<FileMeta | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMeta, setIsLoadingMeta] = useState(true);
  const [isLoadingContent, setIsLoadingContent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch file metadata
  useEffect(() => {
    let cancelled = false;
    setIsLoadingMeta(true);
    setError(null);
    setMeta(null);
    setContent(null);
    setHasMore(false);

    fetchFileMeta(filePath, taskId)
      .then((fileMeta) => {
        if (cancelled) return;
        setMeta(fileMeta);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load file metadata');
      })
      .finally(() => {
        if (!cancelled) setIsLoadingMeta(false);
      });

    return () => {
      cancelled = true;
    };
  }, [filePath, taskId]);

  // Determine if this is a large text file that should use VirtualTextViewer
  // Exception: markdown in outline mode still fetches full content for heading parsing
  const isLargeTextFile =
    meta !== null &&
    TEXT_PREVIEW_TYPES.includes(meta.preview_type) &&
    meta.size >= VIRTUAL_VIEWER_THRESHOLD;
  const usesVirtualViewer = isLargeTextFile && !(outlineMode && meta?.preview_type === 'markdown');

  // Fetch text content when needed (only for small files, or large markdown in outline mode)
  useEffect(() => {
    if (!meta) return;
    if (!TEXT_PREVIEW_TYPES.includes(meta.preview_type)) return;
    if (meta.size >= VIRTUAL_VIEWER_THRESHOLD && !(outlineMode && meta.preview_type === 'markdown')) return;

    let cancelled = false;
    setIsLoadingContent(true);

    fetchFileChunk({ path: filePath, task_id: taskId })
      .then((res) => {
        if (cancelled) return;
        setContent(res.content);
        setHasMore(res.has_more);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load file content');
      })
      .finally(() => {
        if (!cancelled) setIsLoadingContent(false);
      });

    return () => {
      cancelled = true;
    };
  }, [meta, filePath, taskId, outlineMode]);

  // Load more content for large text files
  const loadMore = useCallback(async () => {
    if (!content || !meta) return;
    setIsLoadingContent(true);
    try {
      const res = await fetchFileChunk({
        path: filePath,
        task_id: taskId,
        offset: content.length,
      });
      setContent((prev) => (prev || '') + res.content);
      setHasMore(res.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load more content');
    } finally {
      setIsLoadingContent(false);
    }
  }, [content, meta, filePath, taskId]);

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  if (isLoadingMeta) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
          <p className="text-sm text-gray-500">Loading file...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <div className="flex flex-col items-center gap-3 text-red-600">
          <AlertCircle className="w-8 h-8" />
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (!meta) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <p className="text-sm text-gray-500">File not found</p>
      </div>
    );
  }

  // Large text files → VirtualTextViewer (no content fetch needed)
  if (usesVirtualViewer) {
    return (
      <div className={cn('flex flex-col h-full w-full', className)}>
        <div className="flex-1 min-h-0">
          <VirtualTextViewer filePath={filePath} taskId={taskId} />
        </div>
      </div>
    );
  }

  // Loading content for small text-based viewers
  if (TEXT_PREVIEW_TYPES.includes(meta.preview_type) && content === null) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
      </div>
    );
  }

  // Lazy-load fallback
  const suspenseFallback = (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
    </div>
  );

  // ---------------------------------------------------------------------------
  // Viewer routing
  // ---------------------------------------------------------------------------

  const fileName = filePath.split('/').pop() || filePath;

  const renderViewer = () => {
    switch (meta.preview_type) {
      case 'image':
        return <ImageViewer filePath={filePath} taskId={taskId} />;

      case 'video':
        return <VideoViewer filePath={filePath} taskId={taskId} />;

      case 'audio':
        return <AudioViewer filePath={filePath} taskId={taskId} />;

      case 'pdf':
        return (
          <Suspense fallback={suspenseFallback}>
            <PdfViewerLazy filePath={filePath} taskId={taskId} />
          </Suspense>
        );

      case 'html':
        return <HtmlViewer filePath={filePath} taskId={taskId} />;

      case 'markdown':
        return outlineMode
          ? <MarkdownOutlineView content={content || ''} />
          : <MarkdownViewer content={content || ''} filePath={filePath} taskId={taskId} />;

      case 'document':
        return (
          <Suspense fallback={suspenseFallback}>
            <DocxViewerLazy filePath={filePath} taskId={taskId} />
          </Suspense>
        );

      case 'spreadsheet':
        return (
          <Suspense fallback={suspenseFallback}>
            <SpreadsheetViewerLazy filePath={filePath} taskId={taskId} />
          </Suspense>
        );

      case 'code':
        return <CodeViewer content={content || ''} fileName={fileName} />;

      case 'text':
        return <TextViewer content={content || ''} fileName={fileName} />;

      default:
        return <FallbackViewer filePath={filePath} taskId={taskId} meta={meta} />;
    }
  };

  return (
    <div className={cn('flex flex-col h-full w-full', className)}>
      <div className="flex-1 min-h-0 relative">{renderViewer()}</div>

      {/* Load more button for large text files */}
      {hasMore && TEXT_PREVIEW_TYPES.includes(meta.preview_type) && (
        <div
          className={cn(
            'flex items-center justify-center px-3 py-2 border-t shrink-0',
            'border-gray-200 bg-gray-50',
          )}
        >
          <button
            type="button"
            onClick={loadMore}
            disabled={isLoadingContent}
            className={cn(
              'inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium',
              'transition-colors',
              'text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {isLoadingContent ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                Loading...
              </>
            ) : (
              'Load more'
            )}
          </button>
        </div>
      )}
    </div>
  );
}
