// ============================================================
// Nexus - Specialized Tool Renderers
// Route tool executions to specialized UI based on tool name
// ============================================================

export { CodeExecutionRenderer } from './CodeExecutionRenderer'
export { TodoRenderer } from './TodoRenderer'
export { SearchResultsRenderer } from './SearchResultsRenderer'
export { UrlFetchRenderer } from './UrlFetchRenderer'
export { FileOperationRenderer } from './FileOperationRenderer'
export { TaskDocRenderer } from './TaskDocRenderer'

// ── Toolset detection ─────────────────────────────────────────
// Maps tool name prefixes to their source system for badge display

export type ToolSource = 'hermes' | 'openclaw' | 'core' | 'task_docs' | 'browser' | 'search' | 'project' | 'unknown'

export interface ToolSourceInfo {
  source: ToolSource
  label: string
  emoji: string
  color: string
  bgColor: string
}

const TOOL_SOURCE_MAP: Array<{ pattern: RegExp; info: ToolSourceInfo }> = [
  {
    pattern: /^hermes_/i,
    info: {
      source: 'hermes',
      label: 'Hermes Agent',
      emoji: '🤖',
      color: 'text-indigo-700 dark:text-indigo-300',
      bgColor: 'bg-indigo-50 border-indigo-200 dark:bg-indigo-900/30 dark:border-indigo-800',
    },
  },
  {
    pattern: /^openclaw_|^oc_/i,
    info: {
      source: 'openclaw',
      label: 'OpenClaw',
      emoji: '🦀',
      color: 'text-orange-700 dark:text-orange-300',
      bgColor: 'bg-orange-50 border-orange-200 dark:bg-orange-900/30 dark:border-orange-800',
    },
  },
  {
    pattern: /^task_doc_/i,
    info: {
      source: 'task_docs',
      label: 'Task Memory',
      emoji: '🧠',
      color: 'text-violet-700 dark:text-violet-300',
      bgColor: 'bg-violet-50 border-violet-200 dark:bg-violet-900/30 dark:border-violet-800',
    },
  },
  {
    pattern: /^browse_|^browser_|^screenshot/i,
    info: {
      source: 'browser',
      label: 'Browser',
      emoji: '🌐',
      color: 'text-cyan-700 dark:text-cyan-300',
      bgColor: 'bg-cyan-50 border-cyan-200 dark:bg-cyan-900/30 dark:border-cyan-800',
    },
  },
  {
    pattern: /^web_search|^search_web|^google_|^bing_|^duckduckgo_/i,
    info: {
      source: 'search',
      label: 'Web Search',
      emoji: '🔍',
      color: 'text-amber-700 dark:text-amber-300',
      bgColor: 'bg-amber-50 border-amber-200 dark:bg-amber-900/30 dark:border-amber-800',
    },
  },
  {
    pattern: /^project_|^build_|^dev_server/i,
    info: {
      source: 'project',
      label: 'Project Builder',
      emoji: '🏗️',
      color: 'text-teal-700 dark:text-teal-300',
      bgColor: 'bg-teal-50 border-teal-200 dark:bg-teal-900/30 dark:border-teal-800',
    },
  },
]

/**
 * Detect which system/toolset a tool belongs to based on its name.
 * Used to display source badge on tool cards.
 */
export function getToolSource(toolName: string): ToolSourceInfo {
  for (const entry of TOOL_SOURCE_MAP) {
    if (entry.pattern.test(toolName)) {
      return entry.info
    }
  }
  return {
    source: 'core',
    label: 'Core',
    emoji: '⚙️',
    color: 'text-gray-600 dark:text-gray-400',
    bgColor: 'bg-gray-50 border-gray-200 dark:bg-gray-800/50 dark:border-gray-700',
  }
}

/** Determine which specialized renderer to use based on tool name */
export function getToolRendererType(toolName: string): string {
  const name = toolName.toLowerCase()

  // Task document operations
  if (name.startsWith('task_doc_') || name.includes('task_document')) {
    return 'task_doc'
  }

  // Code execution
  if (name.includes('execute_code') || name.includes('run_code') || name.includes('execute_command')) {
    return 'code'
  }

  // Todo operations
  if (name.includes('todo') || name.includes('task_list') || name.includes('checklist')) {
    return 'todo'
  }

  // Search operations
  if (name.includes('search') && !name.includes('session_search') && !name.includes('task_doc_search')) {
    return 'search'
  }

  // URL/web fetch operations
  if (
    name.includes('fetch_url') ||
    name.includes('browse_webpage') ||
    name.includes('http_request') ||
    name.includes('scrape')
  ) {
    return 'url'
  }

  // File operations
  if (
    name.includes('write_file') ||
    name.includes('read_file') ||
    name.includes('list_directory') ||
    name.includes('file_info') ||
    name.includes('delete_file')
  ) {
    return 'file'
  }

  return 'generic'
}
