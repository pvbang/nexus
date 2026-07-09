// @vitest-environment jsdom
import {
  persistTaskMapping,
  getPersistedTaskId,
  persistStage,
  getPersistedStage,
  persistTaskStage,
  getStageByTask,
} from '@/stores/process/persistence'

const LS_KEY = 'mekongai_task_state'

describe('process persistence (keyed by taskId)', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('per-task stage isolation (root bug)', () => {
    it('does not cross-overwrite stage between two tasks of the same session', () => {
      persistTaskStage('taskA', 'sess1', 'executing')
      persistTaskStage('taskB', 'sess1', 'planning')

      expect(getStageByTask('taskA')).toBe('executing')
      expect(getStageByTask('taskB')).toBe('planning')
    })

    it('updating one task stage leaves the other untouched', () => {
      persistTaskStage('taskA', 'sess1', 'planning')
      persistTaskStage('taskB', 'sess1', 'planning')
      persistTaskStage('taskA', 'sess1', 'completed')

      expect(getStageByTask('taskA')).toBe('completed')
      expect(getStageByTask('taskB')).toBe('planning')
    })

    it('returns null for unknown taskId', () => {
      expect(getStageByTask('nope')).toBeNull()
    })
  })

  describe('legacy API compatibility', () => {
    it('persistTaskMapping + getPersistedTaskId round-trips', () => {
      persistTaskMapping('sess1', 'taskA')
      expect(getPersistedTaskId('sess1')).toBe('taskA')
    })

    it('getPersistedTaskId returns the latest task for a session', () => {
      persistTaskMapping('sess1', 'taskA')
      persistTaskMapping('sess1', 'taskB')
      expect(getPersistedTaskId('sess1')).toBe('taskB')
    })

    it('persistStage updates the latest task of the session and getPersistedStage reads it', () => {
      persistTaskMapping('sess1', 'taskA')
      persistStage('sess1', 'executing')
      expect(getPersistedStage('sess1')).toBe('executing')
      // stage is bound to the task, not the session-key — taskA carries it
      expect(getStageByTask('taskA')).toBe('executing')
    })

    it('persistStage on multi-task session only affects current (latest) task', () => {
      persistTaskMapping('sess1', 'taskA')
      persistStage('sess1', 'executing')
      persistTaskMapping('sess1', 'taskB')
      persistStage('sess1', 'synthesizing')

      expect(getStageByTask('taskA')).toBe('executing')
      expect(getStageByTask('taskB')).toBe('synthesizing')
      // session-level latest reflects taskB
      expect(getPersistedStage('sess1')).toBe('synthesizing')
    })

    it('getPersistedTaskId / getPersistedStage return null for unknown session', () => {
      expect(getPersistedTaskId('ghost')).toBeNull()
      expect(getPersistedStage('ghost')).toBeNull()
    })

    it('ignores empty sessionId / taskId', () => {
      persistTaskMapping('', 'taskA')
      persistTaskMapping('sess1', '')
      expect(localStorage.getItem(LS_KEY)).toBeNull()
    })
  })

  describe('cap enforcement', () => {
    it('keeps at most 50 entries, evicting oldest by updatedAt', () => {
      for (let i = 0; i < 60; i++) {
        persistTaskStage(`task-${i}`, `sess-${i}`, 'planning')
      }
      const raw = localStorage.getItem(LS_KEY)
      expect(raw).not.toBeNull()
      const map = JSON.parse(raw as string)
      expect(Object.keys(map).length).toBeLessThanOrEqual(50)
      // oldest evicted, newest kept
      expect(getStageByTask('task-0')).toBeNull()
      expect(getStageByTask('task-59')).toBe('planning')
    })
  })
})
