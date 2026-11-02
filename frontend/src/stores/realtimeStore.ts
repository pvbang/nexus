// ============================================================
// Nexus - Realtime Store (Zustand)
// Manages WebSocket connection state and live event data
// ============================================================

import { create } from 'zustand'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SystemMetrics {
  cpu: number
  memory: number
  active_tasks: number
  active_agents: number
  active_swarms: number
  queue_size: number
  cache_hit_rate: number
  uptime_seconds: number
  timestamp: number
}

export interface LiveAgentStatus {
  agent_id: string
  name: string
  status: string
  role: string
  last_activity?: string
  timestamp: number
}

export interface LiveTaskStatus {
  task_id: string
  title: string
  status: string
  progress?: number
  timestamp: number
}

export interface LiveSwarmStatus {
  swarm_id: string
  name: string
  status: string
  strategy: string
  round?: number
  total_rounds?: number
  timestamp: number
}

export interface LiveEvent {
  id: string
  type: string
  message: string
  timestamp: number
  data?: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// State interface
// ---------------------------------------------------------------------------

export interface RealtimeState {
  // Connection status
  connected: boolean
  reconnecting: boolean
  lastHeartbeat: number | null

  // Live data
  systemMetrics: SystemMetrics | null
  agentStatuses: Record<string, LiveAgentStatus>
  taskStatuses: Record<string, LiveTaskStatus>
  swarmStatuses: Record<string, LiveSwarmStatus>
  recentEvents: LiveEvent[] // last 50 events

  // Actions
  setConnected: (connected: boolean) => void
  setReconnecting: (reconnecting: boolean) => void
  setHeartbeat: (timestamp: number) => void
  updateMetrics: (metrics: SystemMetrics) => void
  updateAgentStatus: (agent: LiveAgentStatus) => void
  updateTaskStatus: (task: LiveTaskStatus) => void
  updateSwarmStatus: (swarm: LiveSwarmStatus) => void
  addEvent: (event: LiveEvent) => void
  removeAgent: (agentId: string) => void
  removeTask: (taskId: string) => void
  reset: () => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum number of recent events to retain (FIFO) */
const MAX_RECENT_EVENTS = 50

// ---------------------------------------------------------------------------
// Initial state (reusable for reset)
// ---------------------------------------------------------------------------

const initialState = {
  connected: false,
  reconnecting: false,
  lastHeartbeat: null as number | null,
  systemMetrics: null as SystemMetrics | null,
  agentStatuses: {} as Record<string, LiveAgentStatus>,
  taskStatuses: {} as Record<string, LiveTaskStatus>,
  swarmStatuses: {} as Record<string, LiveSwarmStatus>,
  recentEvents: [] as LiveEvent[],
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useRealtimeStore = create<RealtimeState>((set) => ({
  ...initialState,

  setConnected: (connected: boolean) => set({ connected }),

  setReconnecting: (reconnecting: boolean) => set({ reconnecting }),

  setHeartbeat: (timestamp: number) => set({ lastHeartbeat: timestamp }),

  updateMetrics: (metrics: SystemMetrics) => set({ systemMetrics: metrics }),

  updateAgentStatus: (agent: LiveAgentStatus) =>
    set((state) => ({
      agentStatuses: {
        ...state.agentStatuses,
        [agent.agent_id]: agent,
      },
    })),

  updateTaskStatus: (task: LiveTaskStatus) =>
    set((state) => ({
      taskStatuses: {
        ...state.taskStatuses,
        [task.task_id]: task,
      },
    })),

  updateSwarmStatus: (swarm: LiveSwarmStatus) =>
    set((state) => ({
      swarmStatuses: {
        ...state.swarmStatuses,
        [swarm.swarm_id]: swarm,
      },
    })),

  addEvent: (event: LiveEvent) =>
    set((state) => {
      const updated = [event, ...state.recentEvents]
      return {
        recentEvents: updated.length > MAX_RECENT_EVENTS
          ? updated.slice(0, MAX_RECENT_EVENTS)
          : updated,
      }
    }),

  removeAgent: (agentId: string) =>
    set((state) => {
      const { [agentId]: _, ...remaining } = state.agentStatuses
      return { agentStatuses: remaining }
    }),

  removeTask: (taskId: string) =>
    set((state) => {
      const { [taskId]: _, ...remaining } = state.taskStatuses
      return { taskStatuses: remaining }
    }),

  reset: () => set({ ...initialState }),
}))
