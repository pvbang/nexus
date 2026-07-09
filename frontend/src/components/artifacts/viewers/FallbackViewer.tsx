// ============================================================
// MekongAI Nexus - FallbackViewer Component
// Shows file info and download button for unsupported file types
// ============================================================

import { Download, FileQuestion } from 'lucide-react';
import { getDownloadUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';
import type { FileMeta } from '../../../types/artifacts';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FallbackViewerProps {
  filePath: string;
  taskId?: string;
  meta?: FileMeta;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FallbackViewer({ filePath, taskId, meta }: FallbackViewerProps) {
  const downloadUrl = getDownloadUrl(filePath, taskId);
  const fileName = filePath.split('/').pop() || filePath;

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-6">
      <FileQuestion className="w-12 h-12 text-gray-400" />

      <div className="text-center space-y-1">
        <p className="text-sm font-medium text-gray-700 truncate max-w-[300px]">
          {fileName}
        </p>
        {meta && (
          <div className="text-xs text-gray-500 space-y-0.5">
            <p>{formatFileSize(meta.size)}</p>
            <p>{meta.mime_type}</p>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-500">
        Preview not available for this file type
      </p>

      <a
        href={downloadUrl}
        download
        className={cn(
          'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium',
          'transition-colors border',
          'bg-blue-50 hover:bg-blue-100 text-blue-700 border-blue-200',
        )}
      >
        <Download className="w-4 h-4" />
        Download
      </a>
    </div>
  );
}
