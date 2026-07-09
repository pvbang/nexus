// ============================================================
// MekongAI Nexus - HtmlViewer Component
// Sandboxed iframe rendering of HTML content
// ============================================================

import { useState, useEffect, useCallback } from 'react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { fetchFileChunk } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface HtmlViewerProps {
  filePath: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HtmlViewer({ filePath, taskId }: HtmlViewerProps) {
  const [content, setContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadContent = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetchFileChunk({ path: filePath, task_id: taskId });
      setContent(res.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load HTML content');
    } finally {
      setIsLoading(false);
    }
  }, [filePath, taskId]);

  useEffect(() => {
    loadContent();
  }, [loadContent]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col h-full w-full">
      {/* Toolbar */}
      <div
        className={cn(
          'flex items-center justify-between px-3 py-1.5 border-b shrink-0',
          'border-gray-200 bg-gray-50',
        )}
      >
        <span className="text-xs text-gray-500 truncate">
          {filePath.split('/').pop()}
        </span>
        <button
          type="button"
          onClick={loadContent}
          disabled={isLoading}
          className={cn(
            'p-1.5 rounded-md transition-colors',
            'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
          title="Refresh"
        >
          <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
        </button>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
        </div>
      ) : (
        <iframe
          srcDoc={content || ''}
          sandbox="allow-scripts"
          title="HTML Preview"
          className="flex-1 w-full border-0 bg-white"
        />
      )}
    </div>
  );
}
