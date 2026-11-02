// ============================================================
// Nexus - API Layer (Barrel Re-exports)
// ============================================================
// All public APIs are re-exported here so existing imports like
// `import { streamChat, apiGet, ... } from '../api'` continue to work.

// ── Types ──
export type {
  UploadResult,
  Agent,
  Task,
  Swarm,
  ChatMessage,
  SystemStats,
  Session,
  SessionMessage,
  SessionSearchResult,
  ActionEntry,
  SessionStats,
  ConversationProfile,
  LLMModel,
  StreamChatResult,
  StreamEvent,
  StreamStepEvent,
  StreamAgentEvent,
  StreamAgentSpawnEvent,
  StreamSwarmRoundEvent,
  SendChatResponse,
  StreamDebateProposalEvent,
  StreamDebateRebuttalEvent,
  StreamDebateVerdictEvent,
  StreamChatProcessCallbacks,
  StreamChatOptions,
  DecisionOption,
  PendingDecision,
  AnalyticsSummary,
  DailyUsage,
  ModelBreakdown,
  TopSession,
  HourlyPattern,
  AnalyticsData,
} from './types'

// ── Auth ──
export { isTokenExpired, refreshAccessToken } from './auth'

// ── HTTP Client ──
export { apiGet, apiPost, apiPatch, apiDelete } from './client'

// ── SSE Streaming ──
export { streamChat, fetchMissedEvents } from './stream'

// ── Sessions ──
export {
  listSessions,
  getSessionMessages,
  searchSessions,
  deleteSession,
  updateSessionTitle,
  getSessionTimeline,
  getSessionStats,
  indexMessage,
  listProfiles,
  setSessionProfile,
  listModels,
} from './sessions'

// ── Tasks ──
export { getTaskDetail, sendChatMessage } from './tasks'

// ── System / Files / WebSocket / Decisions / Analytics ──
export {
  downloadWithAuth,
  uploadAttachments,
  createMonitorWebSocket,
  answerDecision,
  getAnalytics,
} from './system'

// ── Artifacts ──
export {
  fetchDirectoryTree,
  fetchFileMeta,
  fetchFileChunk,
  getStreamUrl,
  getDownloadUrl,
} from './artifacts'
export type { FetchDirectoryTreeParams, FetchFileChunkParams } from './artifacts'
