// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { makeWsDoorbellHandler } from '../../components/chat/wsDoorbell'

describe('makeWsDoorbellHandler', () => {
  it('never pushes events itself — only wakes polling for a generic event', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const onTerminalHint = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted, onTerminalHint })

    handler({ type: 'event', event_type: 'tool_start', data: {} })

    expect(onWake).toHaveBeenCalledTimes(1)
    expect(onTaskStarted).not.toHaveBeenCalled()
    expect(onTerminalHint).not.toHaveBeenCalled()
  })

  it('ignores heartbeat/pong entirely', () => {
    const onWake = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake })

    handler({ type: 'heartbeat' })
    handler({ type: 'pong' })

    expect(onWake).not.toHaveBeenCalled()
  })

  it('signals task_started with task id and wakes polling', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'task_started', task_id: 't1' })

    expect(onTaskStarted).toHaveBeenCalledWith('t1')
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('handles session_event wrapper for task_started', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'session_event', event: 'task_started', task_id: 't2' })

    expect(onTaskStarted).toHaveBeenCalledWith('t2')
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('does not fire onTaskStarted when task_started has no id, but still wakes', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'task_started' })

    expect(onTaskStarted).not.toHaveBeenCalled()
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('signals terminal hint on stream_end/complete/error and wakes', () => {
    const onWake = vi.fn()
    const onTerminalHint = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTerminalHint })

    handler({ type: 'stream_end', full_content: 'x' })
    handler({ type: 'complete' })
    handler({ type: 'error' })

    expect(onTerminalHint).toHaveBeenCalledTimes(3)
    expect(onWake).toHaveBeenCalledTimes(3)
  })

  // MULTI-TAB FIX: A passive tab (one that did NOT send the message) opens onto a
  // session whose task is already running. The backend announces the AUTHORITATIVE
  // running task_id via session_state (on connect) / subscribed (after subscribe).
  // The doorbell must lock polling onto THAT task — never a previous turn's task
  // inferred from local history (which surfaced a false "Task failed" + resume).
  it('locks onto the running task from session_state and wakes', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'session_state', status: 'running', task_id: 't3' })

    expect(onTaskStarted).toHaveBeenCalledWith('t3')
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('locks onto the running task from a subscribed ack and wakes', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'subscribed', status: 'running', task_id: 't3' })

    expect(onTaskStarted).toHaveBeenCalledWith('t3')
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('does NOT lock onto a non-running session_state (idle/completed) — only wakes', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'session_state', status: 'idle', task_id: null })
    handler({ type: 'session_state', status: 'completed', task_id: 't-old' })

    expect(onTaskStarted).not.toHaveBeenCalled()
    expect(onWake).toHaveBeenCalledTimes(2)
  })

  it('does not fire onTaskStarted when a running session_state lacks a task id, but still wakes', () => {
    const onWake = vi.fn()
    const onTaskStarted = vi.fn()
    const handler = makeWsDoorbellHandler({ sessionId: 's1', onWake, onTaskStarted })

    handler({ type: 'session_state', status: 'running' })

    expect(onTaskStarted).not.toHaveBeenCalled()
    expect(onWake).toHaveBeenCalledTimes(1)
  })
})
