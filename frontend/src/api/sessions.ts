// ============================================================
// Session/Conversation API calls
// ============================================================

import { apiGet, apiPost, apiPatch, apiDelete } from './client'
import type {
  Session,
  SessionMessage,
  SessionSearchResult,
  ActionEntry,
  SessionStats,
  ConversationProfile,
  LLMModel,
} from './types'

/** List all sessions with auto-generated titles */
export function listSessions(limit = 50): Promise<Session[]> {
  return apiGet<Session[]>(`/sessions/?limit=${limit}`)
}

/** Load conversation messages for a session */
export function getSessionMessages(sessionId: string, offset = 0, limit = 200): Promise<SessionMessage[]> {
  return apiGet<SessionMessage[]>(`/sessions/${sessionId}/messages?offset=${offset}&limit=${limit}`)
}

/** Search across all conversations */
export function searchSessions(query: string, limit = 20): Promise<{
  query: string
  results: SessionSearchResult[]
  total: number
}> {
  return apiGet(`/sessions/search?q=${encodeURIComponent(query)}&limit=${limit}`)
}

/** Delete a session */
export function deleteSession(sessionId: string): Promise<{ status: string; messages_deleted: number }> {
  return apiDelete(`/sessions/${sessionId}`)
}

/** Update session title */
export function updateSessionTitle(sessionId: string, title: string): Promise<{ status: string }> {
  return apiPatch(`/sessions/${sessionId}/title`, { title })
}

/** Get session action timeline */
export function getSessionTimeline(sessionId: string): Promise<ActionEntry[]> {
  return apiGet<ActionEntry[]>(`/sessions/${sessionId}/timeline`)
}

/** Get session stats */
export function getSessionStats(sessionId: string): Promise<SessionStats> {
  return apiGet<SessionStats>(`/sessions/${sessionId}/stats`)
}

/** Index a message for search */
export function indexMessage(sessionId: string, role: string, content: string): Promise<{ status: string }> {
  return apiPost('/sessions/index', { session_id: sessionId, role, content })
}

// ============================================================
// Profiles & Models API
// ============================================================

export function listProfiles(): Promise<ConversationProfile[]> {
  return apiGet<ConversationProfile[]>('/chat/profiles')
}

export function setSessionProfile(sessionId: string, profileName: string): Promise<Record<string, unknown>> {
  return apiPost(`/chat/profiles/${sessionId}`, { profile_name: profileName })
}

export function listModels(): Promise<LLMModel[]> {
  return apiGet<LLMModel[]>('/chat/models')
}
