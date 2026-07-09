// ============================================================
// Artifacts Viewer API - Directory tree, file metadata, streaming
// ============================================================

import { apiGet } from './client';
import { API_URL } from '../lib/constants';
import { AUTH_TOKEN_KEY } from './constants';
import type { TreeEntry, FileMeta, ChunkedReadResponse } from '../types/artifacts';

/** Encode each path segment individually, preserving '/' separators. */
function encodePathSegments(filePath: string): string {
  return filePath.split('/').map(encodeURIComponent).join('/');
}

// ============================================================
// Directory Tree
// ============================================================

export interface FetchDirectoryTreeParams {
  path?: string;
  depth?: number;
  task_id?: string;
  show_hidden?: boolean;
}

/** Fetch directory tree entries at a given path */
export function fetchDirectoryTree(params: FetchDirectoryTreeParams = {}): Promise<TreeEntry[]> {
  const searchParams = new URLSearchParams();
  if (params.path !== undefined) searchParams.set('path', params.path);
  if (params.depth !== undefined) searchParams.set('depth', String(params.depth));
  if (params.task_id) searchParams.set('task_id', params.task_id);
  if (params.show_hidden !== undefined) searchParams.set('show_hidden', String(params.show_hidden));
  const qs = searchParams.toString();
  return apiGet<TreeEntry[]>(`/artifacts/tree${qs ? `?${qs}` : ''}`);
}

// ============================================================
// File Metadata
// ============================================================

/** Fetch metadata for a specific file */
export function fetchFileMeta(filePath: string, taskId?: string): Promise<FileMeta> {
  const searchParams = new URLSearchParams();
  if (taskId) searchParams.set('task_id', taskId);
  const qs = searchParams.toString();
  const encodedPath = encodePathSegments(filePath);
  return apiGet<FileMeta>(`/artifacts/meta/${encodedPath}${qs ? `?${qs}` : ''}`);
}

// ============================================================
// Chunked File Read
// ============================================================

export interface FetchFileChunkParams {
  path: string;
  offset?: number;
  limit?: number;
  lines_offset?: number;
  lines_limit?: number;
  task_id?: string;
}

/** Read a chunk of file content */
export function fetchFileChunk(params: FetchFileChunkParams): Promise<ChunkedReadResponse> {
  const searchParams = new URLSearchParams();
  if (params.offset !== undefined) searchParams.set('offset', String(params.offset));
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit));
  if (params.lines_offset !== undefined) searchParams.set('lines_offset', String(params.lines_offset));
  if (params.lines_limit !== undefined) searchParams.set('lines_limit', String(params.lines_limit));
  if (params.task_id) searchParams.set('task_id', params.task_id);
  const qs = searchParams.toString();
  const encodedPath = encodePathSegments(params.path);
  return apiGet<ChunkedReadResponse>(`/artifacts/read/${encodedPath}${qs ? `?${qs}` : ''}`);
}

// ============================================================
// Stream & Download URLs (for <video>/<audio> src and downloads)
// ============================================================

/** Build authenticated stream URL for media elements */
export function getStreamUrl(filePath: string, taskId?: string): string {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const searchParams = new URLSearchParams();
  if (taskId) searchParams.set('task_id', taskId);
  if (token) searchParams.set('token', token);
  const qs = searchParams.toString();
  const encodedPath = encodePathSegments(filePath);
  return `${API_URL}/api/v1/artifacts/stream/${encodedPath}${qs ? `?${qs}` : ''}`;
}

/** Build authenticated download URL */
export function getDownloadUrl(filePath: string, taskId?: string): string {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const searchParams = new URLSearchParams();
  if (taskId) searchParams.set('task_id', taskId);
  if (token) searchParams.set('token', token);
  const qs = searchParams.toString();
  const encodedPath = encodePathSegments(filePath);
  return `${API_URL}/api/v1/artifacts/download/${encodedPath}${qs ? `?${qs}` : ''}`;
}

/** Build authenticated ZIP download URL for a directory */
export function getZipDownloadUrl(dirPath: string, taskId?: string): string {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const searchParams = new URLSearchParams();
  if (dirPath) searchParams.set('path', dirPath);
  if (taskId) searchParams.set('task_id', taskId);
  if (token) searchParams.set('token', token);
  const qs = searchParams.toString();
  return `${API_URL}/api/v1/artifacts/download-zip${qs ? `?${qs}` : ''}`;
}

/** Build authenticated ZIP download URL for an entire task */
export function getTaskZipDownloadUrl(taskId: string): string {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const qs = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${API_URL}/api/v1/artifacts/task/${encodeURIComponent(taskId)}/download-zip${qs}`;
}

/** Build authenticated ZIP download URL for an entire session */
export function getSessionZipDownloadUrl(sessionId: string): string {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const qs = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${API_URL}/api/v1/artifacts/session/${encodeURIComponent(sessionId)}/download-zip${qs}`;
}
