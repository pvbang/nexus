// ============================================================
// MekongAI Nexus - ImageViewer Component
// Simple image viewer with zoom toggle (fit vs actual size)
// ============================================================

import { useState, useCallback } from 'react';
import { Loader2, ZoomIn, ZoomOut, AlertCircle } from 'lucide-react';
import { getStreamUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ImageViewerProps {
  filePath: string;
  taskId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ImageViewer({ filePath, taskId }: ImageViewerProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [isZoomed, setIsZoomed] = useState(false);

  const src = getStreamUrl(filePath, taskId);

  const handleLoad = useCallback(() => {
    setIsLoading(false);
  }, []);

  const handleError = useCallback(() => {
    setIsLoading(false);
    setHasError(true);
  }, []);

  const toggleZoom = useCallback(() => {
    setIsZoomed((prev) => !prev);
  }, []);

  if (hasError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">Failed to load image</p>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col h-full w-full">
      {/* Toolbar */}
      <div
        className={cn(
          'flex items-center justify-end px-3 py-1.5 border-b shrink-0',
          'border-gray-200 bg-gray-50',
        )}
      >
        <button
          type="button"
          onClick={toggleZoom}
          className={cn(
            'p-1.5 rounded-md transition-colors',
            'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
          )}
          title={isZoomed ? 'Fit to container' : 'Actual size'}
        >
          {isZoomed ? <ZoomOut className="w-4 h-4" /> : <ZoomIn className="w-4 h-4" />}
        </button>
      </div>

      {/* Image container */}
      <div
        className={cn(
          'flex-1 overflow-auto flex items-center justify-center',
          isZoomed ? 'cursor-zoom-out' : 'cursor-zoom-in',
        )}
        onClick={toggleZoom}
      >
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-100 animate-pulse">
            <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
          </div>
        )}
        <img
          src={src}
          alt={filePath.split('/').pop() || 'Image'}
          onLoad={handleLoad}
          onError={handleError}
          className={cn(
            'transition-all duration-200',
            isZoomed ? 'max-w-none w-auto h-auto' : 'max-w-full max-h-full object-contain',
            isLoading && 'opacity-0',
          )}
        />
      </div>
    </div>
  );
}
