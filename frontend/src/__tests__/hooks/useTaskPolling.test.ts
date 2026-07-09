// @vitest-environment jsdom
import { dedupBySeqAndTask } from '@/hooks/useTaskPolling'
import type { TaskEvent } from '@/hooks/useTaskPolling'

function makeEvent(overrides: Partial<TaskEvent> & { task_id: string; sequence: number }): TaskEvent {
  return {
    id: `evt-${overrides.task_id}-${overrides.sequence}`,
    session_id: 'session-1',
    event_type: 'step',
    data: {},
    created_at: null,
    ...overrides,
  }
}

describe('dedupBySeqAndTask', () => {
  it('keeps incoming events whose (task_id, sequence) pair is not in prev', () => {
    const prev = [makeEvent({ task_id: 'A', sequence: 1 })]
    const incoming = [
      makeEvent({ task_id: 'A', sequence: 1 }), // duplicate of prev -> drop
      makeEvent({ task_id: 'B', sequence: 1 }), // different task, same seq -> keep
    ]

    const result = dedupBySeqAndTask(prev, incoming)

    expect(result).toHaveLength(1)
    expect(result[0].task_id).toBe('B')
    expect(result[0].sequence).toBe(1)
  })

  it('dedups duplicates within incoming itself', () => {
    const prev: TaskEvent[] = []
    const incoming = [
      makeEvent({ task_id: 'A', sequence: 1 }),
      makeEvent({ task_id: 'A', sequence: 1 }),
    ]

    const result = dedupBySeqAndTask(prev, incoming)

    expect(result).toHaveLength(1)
    expect(result[0].task_id).toBe('A')
    expect(result[0].sequence).toBe(1)
  })

  it('returns all incoming when none overlap prev or each other', () => {
    const prev = [makeEvent({ task_id: 'A', sequence: 1 })]
    const incoming = [
      makeEvent({ task_id: 'A', sequence: 2 }),
      makeEvent({ task_id: 'B', sequence: 1 }),
    ]

    const result = dedupBySeqAndTask(prev, incoming)

    expect(result).toHaveLength(2)
  })

  it('returns empty when all incoming already in prev', () => {
    const prev = [
      makeEvent({ task_id: 'A', sequence: 1 }),
      makeEvent({ task_id: 'A', sequence: 2 }),
    ]
    const incoming = [
      makeEvent({ task_id: 'A', sequence: 1 }),
      makeEvent({ task_id: 'A', sequence: 2 }),
    ]

    const result = dedupBySeqAndTask(prev, incoming)

    expect(result).toHaveLength(0)
  })
})
