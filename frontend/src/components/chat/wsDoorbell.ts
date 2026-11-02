// ============================================================
// Nexus - Chat WS Doorbell
//
// ARCHITECTURE: DB polling is the SOLE source of execution events.
// The Chat WebSocket (useResilientChat) is demoted to a "doorbell": it never
// pushes events into any store. It only converts incoming WS messages into
// lightweight signals that wake DB polling (pollNow) so the UI fetches the
// authoritative events from the DB immediately, reducing latency.
//
// This eliminates the previous race where WS and polling both wrote events
// into executionStore with mismatched ids/timestamps.
// ============================================================

/** A raw message coming off the Chat WebSocket. */
export interface WsDoorbellMessage {
  type: string
  [key: string]: unknown
}

/** Callbacks the doorbell fires — all event delivery is delegated to polling. */
export interface WsDoorbellDeps {
  sessionId: string
  /** Wake DB polling to fetch the latest events now. */
  onWake: () => void
  /** A new task started for this session (carries the backend task id). */
  onTaskStarted?: (taskId: string) => void
  /** Hint that the task may have reached a terminal state — polling confirms it. */
  onTerminalHint?: () => void
}

/**
 * Build a doorbell handler for Chat WS messages.
 *
 * The handler NEVER touches a store. It maps message types to signals:
 * - heartbeat/pong → ignored
 * - task_started (or session_event:task_started) → onTaskStarted(taskId) + onWake
 * - session_state/subscribed with status:"running" → onTaskStarted(taskId) + onWake
 * - stream_end/complete/error → onTerminalHint + onWake
 * - everything else (event/token/…) → onWake
 */
export function makeWsDoorbellHandler(
  deps: WsDoorbellDeps,
): (msg: WsDoorbellMessage) => void {
  return (msg: WsDoorbellMessage) => {
    const t = msg.type

    // Transport noise — never a reason to fetch.
    if (t === 'heartbeat' || t === 'pong') return

    // A task started for this session (either direct or via session_event wrapper).
    const isTaskStarted =
      t === 'task_started' ||
      (t === 'session_event' && (msg.event as string) === 'task_started')
    if (isTaskStarted) {
      const taskId = (msg.task_id as string) || ''
      if (taskId) deps.onTaskStarted?.(taskId)
      deps.onWake()
      return
    }

    // MULTI-TAB FIX: On connect the server sends `session_state` and, after a
    // `subscribe`, a `subscribed` ack — both carry the AUTHORITATIVE running
    // task_id when status==="running". A passive tab (one that did not send the
    // message) must lock polling onto THIS task. Previously it ignored the id and
    // the polling effect fell back to processStore's taskId — a previous, already
    // finished turn — surfacing a false "Task failed" + resume button. This makes
    // the backend the single source of truth for "which task is running now".
    if (t === 'session_state' || t === 'subscribed') {
      if ((msg.status as string) === 'running') {
        const taskId = (msg.task_id as string) || ''
        if (taskId) deps.onTaskStarted?.(taskId)
      }
      deps.onWake()
      return
    }

    // Possible terminal — poll the DB to confirm the real terminal event.
    if (t === 'stream_end' || t === 'complete' || t === 'error') {
      deps.onTerminalHint?.()
      deps.onWake()
      return
    }

    // Any other signal (live event, token, …) just wakes polling.
    deps.onWake()
  }
}
