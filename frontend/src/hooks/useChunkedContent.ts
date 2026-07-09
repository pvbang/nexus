// ============================================================
// useChunkedContent - Progressive loading of large text files
// ============================================================

import { useState, useCallback, useEffect, useRef } from 'react';
import { fetchFileChunk } from '../api/artifacts';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseChunkedContentOptions {
  filePath: string;
  taskId?: string;
  chunkSize?: number; // bytes per chunk, default 65536 (64KB)
  initialLoad?: boolean; // auto-load first chunk on mount, default true
}

interface UseChunkedContentReturn {
  lines: string[];
  totalSize: number;
  totalLines: number | null;
  loadedBytes: number;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  loadMore: () => Promise<void>;
  loadAll: () => Promise<void>;
  reset: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_CHUNK_SIZE = 64 * 1024; // 64KB
const MAX_TOTAL_BYTES = 50 * 1024 * 1024; // 50MB safety limit

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChunkedContent({
  filePath,
  taskId,
  chunkSize = DEFAULT_CHUNK_SIZE,
  initialLoad = true,
}: UseChunkedContentOptions): UseChunkedContentReturn {
  const [lines, setLines] = useState<string[]>([]);
  const [totalSize, setTotalSize] = useState(0);
  const [totalLines, setTotalLines] = useState<number | null>(null);
  const [loadedBytes, setLoadedBytes] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Buffer for incomplete last line at chunk boundary
  const partialLineRef = useRef('');
  const loadedBytesRef = useRef(0);
  const hasMoreRef = useRef(true);
  const isLoadingRef = useRef(false);

  const reset = useCallback(() => {
    setLines([]);
    setTotalSize(0);
    setTotalLines(null);
    setLoadedBytes(0);
    setHasMore(true);
    setIsLoading(false);
    setError(null);
    partialLineRef.current = '';
    loadedBytesRef.current = 0;
    hasMoreRef.current = true;
    isLoadingRef.current = false;
  }, []);

  const loadMore = useCallback(async () => {
    if (isLoadingRef.current || !hasMoreRef.current) return;

    isLoadingRef.current = true;
    setIsLoading(true);
    setError(null);

    try {
      const res = await fetchFileChunk({
        path: filePath,
        task_id: taskId,
        offset: loadedBytesRef.current,
        limit: chunkSize,
      });

      setTotalSize(res.total_size);
      if (res.total_lines !== null) {
        setTotalLines(res.total_lines);
      }

      // Prepend any buffered partial line from previous chunk
      const rawContent = partialLineRef.current + res.content;

      // Split into lines
      const splitLines = rawContent.split('\n');

      // If there's more content, the last "line" may be incomplete
      if (res.has_more) {
        partialLineRef.current = splitLines.pop() || '';
      } else {
        partialLineRef.current = '';
      }

      setLines((prev) => [...prev, ...splitLines]);

      const newLoadedBytes = loadedBytesRef.current + res.length;
      loadedBytesRef.current = newLoadedBytes;
      setLoadedBytes(newLoadedBytes);

      hasMoreRef.current = res.has_more;
      setHasMore(res.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load chunk');
    } finally {
      isLoadingRef.current = false;
      setIsLoading(false);
    }
  }, [filePath, taskId, chunkSize]);

  const loadAll = useCallback(async () => {
    while (hasMoreRef.current && loadedBytesRef.current < MAX_TOTAL_BYTES) {
      await loadMore();
      if (!hasMoreRef.current) break;
    }
  }, [loadMore]);

  // Auto-load first chunk on mount
  useEffect(() => {
    reset();
    if (initialLoad) {
      // Small delay to ensure reset state is applied
      const id = requestAnimationFrame(() => {
        loadMore();
      });
      return () => cancelAnimationFrame(id);
    }
  }, [filePath, taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    lines,
    totalSize,
    totalLines,
    loadedBytes,
    hasMore,
    isLoading,
    error,
    loadMore,
    loadAll,
    reset,
  };
}
