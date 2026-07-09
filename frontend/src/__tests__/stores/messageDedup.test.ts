// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { mergeMessages } from '../../stores/sessionStore'
import type { ChatMessage } from '../../api'

describe('mergeMessages', () => {
  it('drops local-only message when DB has same (role, content) despite different timestamp', () => {
    const local: ChatMessage = { id: 'local-1', role: 'user', content: 'hi', timestamp: 'T1' }
    const db: ChatMessage = { id: 'uuid-1', role: 'user', content: 'hi', timestamp: 'T2' }
    const merged = mergeMessages([db], [local])
    expect(merged.length).toBe(1)
    expect(merged[0].id).toBe('uuid-1')
  })

  it('keeps both valid DB messages with identical content and drops the local echo', () => {
    const u1: ChatMessage = { id: 'uuid-1', role: 'user', content: 'hi', timestamp: 'T1' }
    const u2: ChatMessage = { id: 'uuid-2', role: 'user', content: 'hi', timestamp: 'T2' }
    const local: ChatMessage = { id: 'local-1', role: 'user', content: 'hi', timestamp: 'T3' }
    const merged = mergeMessages([u1, u2], [local])
    expect(merged.length).toBe(2)
    expect(merged.map((m) => m.id)).toEqual(['uuid-1', 'uuid-2'])
  })

  it('keeps a local-only message whose content does not exist in DB', () => {
    const db: ChatMessage = { id: 'uuid-1', role: 'user', content: 'hi', timestamp: 'T1' }
    const local: ChatMessage = { id: 'local-1', role: 'user', content: 'totally different', timestamp: 'T2' }
    const merged = mergeMessages([db], [local])
    expect(merged.length).toBe(2)
    expect(merged.map((m) => m.id)).toEqual(['uuid-1', 'local-1'])
  })
})
