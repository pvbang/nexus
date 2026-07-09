// ============================================================
// MekongAI Nexus - Artifacts Viewer Types
// ============================================================

/** Directory tree entry from GET /artifacts/tree */
export interface TreeEntry {
  name: string;
  display_name?: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified?: string;
  children_count?: number;
  is_binary?: boolean;
}

/** File metadata from GET /artifacts/meta/{path} */
export interface FileMeta {
  name: string;
  path: string;
  size: number;
  modified: string;
  mime_type: string;
  is_binary: boolean;
  encoding: string | null;
  line_count: number | null;
  can_preview: boolean;
  preview_type: PreviewType;
}

export type PreviewType =
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'markdown'
  | 'html'
  | 'document'
  | 'spreadsheet'
  | 'code'
  | 'text'
  | 'unknown';

/** Chunked read response from GET /artifacts/read/{path} */
export interface ChunkedReadResponse {
  content: string;
  total_size: number;
  offset: number;
  length: number;
  has_more: boolean;
  total_lines: number | null;
  encoding: string;
}

/** Lazy tree node (frontend state) */
export interface LazyTreeNode {
  entry: TreeEntry;
  children: LazyTreeNode[] | null; // null = not loaded yet
  isExpanded: boolean;
  isLoading: boolean;
  depth: number;
}

/** Flat row for virtualized rendering */
export interface FlatTreeRow {
  node: LazyTreeNode;
  depth: number;
  isLast: boolean;
}
