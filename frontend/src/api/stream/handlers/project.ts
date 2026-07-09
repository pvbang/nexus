// ============================================================
// Project Handlers - Project Builder events
// ============================================================

import { devWarn } from '@/api/client'
import { _getProjStore } from '@/api/stream/utils'
import type { EventHandler } from '@/api/stream/types'

export const handleProjectEvent: EventHandler = (event) => {
  // project_phase, project_blueprint, project_server, file_implemented,
  // iteration_complete, project_complete, project_error
  try {
    const projectStore = _getProjStore()
    projectStore.getState().handleProjectEvent(event as unknown as Record<string, unknown>)
  } catch (err) {
    devWarn('[SSE] Error handling project event:', err)
  }
}
