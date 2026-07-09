// ============================================================
// MekongAI Nexus - AudioViewer Component
// Native HTML5 audio player with streaming support
// ============================================================

import { useState, useCallback } from 'react';
import { AlertCircle, Loader2, Music } from 'lucide-react';
import { getStreamUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AudioViewerProps {
  filePath: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AudioViewer({ filePath, taskId }: AudioViewerProps) {
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
        <p className="text-sm">Failed to load audio</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-full w-full gap-4 p-6">
      <Music className="w-12 h-12 text-gray-400" />
      <p className="text-sm text-gray-600 truncate max-w-full">
        {filePath.split('/').pop()}
      </p>
      {isLoading && <Loader2 className="w-5 h-5 animate-spin text-blue-600" />}
      <audio
        src={src}
        controls
        onCanPlay={handleCanPlay}
        onError={handleError}
        className={cn('w-full max-w-md', isLoading && 'opacity-0')}
      >
        Your browser does not support the audio element.
      </audio>
    </div>
  );
}
