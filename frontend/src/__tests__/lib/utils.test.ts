import { formatDate, formatRelativeTime, formatSize, truncate, generateId, debounce } from '@/lib/utils'

describe('formatDate', () => {
  it('formats a Date object', () => {
    const date = new Date('2026-01-15T15:45:00Z')
    const result = formatDate(date)
    expect(result).toContain('Jan')
    expect(result).toContain('15')
    expect(result).toContain('2026')
  })

  it('formats a date string', () => {
    const result = formatDate('2026-06-01T10:30:00Z')
    expect(result).toContain('Jun')
    expect(result).toContain('1')
    expect(result).toContain('2026')
  })
})

describe('formatRelativeTime', () => {
  it('returns relative time string with suffix', () => {
    const recent = new Date(Date.now() - 60_000) // 1 minute ago
    const result = formatRelativeTime(recent)
    expect(result).toContain('ago')
  })

  it('accepts string input', () => {
    const recent = new Date(Date.now() - 3600_000).toISOString() // 1 hour ago
    const result = formatRelativeTime(recent)
    expect(result).toContain('ago')
  })
})

describe('formatSize', () => {
  it('returns "0 B" for zero bytes', () => {
    expect(formatSize(0)).toBe('0 B')
  })

  it('formats bytes', () => {
    expect(formatSize(500)).toBe('500 B')
  })

  it('formats kilobytes', () => {
    expect(formatSize(1024)).toBe('1 KB')
    expect(formatSize(1536)).toBe('1.5 KB')
  })

  it('formats megabytes', () => {
    expect(formatSize(1048576)).toBe('1 MB')
    expect(formatSize(3670016)).toBe('3.5 MB')
  })

  it('formats gigabytes', () => {
    expect(formatSize(1073741824)).toBe('1 GB')
  })
})

describe('truncate', () => {
  it('returns original string if within limit', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })

  it('returns original string if exactly at limit', () => {
    expect(truncate('hello', 5)).toBe('hello')
  })

  it('truncates and appends ellipsis', () => {
    expect(truncate('hello world', 5)).toBe('hello...')
  })

  it('handles empty string', () => {
    expect(truncate('', 5)).toBe('')
  })
})

describe('generateId', () => {
  it('returns a string', () => {
    const id = generateId()
    expect(typeof id).toBe('string')
  })

  it('returns unique values', () => {
    const id1 = generateId()
    const id2 = generateId()
    expect(id1).not.toBe(id2)
  })

  it('returns UUID format', () => {
    const id = generateId()
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
})

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('delays function execution', () => {
    const fn = vi.fn()
    const debounced = debounce(fn, 100)

    debounced()
    expect(fn).not.toHaveBeenCalled()

    vi.advanceTimersByTime(100)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('resets timer on subsequent calls', () => {
    const fn = vi.fn()
    const debounced = debounce(fn, 100)

    debounced()
    vi.advanceTimersByTime(50)
    debounced()
    vi.advanceTimersByTime(50)

    expect(fn).not.toHaveBeenCalled()

    vi.advanceTimersByTime(50)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('passes arguments to the debounced function', () => {
    const fn = vi.fn()
    const debounced = debounce(fn, 100)

    debounced('a', 'b')
    vi.advanceTimersByTime(100)

    expect(fn).toHaveBeenCalledWith('a', 'b')
  })

  it('uses latest arguments when called multiple times', () => {
    const fn = vi.fn()
    const debounced = debounce(fn, 100)

    debounced('first')
    debounced('second')
    debounced('third')
    vi.advanceTimersByTime(100)

    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('third')
  })
})
