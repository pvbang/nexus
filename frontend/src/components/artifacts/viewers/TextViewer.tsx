// ============================================================
// Nexus - TextViewer Component
// Simple monospace text display with optional line numbers
// ============================================================

import { useMemo } from 'react';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TextViewerProps {
  content: string;
  fileName?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TextViewer({ content, fileName }: TextViewerProps) {
  const lines = useMemo(() => content.split('\n'), [content]);
  const lineNumberWidth = String(lines.length).length;

  return (
    <div className="h-full w-full overflow-auto">
      {/* Header */}
      {fileName && (
        <div
          className={cn(
            'flex items-center px-3 py-1.5 border-b sticky top-0 z-10',
            'border-gray-200 bg-gray-50',
          )}
        >
          <span className="text-xs text-gray-600 truncate">{fileName}</span>
        </div>
      )}

      {/* Text content */}
      <pre
        className={cn(
          'm-0 p-4 text-[13px] leading-[1.6] font-mono',
          'bg-white text-gray-800',
        )}
      >
        {lines.map((line, idx) => (
          <div key={idx} className="flex">
            <span
              className={cn(
                'select-none text-right pr-4 shrink-0',
                'text-gray-400',
              )}
              style={{ minWidth: `${lineNumberWidth + 1}ch` }}
            >
              {idx + 1}
            </span>
            <span className="flex-1 whitespace-pre-wrap break-all">{line}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}
