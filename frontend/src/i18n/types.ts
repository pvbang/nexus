// ============================================================
// MekongAI Nexus - i18n Type Definitions
// Feature #9: Type-safe translation keys
// ============================================================

export type Locale = 'vi' | 'en'

/** Type-safe nested translation keys */
export interface TranslationKeys {
  common: {
    loading: string
    error: string
    success: string
    cancel: string
    save: string
    delete: string
    confirm: string
    search: string
    close: string
    back: string
    next: string
    retry: string
    noData: string
    copyToClipboard: string
    copied: string
  }
  nav: {
    chat: string
    dashboard: string
    agents: string
    swarms: string
    tasks: string
    graph: string
    xskill: string
    artifacts: string
    process: string
    analytics: string
    terminal: string
    settings: string
    skills: string
  }
  chat: {
    newChat: string
    sendMessage: string
    typeMessage: string
    noMessages: string
    thinking: string
    thinkingTokens: string
    tokenUsage: string
    estimatedCost: string
    responseTime: string
    model: string
    sessions: string
    searchSessions: string
    deleteSession: string
    renameSession: string
  }
  process: {
    planning: string
    spawning: string
    communicating: string
    executing: string
    debating: string
    synthesizing: string
    learning: string
    responding: string
    completed: string
    failed: string
    idle: string
    agentTree: string
    totalTokens: string
    totalCost: string
    activeAgents: string
    toolRunning: string
    toolCompleted: string
    toolFailed: string
    elapsed: string
  }
  analytics: {
    title: string
    period1d: string
    period7d: string
    period30d: string
    period90d: string
    totalTokens: string
    estimatedCost: string
    totalSessions: string
    totalMessages: string
    dailyUsage: string
    modelBreakdown: string
    hourlyPattern: string
    topSessions: string
  }
  settings: {
    title: string
    appearance: string
    themeMode: string
    themePreset: string
    accentColor: string
    language: string
    transparency: string
    thinkingDisplay: string
    light: string
    dark: string
    system: string
    hidden: string
    collapsed: string
    expanded: string
    resetToDefault: string
  }
  terminal: {
    title: string
    clear: string
    fontIncrease: string
    fontDecrease: string
    executing: string
    ready: string
    commandHistory: string
    copyOutput: string
    timeout: string
  }
  /** FIX 6.2: Agent monitor strings */
  agents: {
    noAgents: string
    noAgentsHint: string
    noMatchingAgents: string
    noMatchingHint: string
    selectAgent: string
    selectAgentHint: string
    uploading: string
  }
}

/**
 * Flatten nested keys into dot-notation union type.
 * e.g. 'common.loading' | 'nav.chat' | ...
 */
export type TranslationKey = {
  [Section in keyof TranslationKeys]: {
    [Key in keyof TranslationKeys[Section]]: `${Section}.${Key & string}`
  }[keyof TranslationKeys[Section]]
}[keyof TranslationKeys]
