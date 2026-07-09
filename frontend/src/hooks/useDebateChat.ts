// ============================================================
// MekongAI Nexus - useDebateChat hook
// Accumulates debate_message events into DebateChatState
// for rendering by DebateChatView component.
//
// ============================================================

import { useState, useCallback, useRef } from 'react'
import type { DebateChatState, DebateChatMessage } from '../components/chat/DebateChatView'
import type { ExecutionEvent } from '../types/execution'

/** Cap debate messages to prevent unbounded memory growth during long debates (30+ min) */
const MAX_DEBATE_MESSAGES = 500

const INITIAL_STATE: DebateChatState = {
  messages: [],
  isActive: false,
  totalTurns: 0,
  maxTurns: 30,
  budgetRemaining: 1.0,
  agents: [],
}

/**
 * Hook that accumulates debate chat events into renderable state.
 * 
 * Usage:
 *   const { state, processEvent, reset } = useDebateChat()
 *   // On each SSE event:
 *   processEvent(event)
 *   // Render:
 *   <DebateChatView state={state} />
 */
export function useDebateChat() {
  const [state, setState] = useState<DebateChatState>(INITIAL_STATE)
  const stateRef = useRef(state)
  stateRef.current = state

  const processEvent = useCallback((event: ExecutionEvent) => {
    const eventType = event.type

    if (eventType === 'debate_started') {
      const e = event as any
      setState({
        messages: [],
        isActive: true,
        totalTurns: 0,
        maxTurns: e.max_turns || 30,
        budgetRemaining: 1.0,
        agents: (e.agents || []).map((name: string) => ({ name, role: '', debate_role: '' })),
      })
      return true
    }

    if (eventType === 'debate_turn' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentSpeaker: e.speaker || e.agent_name || '',
        currentSpeakerRole: e.speaker_role || e.agent_role || '',
        currentActivity: 'planning',
        currentTool: undefined,
        totalTurns: Math.max(prev.totalTurns, e.turn || 0),
        budgetRemaining: e.budget_remaining ?? prev.budgetRemaining,
      }))
      return true
    }

    if (eventType === 'debate_agent_plan' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentSpeaker: e.agent_name || prev.currentSpeaker,
        currentActivity: 'researching',
        currentTool: undefined,
      }))
      return true
    }

    if (eventType === 'debate_tool_call' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentSpeaker: e.agent_name || prev.currentSpeaker,
        currentActivity: e.phase === 'action' ? 'acting' : 'researching',
        currentTool: e.tool || '',
      }))
      return true
    }

    if (eventType === 'debate_tool_result' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentActivity: e.phase === 'action' ? 'acting' : 'researching',
        currentTool: undefined, // Tool finished, waiting for next action
      }))
      return true
    }

    if (eventType === 'debate_action_phase' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentSpeaker: e.agent_name || prev.currentSpeaker,
        currentActivity: 'acting',
        currentTool: undefined,
      }))
      return true
    }

    if (eventType === 'debate_message') {
      const e = event as any
      const msg: DebateChatMessage = {
        turn: e.turn || 0,
        agent_name: e.agent_name || 'Unknown',
        agent_role: e.agent_role || '',
        content: e.content || '',
        message_type: e.message_type || 'opinion',
        references: e.references || [],
        tools_used: e.tools_used || [],
        evidence_items: e.evidence_items || [],
        confidence: e.confidence || 0.7,
        timestamp: e.timestamp || new Date().toISOString(),
        is_final: e.is_final,
        quality_score: e.quality_score,
        convergence_score: e.convergence_score,
        action_type: e.action_type,
      }

      setState(prev => {
        const isDuplicate = prev.messages.some(
          (m) => m.turn === msg.turn && m.agent_name === msg.agent_name
            && m.content.slice(0, 80) === msg.content.slice(0, 80),
        )
        if (isDuplicate) return prev

        const updated = [...prev.messages, msg]
        // Sort by turn for correct chronological order
        // Multiple messages can share the same turn (moderator + agent in same turn).
        updated.sort((a, b) => {
          const turnDiff = a.turn - b.turn
          if (turnDiff !== 0) return turnDiff
          const aTime = new Date(a.timestamp || 0).getTime()
          const bTime = new Date(b.timestamp || 0).getTime()
          return aTime - bTime
        })
        // Cap messages to prevent unbounded memory growth during long debates
        const capped = updated.length > MAX_DEBATE_MESSAGES
          ? updated.slice(-MAX_DEBATE_MESSAGES)
          : updated
        return {
          ...prev,
          messages: capped,
          totalTurns: Math.max(prev.totalTurns, msg.turn),
          budgetRemaining: e.budget_remaining ?? prev.budgetRemaining,
          // Clear activity indicator — message posted means turn is done
          currentSpeaker: undefined,
          currentSpeakerRole: undefined,
          currentActivity: undefined,
          currentTool: undefined,
        }
      })
      return true
    }

    if (eventType === 'agent_spawn' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => {
        const existingIdx = prev.agents.findIndex(a => a.name === e.agent_name)
        if (existingIdx >= 0) {
          const updated = [...prev.agents]
          updated[existingIdx] = {
            name: e.agent_name || '',
            role: e.agent_role || updated[existingIdx].role,
            debate_role: e.debate_role || e.agent_role || updated[existingIdx].debate_role,
          }
          return { ...prev, agents: updated }
        }
        return {
          ...prev,
          agents: [...prev.agents, {
            name: e.agent_name || '',
            role: e.agent_role || '',
            debate_role: e.debate_role || e.agent_role || '',
          }],
        }
      })
      return true
    }

    if (eventType === 'moderator_intervention' && stateRef.current.isActive) {
      const e = event as any
      // Moderator intervention is also shown as a message
      // SSE handler (chat.py) transforms the backend field `message` → `content`.
      // WS path preserves original field name `message` in data payload.
      const msg: DebateChatMessage = {
        turn: e.turn || stateRef.current.totalTurns,
        agent_name: 'Moderator',
        agent_role: 'moderator',
        content: e.content || e.message || '',
        message_type: 'moderator',
        references: [],
        tools_used: [],
        evidence_items: [],
        confidence: 1.0,
        timestamp: new Date().toISOString(),
        quality_score: e.quality_score,
        convergence_score: e.convergence_score,
        // WS raw path sends as `action`. Previously only read `e.action` which is
        // undefined on SSE path, causing moderator action display to be blank.
        action_type: e.action_type || e.action,
      }

      setState(prev => {
        const isDuplicate = prev.messages.some(
          (m) => m.agent_name === 'Moderator'
            && m.turn === msg.turn
            && m.content.slice(0, 80) === msg.content.slice(0, 80),
        )
        if (isDuplicate) return prev
        return {
          ...prev,
          messages: [...prev.messages, msg].sort((a, b) => a.turn - b.turn),
        }
      })
      return true
    }

    if (eventType === 'debate_phase' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        currentPhase: e.phase || prev.currentPhase,
        phaseStatus: e.status || undefined,
      }))
      return true
    }

    if (eventType === 'debate_reflection' && stateRef.current.isActive) {
      const e = event as any
      setState(prev => ({
        ...prev,
        reflectionData: {
          progressScore: e.progress_score ?? 0.5,
          sufficient: !!e.sufficient,
          stagnating: !!e.stagnating,
          recommendation: e.recommendation || 'continue',
        },
      }))
      return true
    }

    if (eventType === 'synthesis_start' && stateRef.current.isActive) {
      setState(prev => ({
        ...prev,
        currentSpeaker: 'Synthesis Engine',
        currentSpeakerRole: 'synthesizer',
        currentActivity: 'synthesizing',
        currentTool: undefined,
        currentPhase: 'synthesis',
      }))
      return true
    }

    if (eventType === 'synthesis_done' && stateRef.current.isActive) {
      setState(prev => ({
        ...prev,
        currentSpeaker: undefined,
        currentActivity: undefined,
        currentTool: undefined,
      }))
      return true
    }

    if (eventType === 'debate_concluded') {
      const e = event as any
      setState(prev => ({
        ...prev,
        isActive: false,
        concludedBy: e.concluded_by || 'unknown',
        totalTurns: e.total_turns || prev.totalTurns,
        // Clear activity on conclusion
        currentSpeaker: undefined,
        currentSpeakerRole: undefined,
        currentActivity: undefined,
        currentTool: undefined,
      }))
      return true
    }

    // Not a debate event
    return false
  }, [])

  const reset = useCallback(() => {
    setState(INITIAL_STATE)
  }, [])

  const isDebateActive = state.isActive || state.messages.length > 0

  return { state, processEvent, reset, isDebateActive }
}
