// ============================================================
// Nexus - Execution Trace Store (Zustand)
// Persists execution events per session across navigation
// ============================================================
// Key design: when a tool_done event arrives, we merge its data
// into the matching tool_start event instead of adding a separate
// entry. This ensures the spinner stops and output is displayed
// on the original tool card.
//
// ============================================================

import { create } from 'zustand'
import type { ExecutionEvent, ToolEvent } from '../types/execution'

// ── Throttled event queue with ADAPTIVE flush interval ────────
// Incoming events are buffered here and flushed into the store
// at most once per flush interval.
//

const BASE_FLUSH_MS = 200    // Normal: flush every 200ms
const HIGH_FLUSH_MS = 400
// 1000ms caused execution timeline to appear 1s behind real-time.
const MAX_FLUSH_MS = 600

interface QueueEntry {
  sessionId: string
  event: ExecutionEvent
}

let eventQueue: QueueEntry[] = []
let flushTimer: ReturnType<typeof setTimeout> | null = null

/** PHASE-B: Compute adaptive flush interval based on queue pressure */
function getAdaptiveFlushMs(): number {
  const queueSize = eventQueue.length
  if (queueSize > 100) return MAX_FLUSH_MS
  if (queueSize > 50) return HIGH_FLUSH_MS
  return BASE_FLUSH_MS
}

function scheduleFlush() {
  if (flushTimer !== null) return // already scheduled
  const flushMs = getAdaptiveFlushMs()
  flushTimer = setTimeout(() => {
    flushTimer = null
    const batch = eventQueue
    eventQueue = []
    if (batch.length === 0) return

    // Group by sessionId and apply all at once
    const grouped = new Map<string, ExecutionEvent[]>()
    for (const { sessionId, event } of batch) {
      let list = grouped.get(sessionId)
      if (!list) {
        list = []
        grouped.set(sessionId, list)
      }
      list.push(event)
    }

    // PERF-FLUSH: Single set() per flush batch instead of N set() calls.
    // Previously: loop _addEventDirect per-event → N Zustand subscriber re-renders.
    // Now: collect all mutations → 1 set() call → 1 re-render per flush cycle.
    useExecutionStore.setState((state) => {
      let newEventsBySession = state.eventsBySession
      let newMaxSeq = state.maxSequenceBySession
      let changed = false

      for (const [sid, events] of grouped) {
        let existing = newEventsBySession[sid] || []
        const isInitialized = sid in newEventsBySession || state.activeBySession[sid]

        for (const event of events) {
          // Gate check: same logic as _addEventDirect for uninitialized sessions
          if (!isInitialized && existing.length === 0) {
            const evtType = (event as unknown as Record<string, unknown>).type
            const AUTO_INIT_TYPES = new Set([
              'task_started', 'step', 'tool_start', 'tool_done', 'agent_spawn',
              'planning_start', 'planning_done', 'react_phase', 'deep_analysis',
              'deep_analysis_complete', 'agent_thinking', 'self_reflection',
              'auto_verify', 'completion_check', 'debate_proposal', 'debate_critique',
              'debate_revision', 'debate_rebuttal', 'debate_verdict',
              'xskill_retrieve', 'xskill_accumulate', 'mid_analysis',
              'system_warning', 'task_crashed', 'resource_alert', 'budget_warning',
              'swarm_round', 'swarm_round_start', 'agent_response',
              'debate_message', 'debate_started', 'debate_concluded', 'moderator_intervention',
              'debate_turn', 'debate_reflection', 'debate_gate_check',
              'debate_tool_call', 'debate_tool_result', 'debate_agent_plan',
              'debate_phase', 'debate_action_phase',
              'preparation_started', 'preparation_plan', 'preparation_complete',
              'preparation_research', 'synthesis_start', 'synthesis_done',
              'report_building', 'action_tool_call', 'action_tool_result',
              'swarm_activated', 'task_heartbeat',
              'context_compaction', 'context_trimmed', 'debate_error',
              'evaluation_done', 'agent_stuck', 'tool_error',
              'document_created', 'next_turn_plan', 'evolution_insight',
              // Orchestrator decision/insight events surfaced on the timeline
              'metacognitive_insight', 'authorization_required',
              'divergent_thinking_done', 'workspace_rollback',
              'infrastructure_backoff', 'agent_failed', 'agent_force_stopped',
            ])
            if (!AUTO_INIT_TYPES.has(evtType as string)) {
              continue // Skip this event — session not active + event type not auto-init
            }
          }

          const updated = applyEventToList(existing, event, sid)
          if (updated !== existing) {
            existing = updated
            changed = true
          }
        }

        if (changed || existing !== (newEventsBySession[sid] || [])) {
          if (newEventsBySession === state.eventsBySession) {
            newEventsBySession = { ...state.eventsBySession }
          }
          newEventsBySession[sid] = existing

          // Update max sequence for this session
          let currentMax = newMaxSeq[sid] ?? 0
          for (const event of events) {
            const evtSeq = (event as unknown as Record<string, unknown>).sequence as number | undefined
            if (typeof evtSeq === 'number' && evtSeq > currentMax) currentMax = evtSeq
          }
          if (currentMax > (state.maxSequenceBySession[sid] ?? 0)) {
            if (newMaxSeq === state.maxSequenceBySession) {
              newMaxSeq = { ...state.maxSequenceBySession }
            }
            newMaxSeq[sid] = currentMax
          }
        }
      }

      if (!changed) return state // No mutations — skip re-render entirely
      return {
        eventsBySession: newEventsBySession,
        maxSequenceBySession: newMaxSeq,
      }
    })
  }, flushMs)
}

/**
 * Try to find a matching tool_start event for a tool_done event.
 * Match by: same tool name + same agent_name, not yet completed (no output).
 * Search in reverse so we match the most recent pending start.
 */
function findMatchingToolStart(
  events: ExecutionEvent[],
  doneEvent: ToolEvent,
): number {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]
    if (
      e.type === 'tool_start' &&
      (e as ToolEvent).tool === doneEvent.tool &&
      (e as ToolEvent).agent_name === doneEvent.agent_name &&
      (e as ToolEvent)._merged !== true // not already merged with a done
    ) {
      return i
    }
  }
  return -1
}

/**
 * Post-process a list of events to merge any orphaned tool_done
 * events into their matching tool_start events (for DB replay).
 */
function mergeToolPairs(events: ExecutionEvent[]): ExecutionEvent[] {
  const result = [...events]
  const doneIndices = new Set<number>()

  // For each tool_done, find matching tool_start and merge
  for (let i = 0; i < result.length; i++) {
    const evt = result[i]
    if (evt.type !== 'tool_done') continue
    const done = evt as ToolEvent

    // Search backwards from this index for a matching unmerged tool_start
    let startIdx = -1
    for (let j = i - 1; j >= 0; j--) {
      const candidate = result[j]
      if (
        candidate.type === 'tool_start' &&
        (candidate as ToolEvent).tool === done.tool &&
        (candidate as ToolEvent).agent_name === done.agent_name &&
        (candidate as ToolEvent)._merged !== true
      ) {
        startIdx = j
        break
      }
    }

    if (startIdx >= 0) {
      // Merge done data into start event
      const startEvt = result[startIdx] as ToolEvent
      result[startIdx] = {
        ...startEvt,
        output: done.output ?? startEvt.output,
        success: done.success ?? startEvt.success,
        execution_time_ms: done.execution_time_ms ?? startEvt.execution_time_ms,
        params: startEvt.params || done.params,
        _merged: true,
      }
      doneIndices.add(i) // mark done event for removal
    }
  }

  // Remove merged tool_done events (they are now inside their tool_start)
  return result.filter((_, idx) => !doneIndices.has(idx))
}

const _wsActiveSessions: Set<string> = new Set()
/**
 * BUG-L1 FIX: Documented dangerous behavior. Prefer removeWsActiveSession(id) for cleanup.
 * @param sessionId - Session to mark as active. Pass null to clear ALL (use with caution).
 * @deprecated for null usage. Use removeWsActiveSession(sessionId) instead for targeted cleanup.
 */
export function setWsActiveSession(sessionId: string | null) {
  if (sessionId) {
    _wsActiveSessions.add(sessionId)
  }
  // When null: do nothing. Use removeWsActiveSession() to remove specific sessions.
  // REMOVED: _wsActiveSessions.clear() — this is unsafe for multi-tab
}
/** Remove a specific session from active set (multi-tab safe) */
export function removeWsActiveSession(sessionId: string) {
  _wsActiveSessions.delete(sessionId)
}
export function getWsActiveSession(): string | null {
  // Backward compat: return first entry (for code that checks !== null)
  const first = _wsActiveSessions.values().next().value
  return first ?? null
}
/** BUG-H2 FIX: Check if a specific session has active WS delivery */
export function isWsActiveForSession(sessionId: string): boolean {
  return _wsActiveSessions.has(sessionId)
}

// Between Chat WS starting to connect and onopen firing, Monitor WS should not
// route events for this session (they'll arrive via Chat WS momentarily).
const _wsConnectingSessions: Set<string> = new Set()
// FIX: BUG-RT09 - Don't allow clearing all sessions at once (unsafe for multi-tab)
export function setWsConnectingSession(sessionId: string | null) {
  if (sessionId) {
    _wsConnectingSessions.add(sessionId)
  }
  // REMOVED: _wsConnectingSessions.clear() — this is unsafe for multi-tab.
  // Use removeWsConnectingSession(sessionId) for targeted cleanup instead.
}
export function removeWsConnectingSession(sessionId: string) {
  _wsConnectingSessions.delete(sessionId)
}
export function getWsConnectingSession(): string | null {
  const first = _wsConnectingSessions.values().next().value
  return first ?? null
}
/** BUG-H2 FIX: Check if a specific session is in connecting state */
export function isWsConnectingForSession(sessionId: string): boolean {
  return _wsConnectingSessions.has(sessionId)
}

// ── PERF: Map-based dedup index for O(1) sequence lookups ─────────────
// Instead of scanning the entire events array on every new event,
// we maintain a per-session Set of seen sequence keys (taskId_sequence).
// This avoids the O(n) `.some()` scan for the most common dedup path.
const _seqIndexBySession = new Map<string, Set<string>>()

/** Get or create the sequence index for a session */
function getSeqIndex(sessionId: string): Set<string> {
  let idx = _seqIndexBySession.get(sessionId)
  if (!idx) {
    idx = new Set()
    _seqIndexBySession.set(sessionId, idx)
  }
  return idx
}

/** Build a sequence dedup key from event data */
function seqKey(taskId: string, sequence: number): string {
  return `${taskId}_${sequence}`
}

interface ExecutionState {
  /** Execution events per session: sessionId → events */
  eventsBySession: Record<string, ExecutionEvent[]>
  /** Whether a session has an active (running) execution */
  activeBySession: Record<string, boolean>
  /** DEDUP: Highest sequence number seen per session (for incremental fetch) */
  maxSequenceBySession: Record<string, number>

  // Actions
  /** Queue an event for throttled batch update (UI-friendly) */
  addEvent: (sessionId: string, event: ExecutionEvent) => void
  /** Batch-add multiple events in a single state update (avoids N re-renders) */
  batchAddEvents: (sessionId: string, events: ExecutionEvent[]) => void
  /** Directly add event to store without throttling (used by flush) */
  _addEventDirect: (sessionId: string, event: ExecutionEvent) => void
  setEvents: (sessionId: string, events: ExecutionEvent[]) => void
  clearSession: (sessionId: string) => void
  setActive: (sessionId: string, active: boolean) => void
  getEvents: (sessionId: string) => ExecutionEvent[]
  getMaxSequence: (sessionId: string) => number
}

/** PERF-05: Max events per session to prevent unbounded growth + re-render overhead.
 *  AUDIT-M2 FIX: Increased from 500 → 1000 to accommodate complex swarm debates.
 *  HIGH-06 FIX: Increased from 1000 → 2000. Complex debates (8 agents × 35 turns
 *  × 3-5 tools each) generate 1500-2500 events. At 1000, early critical events
 *  (planning, agent_spawn, deep_analysis) were being sliced off, losing context
 *  for users scrolling back through execution history. */
const MAX_EVENTS_PER_SESSION = 2000

/** Internal logic for adding a single event (merge/dedup) */
function applyEventToList(existing: ExecutionEvent[], event: ExecutionEvent, sessionId?: string): ExecutionEvent[] {
  // For step events, update existing step instead of adding duplicate
  if (event.type === 'step') {
    const stepEvent = event as { step: number }
    const idx = existing.findIndex(
      (e) => e.type === 'step' && (e as { step: number }).step === stepEvent.step
    )
    if (idx >= 0) {
      const updated = [...existing]
      updated[idx] = event
      return updated
    }
  }

  // For tool_done events, merge into matching tool_start instead of adding
  if (event.type === 'tool_done') {
    const doneEvent = event as ToolEvent
    const startIdx = findMatchingToolStart(existing, doneEvent)
    if (startIdx >= 0) {
      const updated = [...existing]
      const startEvt = updated[startIdx] as ToolEvent
      updated[startIdx] = {
        ...startEvt,
        output: doneEvent.output ?? startEvt.output,
        success: doneEvent.success ?? startEvt.success,
        execution_time_ms: doneEvent.execution_time_ms ?? startEvt.execution_time_ms,
        params: startEvt.params || doneEvent.params,
        _merged: true,
      }
      return updated
    }
    // No matching start found — add as standalone (already completed)
  }

  const evtAny = event as unknown as Record<string, unknown>
  const evtType = String(event.type)
  const evtAgentName = (evtAny.agent_name as string) || ''
  const evtTool = (evtAny.tool as string) || ''
  const evtContent = (evtAny.content as string) || ''
  const evtSequence = (evtAny.sequence as number) ?? -1

  // PERF: O(1) sequence-based dedup using Map index (most common path)
  const evtTaskId = (evtAny.task_id as string) || ''
  if (sessionId && evtSequence >= 0) {
    const idx = getSeqIndex(sessionId)
    const key = seqKey(evtTaskId || '_', evtSequence)
    if (idx.has(key)) {
      return existing // Already seen this sequence — fast-path duplicate
    }
  }

  const isDuplicate = existing.some((e) => {
    if (String(e.type) !== evtType) return false
    const eAny = e as unknown as Record<string, unknown>
    const eAgentName = (eAny.agent_name as string) || ''
    const eTool = (eAny.tool as string) || ''
    const eContent = (eAny.content as string) || ''
    const eSequence = (eAny.sequence as number) ?? -2

    // FIX: BUG-RT07 - Sequences reset per task, so include task_id in dedup key
    // to prevent cross-task false dedup within the same session.
    if (evtSequence >= 0 && eSequence >= 0 && evtSequence === eSequence) {
      const eTaskId = (eAny.task_id as string) || ''
      // If both have task_id, they must match for dedup. If either is missing, fallback to sequence-only.
      if (evtTaskId && eTaskId) {
        if (evtTaskId === eTaskId) return true
        // Different task_id → not a duplicate (sequence reused across tasks)
        return false
      }
      return true
    }

    // Same tool can be called multiple times in a task (e.g., web_search called 3 times).
    // Only dedup if: (a) exact same ID, or (b) cross-source AND existing is unmerged (pending).
    // A merged (completed) tool_start means it's from a previous call — new tool_start is legitimate.
    if (evtType === 'tool_start' || evtType === 'tool_done') {
      if (eTool !== evtTool || eAgentName !== evtAgentName) return false
      const eId = String((eAny.id as string) || '')
      const evtId = String((evtAny.id as string) || '')
      // Exact same ID → obvious duplicate
      if (eId === evtId && eId !== '') return true
      // If existing tool_start is already merged (completed), new tool_start is a NEW call
      if (evtType === 'tool_start' && (eAny._merged === true)) return false
      // Same source → not a cross-delivery duplicate, could be repeated call
      const sameSource = (eId.startsWith('replay-') && evtId.startsWith('replay-')) ||
                         (eId.startsWith('ws-') && evtId.startsWith('ws-')) ||
                         (eId.startsWith('poll-') && evtId.startsWith('poll-')) ||
                         (eId.startsWith('bg-') && evtId.startsWith('bg-'))
      if (sameSource) return false
      // Cross-source (e.g., replay vs ws): likely F5 duplicate only if timestamps are close
      // Use 2s window — wider than before to catch DB flush delays
      if (Math.abs(e.timestamp - event.timestamp) < 2000) return true
      return false
    }
    // For thinking events: match by agent + content similarity
    if (evtType === 'thinking' || evtType === 'agent_thinking') {
      return eAgentName === evtAgentName && eContent === evtContent
    }
    // For agent_spawn: match by agent name
    if (evtType === 'agent_spawn') {
      return eAgentName === evtAgentName
    }
    // For planning/evaluation/next_turn_plan: match by type only (at most one per task)
    if (evtType === 'planning_start' || evtType === 'planning_done' || evtType === 'evaluation_done' || evtType === 'next_turn_plan') {
      return true
    }
    // Dedup by skill name, not just type.
    if (evtType === 'xskill_retrieve' || evtType === 'xskill_accumulate') {
      const evtSkillName = (evtAny.skill_name as string) || (evtAny.skillName as string) || ''
      const eSkillName = (eAny.skill_name as string) || (eAny.skillName as string) || ''
      return evtSkillName !== '' && eSkillName !== '' && evtSkillName === eSkillName
    }
    if (evtType === 'react_phase') {
      const evtPhase = (evtAny.phase as string) || ''
      const ePhase = (eAny.phase as string) || ''
      return evtPhase === ePhase
    }
    if (evtType === 'debate_message') {
      const evtTurn = (evtAny.turn as number) ?? -1
      const eTurn = (eAny.turn as number) ?? -2
      return evtTurn === eTurn && evtAgentName === eAgentName && evtContent === eContent
    }
    // debate_started/debate_concluded: at most one per task
    if (evtType === 'debate_started' || evtType === 'debate_concluded') {
      return true
    }
    // moderator_intervention: unique per turn + action_type + content prefix.
    if (evtType === 'moderator_intervention') {
      const evtTurn = (evtAny.turn as number) ?? -1
      const eTurn = (eAny.turn as number) ?? -2
      if (evtTurn !== eTurn) return false
      // Same turn — dedup only if same action type AND same content prefix
      const evtAction = (evtAny.action_type as string) || (evtAny.action as string) || ''
      const eAction = (eAny.action_type as string) || (eAny.action as string) || ''
      return evtAction === eAction && evtContent.slice(0, 100) === eContent.slice(0, 100)
    }
    return false
  })
  if (isDuplicate) return existing

  // PERF: Register this event's sequence in the index for future O(1) lookups
  if (sessionId && evtSequence >= 0) {
    const idx = getSeqIndex(sessionId)
    idx.add(seqKey(evtTaskId || '_', evtSequence))
  }

  const newSeq = evtSequence // reuse already-computed sequence from dedup logic above
  let updated: ExecutionEvent[]

  if (newSeq != null && newSeq > 0 && existing.length > 0) {
    // Find the correct insertion index (scan from end: find first event with seq <= newSeq)
    let insertIdx = existing.length // default: append at end
    for (let i = existing.length - 1; i >= 0; i--) {
      const iSeq = (existing[i] as unknown as Record<string, unknown>).sequence as number | undefined
      if (iSeq != null && iSeq <= newSeq) {
        insertIdx = i + 1
        break
      }
      if (i === 0) {
        // All existing events have sequence > newSeq (or no sequence): insert at beginning
        if (iSeq != null && iSeq > newSeq) {
          insertIdx = 0
        }
      }
    }
    if (insertIdx === existing.length) {
      updated = [...existing, event]
    } else {
      updated = [...existing.slice(0, insertIdx), event, ...existing.slice(insertIdx)]
    }
  } else {
    // No sequence on event — append at end (original behavior)
    updated = [...existing, event]
  }

  if (updated.length > MAX_EVENTS_PER_SESSION) {
    // P2-03 FIX: Preserve first 50 critical init events (planning, agent_spawn,
    // deep_analysis, debate_started) + last 1950 events. Previously slice(-2000)
    // discarded early context events that users need when scrolling back.
    const PRESERVE_HEAD = 50
    const head = updated.slice(0, PRESERVE_HEAD)
    const tail = updated.slice(-(MAX_EVENTS_PER_SESSION - PRESERVE_HEAD))
    return [...head, ...tail]
  }
  return updated
}

export const useExecutionStore = create<ExecutionState>((set, get) => ({
  eventsBySession: {},
  activeBySession: {},
  maxSequenceBySession: {},

  // Throttled: queue event and flush after FLUSH_INTERVAL_MS
  addEvent: (sessionId, event) => {
    eventQueue.push({ sessionId, event })
    scheduleFlush()
  },

  // Batch-add multiple events in a single state update (avoids N re-renders on reload)
  batchAddEvents: (sessionId, events) => {
    if (events.length === 0) return
    set((state) => {
      let existing = state.eventsBySession[sessionId] || []
      let maxSeq = state.maxSequenceBySession[sessionId] ?? 0
      for (const event of events) {
        existing = applyEventToList(existing, event, sessionId)
        const evtSeq = (event as unknown as Record<string, unknown>).sequence as number | undefined
        if (typeof evtSeq === 'number' && evtSeq > maxSeq) maxSeq = evtSeq
      }
      return {
        eventsBySession: { ...state.eventsBySession, [sessionId]: existing },
        maxSequenceBySession: { ...state.maxSequenceBySession, [sessionId]: maxSeq },
      }
    })
  },

  // Direct: apply immediately (called from flush or bulk setEvents)
  _addEventDirect: (sessionId, event) => {
    set((state) => {
      if (!(sessionId in state.eventsBySession) && !state.activeBySession[sessionId]) {
        const evtType = (event as unknown as Record<string, unknown>).type
        const AUTO_INIT_TYPES = new Set([
          'task_started', 'step', 'tool_start', 'tool_done', 'agent_spawn',
          'planning_start', 'planning_done', 'react_phase', 'deep_analysis',
          'deep_analysis_complete', 'agent_thinking', 'self_reflection',
          'auto_verify', 'completion_check', 'debate_proposal', 'debate_critique',
          'debate_revision', 'debate_rebuttal', 'debate_verdict',
          'xskill_retrieve', 'xskill_accumulate', 'mid_analysis',
          'system_warning', 'task_crashed', 'resource_alert', 'budget_warning',
          'swarm_round', 'swarm_round_start', 'agent_response',
          'debate_message', 'debate_started', 'debate_concluded', 'moderator_intervention',
          'debate_turn', 'debate_reflection', 'debate_gate_check',
          'debate_tool_call', 'debate_tool_result', 'debate_agent_plan',
          'debate_phase', 'debate_action_phase',
          'preparation_started', 'preparation_plan', 'preparation_complete',
          'preparation_research', 'synthesis_start', 'synthesis_done',
          // Background tabs receiving these via Monitor WS silently dropped them.
          'report_building', 'action_tool_call', 'action_tool_result',
          'swarm_activated', 'task_heartbeat',
          // FIX: BUG-RT03 - Backend emits these but they were silently dropped
          'context_compaction', 'context_trimmed', 'debate_error',
          // Event persistence gaps fix
          'evaluation_done', 'agent_stuck', 'tool_error',
          // Orchestration events
          'document_created', 'next_turn_plan', 'evolution_insight',
          // Orchestrator decision/insight events surfaced on the timeline
          'metacognitive_insight', 'authorization_required',
          'divergent_thinking_done', 'workspace_rollback',
          'infrastructure_backoff', 'agent_failed', 'agent_force_stopped',
        ])
        if (AUTO_INIT_TYPES.has(evtType as string)) {
          // Allow through — will create the session entry below
        } else {
          return state
        }
      }
      const existing = state.eventsBySession[sessionId] || []
      const updated = applyEventToList(existing, event, sessionId)
      if (updated === existing) return state // no change
      const evtSeq = (event as unknown as Record<string, unknown>).sequence as number | undefined
      const currentMax = state.maxSequenceBySession[sessionId] ?? 0
      const newMax = (typeof evtSeq === 'number' && evtSeq > currentMax) ? evtSeq : currentMax
      return {
        eventsBySession: { ...state.eventsBySession, [sessionId]: updated },
        ...(newMax !== currentMax
          ? { maxSequenceBySession: { ...state.maxSequenceBySession, [sessionId]: newMax } }
          : {}),
      }
    })
  },

  setEvents: (sessionId, events) => {
    const sorted = events
      .map((evt, i) => ({ evt, i }))
      .sort((a, b) => {
        const seqA = (a.evt as any).sequence
        const seqB = (b.evt as any).sequence
        // Both have sequence → sort by sequence (primary, correct order)
        if (seqA != null && seqB != null) {
          return seqA - seqB
        }
        // One has sequence, other doesn't → sequenced events first
        if (seqA != null && seqB == null) return -1
        if (seqA == null && seqB != null) return 1
        // Neither has sequence → fallback to timestamp + original index (stable)
        const diff = a.evt.timestamp - b.evt.timestamp
        return diff !== 0 ? diff : a.i - b.i
      })
      .map(({ evt }) => evt)
    // Merge tool pairs so replayed events show correct status
    const merged = mergeToolPairs(sorted)
    // Track max sequence for incremental fetch + rebuild seq index
    let maxSeq = 0
    const idx = getSeqIndex(sessionId)
    idx.clear() // rebuild from scratch for setEvents
    for (const evt of sorted) {
      const seq = (evt as any).sequence
      if (typeof seq === 'number' && seq > maxSeq) maxSeq = seq
      if (typeof seq === 'number' && seq >= 0) {
        const taskId = (evt as any).task_id || '_'
        idx.add(seqKey(taskId, seq))
      }
    }
    set((state) => ({
      eventsBySession: { ...state.eventsBySession, [sessionId]: merged },
      maxSequenceBySession: { ...state.maxSequenceBySession, [sessionId]: maxSeq },
    }))
  },

  clearSession: (sessionId) => {
    eventQueue = eventQueue.filter((entry) => entry.sessionId !== sessionId)
    _seqIndexBySession.delete(sessionId) // PERF: clear dedup index
    if (flushTimer !== null && eventQueue.length === 0) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    set((state) => {
      const { [sessionId]: _, ...remaining } = state.eventsBySession
      const { [sessionId]: __, ...remainingActive } = state.activeBySession
      const { [sessionId]: ___, ...remainingSeq } = state.maxSequenceBySession
      return { eventsBySession: remaining, activeBySession: remainingActive, maxSequenceBySession: remainingSeq }
    })
  },

  getMaxSequence: (sessionId) => {
    return get().maxSequenceBySession[sessionId] ?? 0
  },

  setActive: (sessionId, active) => {
    set((state) => ({
      activeBySession: { ...state.activeBySession, [sessionId]: active },
    }))
  },

  getEvents: (sessionId) => {
    return get().eventsBySession[sessionId] || []
  },
}))

// ============================================================
// Prevents leaked timers and stale queued events when module is hot-reloaded.
// ============================================================
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    if (flushTimer !== null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    eventQueue = []
  })
}
