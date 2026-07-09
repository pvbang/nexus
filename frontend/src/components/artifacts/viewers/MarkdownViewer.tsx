// ============================================================
// MekongAI Nexus - MarkdownViewer Component
// Wraps the shared MarkdownRenderer with proper scrolling container
// ============================================================

import { useCallback } from 'react';
import { MarkdownRenderer } from '../../ui/MarkdownRenderer';
import { getStreamUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MarkdownViewerProps {
  content: string;
  filePath?: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MarkdownViewer({ content, filePath, taskId }: MarkdownViewerProps) {
  const transformImageUri = useCallback(
    (src: string) => {
      if (!src) return src;
      if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('data:')) {
        return src;
      }
      const dir = filePath ? filePath.replace(/\/[^/]*$/, '') : '';
      const resolved = src.startsWith('/')
        ? src.slice(1)
        : dir
          ? `${dir}/${src}`.replace(/\/\.\//g, '/')
          : src;
      return getStreamUrl(resolved, taskId);
    },
    [filePath, taskId],
  );

  return (
    <div className={cn('absolute inset-0 overflow-auto p-6')}>
      <MarkdownRenderer content={content} className="max-w-none" transformImageUri={transformImageUri} />
    </div>
  );
}
