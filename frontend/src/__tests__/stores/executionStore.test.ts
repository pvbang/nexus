import { useExecutionStore } from '@/stores/executionStore'
import type { ExecutionEvent } from '@/types/execution'

function makeEvent(overrides: Partial<ExecutionEvent> & { type: string }): ExecutionEvent {
  return {
    id: `evt-${Math.random().toString(36).slice(2)}`,
    timestamp: Date.now(),
    ...overrides,
  } as ExecutionEvent
}

describe('executionStore', () => {
  beforeEach(() => {
    // Reset store state between tests
    const store = useExecutionStore.getState()
    const sessions = Object.keys(store.eventsBySession)
    for (const sid of sessions) {
      store.clearSession(sid)
    }
  })

  describe('setEvents', () => {
    it('stores events for a session', () => {
      const store = useExecutionStore.getState()
      const events = [
        makeEvent({ type: 'step', step: 1, sequence: 1, task_id: 't1' } as any),
        makeEvent({ type: 'step', step: 2, sequence: 2, task_id: 't1' } as any),
      ]

      store.setEvents('session-1', events)

      const stored = store.getEvents('session-1')
      expect(stored).toHaveLength(2)
    })

    it('sorts events by sequence', () => {
      const store = useExecutionStore.getState()
      const events = [
        makeEvent({ type: 'agent_spawn', sequence: 3, task_id: 't1', agent_name: 'a', agent_role: 'r' } as any),
        makeEvent({ type: 'step', sequence: 1, task_id: 't1' } as any),
        makeEvent({ type: 'tool_start', sequence: 2, task_id: 't1', tool: 'x', agent_name: 'a' } as any),
      ]

      store.setEvents('session-1', events)

      const stored = store.getEvents('session-1')
      expect((stored[0] as any).sequence).toBe(1)
      expect((stored[1] as any).sequence).toBe(2)
      expect((stored[2] as any).sequence).toBe(3)
    })

    it('rebuilds sequence index (maxSequence)', () => {
      const store = useExecutionStore.getState()
      const events = [
        makeEvent({ type: 'step', sequence: 5, task_id: 't1' } as any),
        makeEvent({ type: 'step', sequence: 10, task_id: 't1' } as any),
        makeEvent({ type: 'step', sequence: 3, task_id: 't1' } as any),
      ]

      store.setEvents('session-1', events)

      expect(store.getMaxSequence('session-1')).toBe(10)
    })

    it('merges tool_done into tool_start', () => {
      const store = useExecutionStore.getState()
      const events = [
        makeEvent({
          type: 'tool_start',
          sequence: 1,
          task_id: 't1',
          tool: 'web_search',
          agent_name: 'researcher',
        } as any),
        makeEvent({
          type: 'tool_done',
          sequence: 2,
          task_id: 't1',
          tool: 'web_search',
          agent_name: 'researcher',
          output: 'search results',
          success: true,
        } as any),
      ]

      store.setEvents('session-1', events)

      const stored = store.getEvents('session-1')
      expect(stored).toHaveLength(1)
      expect(stored[0].type).toBe('tool_start')
      expect((stored[0] as any)._merged).toBe(true)
      expect((stored[0] as any).output).toBe('search results')
      expect((stored[0] as any).success).toBe(true)
    })
  })

  describe('batchAddEvents deduplication', () => {
    it('deduplicates events with same sequence and task_id', () => {
      const store = useExecutionStore.getState()

      const event1 = makeEvent({ type: 'step', sequence: 1, task_id: 't1' } as any)
      const event2 = makeEvent({ type: 'step', sequence: 1, task_id: 't1' } as any)

      store.batchAddEvents('session-dup', [event1, event2])

      const stored = store.getEvents('session-dup')
      expect(stored).toHaveLength(1)
    })

    it('allows events with different task_ids but same sequence', () => {
      const store = useExecutionStore.getState()

      const event1 = makeEvent({ type: 'agent_spawn', sequence: 1, task_id: 't1', agent_name: 'agent-a', agent_role: 'coder' } as any)
      const event2 = makeEvent({ type: 'agent_spawn', sequence: 1, task_id: 't2', agent_name: 'agent-b', agent_role: 'reviewer' } as any)

      store.batchAddEvents('session-multi', [event1, event2])

      const stored = store.getEvents('session-multi')
      expect(stored).toHaveLength(2)
    })

    it('updates step event in-place for same step number', () => {
      const store = useExecutionStore.getState()

      const step1 = makeEvent({
        type: 'step',
        step: 1,
        title: 'Planning',
        status: 'active',
      } as any)
      const step1Updated = makeEvent({
        type: 'step',
        step: 1,
        title: 'Planning',
        status: 'done',
      } as any)

      store.batchAddEvents('session-step', [step1])
      store.batchAddEvents('session-step', [step1Updated])

      const stored = store.getEvents('session-step')
      expect(stored).toHaveLength(1)
      expect((stored[0] as any).status).toBe('done')
    })
  })

  describe('clearSession', () => {
    it('removes all events for a session', () => {
      const store = useExecutionStore.getState()
      store.setEvents('session-clear', [
        makeEvent({ type: 'step', sequence: 1, task_id: 't1' } as any),
      ])

      store.clearSession('session-clear')

      expect(store.getEvents('session-clear')).toHaveLength(0)
    })

    it('removes active status', () => {
      const store = useExecutionStore.getState()
      store.setActive('session-clear2', true)
      store.clearSession('session-clear2')

      const state = useExecutionStore.getState()
      expect(state.activeBySession['session-clear2']).toBeUndefined()
    })

    it('resets max sequence', () => {
      const store = useExecutionStore.getState()
      store.setEvents('session-clear3', [
        makeEvent({ type: 'step', sequence: 5, task_id: 't1' } as any),
      ])

      store.clearSession('session-clear3')

      expect(store.getMaxSequence('session-clear3')).toBe(0)
    })

    it('does not affect other sessions', () => {
      const store = useExecutionStore.getState()
      store.setEvents('session-a', [
        makeEvent({ type: 'step', sequence: 1, task_id: 't1' } as any),
      ])
      store.setEvents('session-b', [
        makeEvent({ type: 'step', sequence: 2, task_id: 't2' } as any),
      ])

      store.clearSession('session-a')

      expect(store.getEvents('session-a')).toHaveLength(0)
      expect(store.getEvents('session-b')).toHaveLength(1)
    })
  })
})
