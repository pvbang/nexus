// ============================================================
// Nexus - File Content LRU Cache Hook
// Provides cached file content fetching with LRU eviction.
// Cache key: `${task_id}/${file_path}` or file_path for global.
// Invalidates when file.modified changes.
// ============================================================

import { useCallback, useRef } from 'react'
import { apiGet } from '../api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ArtifactFile {
  path: string
  name: string
  size: number
  modified: number
  type: string
  is_binary?: boolean
  is_context_doc?: boolean
  task_id?: string
  download_url?: string
}

interface ArtifactContent {
  content: string | null
  is_binary: boolean
  download_url?: string
  message?: string
  type: string
}

interface CacheEntry {
  content: ArtifactContent
  modified: number
  accessedAt: number
}

// ---------------------------------------------------------------------------
// Module-level LRU Cache (shared across all component instances)
// ---------------------------------------------------------------------------

const MAX_CACHE_SIZE = 30
const cache = new Map<string, CacheEntry>()

function getCacheKey(file: ArtifactFile): string {
  return file.task_id ? `${file.task_id}/${file.path}` : file.path
}

function evictOldest(): void {
  if (cache.size <= MAX_CACHE_SIZE) return

  let oldestKey: string | null = null
  let oldestTime = Infinity

  for (const [key, entry] of cache) {
    if (entry.accessedAt < oldestTime) {
      oldestTime = entry.accessedAt
      oldestKey = key
    }
  }

  if (oldestKey) {
    cache.delete(oldestKey)
  }
}

/** Clear entire cache (for manual refresh) */
export function clearFileContentCache(): void {
  cache.clear()
}

/** Invalidate a specific cache entry */
export function invalidateCacheEntry(file: ArtifactFile): void {
  cache.delete(getCacheKey(file))
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseFileContentCacheReturn {
  fetchContent: (file: ArtifactFile) => Promise<ArtifactContent>
  clearCache: () => void
  getCachedContent: (file: ArtifactFile) => ArtifactContent | null
}

export function useFileContentCache(): UseFileContentCacheReturn {
  // Use ref to avoid stale closure issues
  const fetchingRef = useRef<Set<string>>(new Set())

  const getCachedContent = useCallback((file: ArtifactFile): ArtifactContent | null => {
    const key = getCacheKey(file)
    const entry = cache.get(key)

    if (!entry) return null

    // Invalidate if file has been modified
    if (file.modified && entry.modified && file.modified > entry.modified) {
      cache.delete(key)
      return null
    }

    // Update access time (LRU)
    entry.accessedAt = Date.now()
    return entry.content
  }, [])

  const fetchContent = useCallback(async (file: ArtifactFile): Promise<ArtifactContent> => {
    const key = getCacheKey(file)

    // Check cache first
    const cached = getCachedContent(file)
    if (cached) return cached

    // Prevent duplicate fetches for same file
    if (fetchingRef.current.has(key)) {
      // Wait for existing fetch to complete
      await new Promise(resolve => setTimeout(resolve, 100))
      const result = cache.get(key)
      if (result) {
        result.accessedAt = Date.now()
        return result.content
      }
    }

    fetchingRef.current.add(key)

    try {
      const readUrl = file.task_id
        ? `/artifacts/task/${file.task_id}/read/${file.path}`
        : `/artifacts/read/${file.path}`

      const result = await apiGet<ArtifactContent>(readUrl)

      // Store in cache
      cache.set(key, {
        content: result,
        modified: file.modified,
        accessedAt: Date.now(),
      })

      // Evict if over limit
      evictOldest()

      return result
    } finally {
      fetchingRef.current.delete(key)
    }
  }, [getCachedContent])

  return {
    fetchContent,
    clearCache: clearFileContentCache,
    getCachedContent,
  }
}
