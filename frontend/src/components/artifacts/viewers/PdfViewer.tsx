// ============================================================
// MekongAI Nexus - PdfViewer Component
// PDF rendering with react-pdf, page navigation, and zoom controls
// ============================================================

import { useState, useCallback, useRef, useEffect } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import {
  AlertCircle,
  Loader2,
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Maximize2,
} from 'lucide-react';
import { getStreamUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Configure PDF.js worker for Vite
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PdfViewerProps {
  filePath: string;
  taskId?: string;
}

type ZoomMode = 'fit-width' | 'fit-page' | 'custom';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];
const DEFAULT_ZOOM = 1;
const PAGES_BUFFER = 1; // Render current ± buffer pages

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PdfViewer({ filePath, taskId }: PdfViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(DEFAULT_ZOOM);
  const [zoomMode, setZoomMode] = useState<ZoomMode>('fit-width');
  const [error, setError] = useState<string | null>(null);
  const [containerWidth, setContainerWidth] = useState<number>(600);
  const containerRef = useRef<HTMLDivElement>(null);

  const src = getStreamUrl(filePath, taskId);

  // Observe container width for fit modes
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total);
    setCurrentPage(1);
    setError(null);
  }, []);

  const onDocumentLoadError = useCallback((err: Error) => {
    setError(err.message || 'Failed to load PDF');
  }, []);

  const goToPage = useCallback(
    (page: number) => {
      setCurrentPage(Math.max(1, Math.min(page, numPages)));
    },
    [numPages],
  );

  const zoomIn = useCallback(() => {
    setZoomMode('custom');
    setScale((prev) => {
      const nextIdx = ZOOM_STEPS.findIndex((s) => s > prev);
      return nextIdx >= 0 ? ZOOM_STEPS[nextIdx] : prev;
    });
  }, []);

  const zoomOut = useCallback(() => {
    setZoomMode('custom');
    setScale((prev) => {
      const prevIdx = [...ZOOM_STEPS].reverse().findIndex((s) => s < prev);
      return prevIdx >= 0 ? ZOOM_STEPS[ZOOM_STEPS.length - 1 - prevIdx] : prev;
    });
  }, []);

  const fitWidth = useCallback(() => {
    setZoomMode('fit-width');
  }, []);

  // Calculate effective width based on zoom mode
  const effectiveWidth = zoomMode === 'fit-width' ? containerWidth - 32 : undefined;
  const effectiveScale = zoomMode === 'custom' ? scale : undefined;

  // Determine which pages to render (current ± buffer)
  const pagesToRender: number[] = [];
  for (
    let i = Math.max(1, currentPage - PAGES_BUFFER);
    i <= Math.min(numPages, currentPage + PAGES_BUFFER);
    i++
  ) {
    pagesToRender.push(i);
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full">
      {/* Toolbar */}
      <div
        className={cn(
          'flex items-center justify-between px-3 py-1.5 border-b shrink-0 gap-2',
          'border-gray-200 bg-gray-50',
        )}
      >
        {/* Page navigation */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className={cn(
              'p-1 rounded transition-colors',
              'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
              'disabled:opacity-30 disabled:cursor-not-allowed',
            )}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-xs text-gray-600 min-w-[60px] text-center">
            {currentPage} / {numPages || '—'}
          </span>
          <button
            type="button"
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= numPages}
            className={cn(
              'p-1 rounded transition-colors',
              'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
              'disabled:opacity-30 disabled:cursor-not-allowed',
            )}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        {/* Zoom controls */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={zoomOut}
            className={cn(
              'p-1 rounded transition-colors',
              'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
            )}
            title="Zoom out"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs text-gray-600 min-w-[40px] text-center">
            {zoomMode === 'fit-width' ? 'Fit' : `${Math.round(scale * 100)}%`}
          </span>
          <button
            type="button"
            onClick={zoomIn}
            className={cn(
              'p-1 rounded transition-colors',
              'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
            )}
            title="Zoom in"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={fitWidth}
            className={cn(
              'p-1 rounded transition-colors',
              zoomMode === 'fit-width'
                ? 'text-blue-600 bg-blue-100'
                : 'text-gray-500 hover:text-gray-700 hover:bg-gray-200',
            )}
            title="Fit width"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* PDF Content */}
      <div ref={containerRef} className="flex-1 overflow-auto flex justify-center p-4">
        <Document
          file={src}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
            </div>
          }
        >
          <div className="flex flex-col items-center gap-4">
            {pagesToRender.map((pageNum) => (
              <Page
                key={pageNum}
                pageNumber={pageNum}
                width={effectiveWidth}
                scale={effectiveScale}
                loading={
                  <div className="flex items-center justify-center h-[400px] bg-gray-100 rounded">
                    <Loader2 className="w-5 h-5 animate-spin text-blue-600" />
                  </div>
                }
                className="shadow-md rounded"
              />
            ))}
          </div>
        </Document>
      </div>
    </div>
  );
}
