// ============================================================
// Nexus - DocxViewer Component
// Converts DOCX to HTML using mammoth and renders in styled container
// ============================================================

import { useState, useEffect, useCallback } from 'react';
import mammoth from 'mammoth';
import { AlertCircle, Loader2 } from 'lucide-react';
import { getDownloadUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DocxViewerProps {
  filePath: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DocxViewer({ filePath, taskId }: DocxViewerProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDocument = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const url = getDownloadUrl(filePath, taskId);
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Failed to fetch document: ${response.statusText}`);
      }
      const arrayBuffer = await response.arrayBuffer();
      const result = await mammoth.convertToHtml({ arrayBuffer });
      setHtml(result.value);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to convert document');
    } finally {
      setIsLoading(false);
    }
  }, [filePath, taskId]);

  useEffect(() => {
    loadDocument();
  }, [loadDocument]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
        <p className="text-sm text-gray-500">Converting document...</p>
      </div>
    );
  }

  return (
    <div className="h-full w-full overflow-auto p-6">
      <div
        className={cn(
          'doc-theme max-w-[800px] mx-auto prose prose-sm',
          'prose-headings:text-gray-900',
          'prose-p:text-gray-700',
          'prose-table:border-collapse prose-td:border prose-td:border-gray-300',
          'prose-td:p-2',
          'prose-img:max-w-full prose-img:rounded',
        )}
        dangerouslySetInnerHTML={{ __html: html || '' }}
      />
    </div>
  );
}
