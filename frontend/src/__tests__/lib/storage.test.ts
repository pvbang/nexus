// Mock localStorage for node environment
function createLocalStorageMock() {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} },
    get length() { return Object.keys(store).length },
    key: (index: number) => Object.keys(store)[index] ?? null,
  }
}

const localStorageMock = createLocalStorageMock()
Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock, writable: true })

import { storageGet, storageSet, storageRemove, storageClear, storageKeys, storageHas } from '@/lib/storage'

describe('storage', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('storageSet / storageGet', () => {
    it('stores and retrieves a string value', () => {
      storageSet('name', 'hello')
      expect(storageGet<string>('name')).toBe('hello')
    })

    it('stores and retrieves an object value', () => {
      const obj = { a: 1, b: 'two' }
      storageSet('config', obj)
      expect(storageGet('config')).toEqual(obj)
    })

    it('stores and retrieves a number', () => {
      storageSet('count', 42)
      expect(storageGet<number>('count')).toBe(42)
    })

    it('returns null for non-existent key', () => {
      expect(storageGet('nonexistent')).toBeNull()
    })

    it('returns default value for non-existent key', () => {
      expect(storageGet('nonexistent', 'default')).toBe('default')
    })

    it('returns true on successful set', () => {
      expect(storageSet('key', 'value')).toBe(true)
    })
  })

  describe('TTL expiry', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('returns value before TTL expires', () => {
      storageSet('temp', 'data', 5000)
      vi.advanceTimersByTime(3000)
      expect(storageGet<string>('temp')).toBe('data')
    })

    it('returns null after TTL expires', () => {
      storageSet('temp', 'data', 5000)
      vi.advanceTimersByTime(6000)
      expect(storageGet('temp')).toBeNull()
    })

    it('returns default value after TTL expires', () => {
      storageSet('temp', 'data', 1000)
      vi.advanceTimersByTime(2000)
      expect(storageGet('temp', 'fallback')).toBe('fallback')
    })

    it('removes expired key from localStorage on access', () => {
      storageSet('temp', 'data', 1000)
      vi.advanceTimersByTime(2000)
      storageGet('temp')
      const rawKey = 'nexus:temp'
      expect(localStorage.getItem(rawKey)).toBeNull()
    })
  })

  describe('namespace isolation', () => {
    it('prefixes keys with namespace', () => {
      storageSet('mykey', 'val')
      expect(localStorage.getItem('nexus:mykey')).not.toBeNull()
    })

    it('does not interfere with non-namespaced keys', () => {
      localStorage.setItem('external_key', 'external_value')
      storageSet('mykey', 'val')

      const keys = storageKeys()
      expect(keys).toContain('mykey')
      expect(keys).not.toContain('external_key')
    })

    it('storageClear only removes namespaced keys', () => {
      localStorage.setItem('external_key', 'external_value')
      storageSet('key1', 'v1')
      storageSet('key2', 'v2')

      storageClear()

      expect(localStorage.getItem('external_key')).toBe('external_value')
      expect(storageGet('key1')).toBeNull()
      expect(storageGet('key2')).toBeNull()
    })
  })

  describe('storageRemove', () => {
    it('removes a stored item', () => {
      storageSet('toRemove', 'data')
      storageRemove('toRemove')
      expect(storageGet('toRemove')).toBeNull()
    })
  })

  describe('storageHas', () => {
    it('returns true for existing key', () => {
      storageSet('exists', 'yes')
      expect(storageHas('exists')).toBe(true)
    })

    it('returns false for non-existent key', () => {
      expect(storageHas('nope')).toBe(false)
    })
  })

  describe('storageKeys', () => {
    it('lists all namespaced keys', () => {
      storageSet('a', 1)
      storageSet('b', 2)
      storageSet('c', 3)

      const keys = storageKeys()
      expect(keys).toContain('a')
      expect(keys).toContain('b')
      expect(keys).toContain('c')
      expect(keys).toHaveLength(3)
    })
  })
})
