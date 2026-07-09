// ============================================================
// Connection - SSE connection management, retry logic, missed event replay
// ============================================================

import { getAuthHeaders } from '@/api/auth'
import { devLog, devWarn } from '@/api/client'
import { API_URL } from '@/lib/constants'
import { _getES } from '@/api/stream/utils'

/**
 * Fetch missed events from backend for a session after a given sequence number.
 * Used by SSE reconnect logic and F5-recovery to catch up on events missed
 * during connection drops.
 *
 * Events are fed directly to executionStore for UI replay.
 */
export async function fetchMissedEvents(
  sessionId: string,
  afterSequence: number = 0,
): Promise<number> {
  try {
    const res = await fetch(
      `${API_URL}/api/v1/sessions/${sessionId}/events?after_seq=${afterSequence}&limit=200`,
      { headers: { ...getAuthHeaders() } },
    )
    if (!res.ok) {
      devWarn('[SSE-Replay] Failed to fetch missed events:', res.status)
      return 0
    }
    const data = await res.json()
    const events = Array.isArray(data) ? data : (data.events || [])
    if (events.length === 0) return 0

    const es = _getES()
    let replayed = 0
    for (const evt of events) {
      const eventType = evt.event_type || evt.type || 'unknown'
      es.addEvent(sessionId, {
        id: `replay-${evt.sequence || Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        type: eventType,
        ...evt.data,
        sequence: evt.sequence,
        timestamp: evt.timestamp || Date.now(),
      })
      replayed++
    }
    devLog(`[SSE-Replay] Replayed ${replayed} missed events for session ${sessionId}`)
    return replayed
  } catch (err) {
    devWarn('[SSE-Replay] Error fetching missed events:', err)
    return 0
  }
}
