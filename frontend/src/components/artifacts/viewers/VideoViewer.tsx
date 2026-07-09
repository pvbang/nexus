// ============================================================
// MekongAI Nexus - VideoViewer Component
// Native HTML5 video player with streaming support
// ============================================================

import { useState, useCallback } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { getStreamUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VideoViewerProps {
  filePath: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VideoViewer({ filePath, taskId }: VideoViewerProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const src = getStreamUrl(filePath, taskId);

  const handleCanPlay = useCallback(() => {
    setIsLoading(false);
  }, []);

  const handleError = useCallback(() => {
    setIsLoading(false);
    setHasError(true);
  }, []);

  if (hasError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">Failed to load video</p>
      </div>
    );
  }

  return (
    <div className="relative flex items-center justify-center h-full w-full bg-gray-900">
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        </div>
      )}
      <video
        src={src}
        controls
        onCanPlay={handleCanPlay}
        onError={handleError}
        className={cn('max-w-full max-h-full', isLoading && 'opacity-0')}
      >
        Your browser does not support the video element.
      </video>
    </div>
  );
}
