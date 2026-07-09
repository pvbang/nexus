/**
 * Centralized localStorage management with:
 * - Namespace prefix to avoid key collisions
 * - Quota monitoring & eviction
 * - Type-safe get/set with JSON serialization
 * - TTL (time-to-live) support for expiring keys
 * - Error handling for QuotaExceededError
 */

const NAMESPACE = 'nexus';
const SEPARATOR = ':';
const MAX_STORAGE_MB = 4; // Conservative limit (most browsers allow 5-10MB)

interface StorageItem<T> {
  value: T;
  expiresAt?: number; // Unix timestamp ms
  createdAt: number;
}

function prefixKey(key: string): string {
  return `${NAMESPACE}${SEPARATOR}${key}`;
}

/**
 * Get item from localStorage with type safety and TTL check
 */
export function storageGet<T>(key: string, defaultValue: T): T;
export function storageGet<T>(key: string): T | null;
export function storageGet<T>(key: string, defaultValue?: T): T | null {
  try {
    const raw = localStorage.getItem(prefixKey(key));
    if (!raw) return defaultValue ?? null;

    const item: StorageItem<T> = JSON.parse(raw);

    // Check TTL
    if (item.expiresAt && Date.now() > item.expiresAt) {
      localStorage.removeItem(prefixKey(key));
      return defaultValue ?? null;
    }

    return item.value;
  } catch {
    return defaultValue ?? null;
  }
}

/**
 * Set item in localStorage with optional TTL
 * @param ttlMs - Time to live in milliseconds (optional)
 */
export function storageSet<T>(key: string, value: T, ttlMs?: number): boolean {
  try {
    const item: StorageItem<T> = {
      value,
      createdAt: Date.now(),
      ...(ttlMs ? { expiresAt: Date.now() + ttlMs } : {}),
    };

    localStorage.setItem(prefixKey(key), JSON.stringify(item));
    return true;
  } catch (e) {
    if (e instanceof DOMException && e.name === 'QuotaExceededError') {
      // Evict expired items first
      evictExpired();
      // Try again
      try {
        const item: StorageItem<T> = {
          value,
          createdAt: Date.now(),
          ...(ttlMs ? { expiresAt: Date.now() + ttlMs } : {}),
        };
        localStorage.setItem(prefixKey(key), JSON.stringify(item));
        return true;
      } catch {
        // Evict oldest items
        evictOldest(1);
        return false;
      }
    }
    return false;
  }
}

/**
 * Remove item from localStorage
 */
export function storageRemove(key: string): void {
  localStorage.removeItem(prefixKey(key));
}

/**
 * Check if key exists and is not expired
 */
export function storageHas(key: string): boolean {
  return storageGet(key) !== null;
}

/**
 * Get all keys managed by this namespace
 */
export function storageKeys(): string[] {
  const prefix = `${NAMESPACE}${SEPARATOR}`;
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      keys.push(k.slice(prefix.length));
    }
  }
  return keys;
}

/**
 * Clear all items in namespace
 */
export function storageClear(): void {
  const prefix = `${NAMESPACE}${SEPARATOR}`;
  const keysToRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      keysToRemove.push(k);
    }
  }
  keysToRemove.forEach((k) => localStorage.removeItem(k));
}

/**
 * Get estimated storage usage in bytes
 */
export function storageUsage(): { usedBytes: number; maxBytes: number; percentage: number } {
  let usedBytes = 0;
  const prefix = `${NAMESPACE}${SEPARATOR}`;
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      const v = localStorage.getItem(k);
      if (v) {
        usedBytes += (k.length + v.length) * 2; // UTF-16
      }
    }
  }
  const maxBytes = MAX_STORAGE_MB * 1024 * 1024;
  return { usedBytes, maxBytes, percentage: (usedBytes / maxBytes) * 100 };
}

/**
 * Remove expired items
 */
export function evictExpired(): number {
  let evicted = 0;
  const prefix = `${NAMESPACE}${SEPARATOR}`;
  const now = Date.now();
  const keysToRemove: string[] = [];

  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      try {
        const raw = localStorage.getItem(k);
        if (raw) {
          const item = JSON.parse(raw);
          if (item.expiresAt && now > item.expiresAt) {
            keysToRemove.push(k);
          }
        }
      } catch {
        // Skip malformed items
      }
    }
  }

  keysToRemove.forEach((k) => {
    localStorage.removeItem(k);
    evicted++;
  });

  return evicted;
}

/**
 * Evict oldest N items by createdAt
 */
function evictOldest(count: number): void {
  const prefix = `${NAMESPACE}${SEPARATOR}`;
  const items: Array<{ key: string; createdAt: number }> = [];

  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      try {
        const raw = localStorage.getItem(k);
        if (raw) {
          const item = JSON.parse(raw);
          items.push({ key: k, createdAt: item.createdAt || 0 });
        }
      } catch {
        items.push({ key: k, createdAt: 0 });
      }
    }
  }

  items.sort((a, b) => a.createdAt - b.createdAt);
  items.slice(0, count).forEach(({ key }) => localStorage.removeItem(key));
}
