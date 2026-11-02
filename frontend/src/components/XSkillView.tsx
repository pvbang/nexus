// ============================================================
// Nexus - XSkill Continual Learning View
// Enhanced with UI primitives (Card, Badge, ProgressBar, Tabs,
// Spinner, EmptyState, Skeleton) and usePolling hook
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import { Search, BookOpen, Lightbulb, TrendingUp, RefreshCw, Trash2 } from 'lucide-react'
import { apiGet, apiDelete } from '../api'
import { usePolling } from '../hooks/usePolling'
import { Card } from './ui/Card'
import { Badge } from './ui/Badge'
import { ProgressBar } from './ui/ProgressBar'
import { Tabs, type Tab } from './ui/Tabs'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import { Skeleton } from './ui/Skeleton'
import { cn } from '../lib/cn'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Skill {
  id: string
  title: string
  task_type: string
  content: string
  tools_used: string[]
  effectiveness: number
  success_count: number
  fail_count: number
  source_task_id?: string
  metadata?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

interface Experience {
  id: string
  content: string
  type: string
  context?: string
  lesson?: string
  importance: number
  times_applied: number
  times_helpful: number
  source_task_id?: string
  source_agent_id?: string
  metadata?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

interface XSkillStats {
  skills?: { total_skills?: number; avg_effectiveness?: number }
  experiences?: { total?: number }
}

interface XSkillData {
  skills: Skill[]
  experiences: Experience[]
  stats: XSkillStats | null
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type ExperienceType = 'tactical' | 'error_recovery' | 'optimization' | 'pattern'

const experienceTypeBadgeVariant: Record<ExperienceType, 'info' | 'error' | 'success' | 'purple'> = {
  tactical: 'info',
  error_recovery: 'error',
  optimization: 'success',
  pattern: 'purple',
}

function getEffectivenessVariant(val: number): 'success' | 'warning' | 'error' {
  if (val >= 0.7) return 'success'
  if (val >= 0.4) return 'warning'
  return 'error'
}

function getImportanceInfo(val: number): { label: string; variant: 'error' | 'warning' | 'default' } {
  if (val >= 0.7) return { label: 'High', variant: 'error' }
  if (val >= 0.4) return { label: 'Medium', variant: 'warning' }
  return { label: 'Low', variant: 'default' }
}

// ---------------------------------------------------------------------------
// Data fetcher
// ---------------------------------------------------------------------------

async function fetchXSkillData(): Promise<XSkillData> {
  const [skills, experiences, stats] = await Promise.all([
    apiGet<Skill[]>('/xskill/skills'),
    apiGet<Experience[]>('/xskill/experiences'),
    apiGet<XSkillStats>('/xskill/stats'),
  ])
  return { skills, experiences, stats }
}

// ---------------------------------------------------------------------------
// Skeleton loaders
// ---------------------------------------------------------------------------

function SkillCardSkeleton() {
  return (
    <Card variant="default" padding="md" className="animate-pulse">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 space-y-2">
          <Skeleton className="h-5 w-3/4" />
          <div className="flex gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-24" />
          </div>
        </div>
        <Skeleton className="h-8 w-12" />
      </div>
      <Skeleton className="mt-3 h-2 w-full" />
      <Skeleton className="mt-3 h-12 w-full" />
    </Card>
  )
}

function ExperienceCardSkeleton() {
  return (
    <Card variant="default" padding="md" className="animate-pulse">
      <div className="flex gap-2 mb-3">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-5 w-16" />
      </div>
      <Skeleton className="h-4 w-full" />
      <Skeleton className="mt-1 h-4 w-5/6" />
      <Skeleton className="mt-3 h-16 w-full" />
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function XSkillView() {
  const [activeTab, setActiveTab] = useState<string>('skills')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedSkillId, setExpandedSkillId] = useState<string | null>(null)
  const [expandedExpId, setExpandedExpId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // Use polling hook instead of manual interval
  const { data, loading, error, refresh } = usePolling<XSkillData>({
    fetcher: fetchXSkillData,
    interval: 10000,
  })

  const handleDeleteSkill = useCallback(async (e: React.MouseEvent, skillId: string) => {
    e.stopPropagation()
    if (!confirm('Xóa skill này? Hành động không thể hoàn tác.')) return
    setDeletingId(skillId)
    try {
      await apiDelete(`/xskill/skills/${skillId}`)
      void refresh()
    } finally {
      setDeletingId(null)
    }
  }, [refresh])

  const handleDeleteExperience = useCallback(async (e: React.MouseEvent, expId: string) => {
    e.stopPropagation()
    if (!confirm('Xóa experience này? Hành động không thể hoàn tác.')) return
    setDeletingId(expId)
    try {
      await apiDelete(`/xskill/experiences/${expId}`)
      void refresh()
    } finally {
      setDeletingId(null)
    }
  }, [refresh])

  const skills = data?.skills ?? []
  const experiences = data?.experiences ?? []
  const stats = data?.stats ?? null

  // Filter skills by search
  const filteredSkills = useMemo(() => {
    if (!searchQuery.trim()) return skills
    const q = searchQuery.toLowerCase()
    return skills.filter(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.task_type.toLowerCase().includes(q) ||
        s.content.toLowerCase().includes(q) ||
        s.tools_used.some((t) => t.toLowerCase().includes(q)),
    )
  }, [skills, searchQuery])

  // Filter experiences by search
  const filteredExperiences = useMemo(() => {
    if (!searchQuery.trim()) return experiences
    const q = searchQuery.toLowerCase()
    return experiences.filter(
      (e) =>
        e.content.toLowerCase().includes(q) ||
        e.type.toLowerCase().includes(q) ||
        (e.lesson?.toLowerCase().includes(q) ?? false) ||
        (e.context?.toLowerCase().includes(q) ?? false),
    )
  }, [experiences, searchQuery])

  // Tab definitions with counts
  const tabs: Tab[] = useMemo(
    () => [
      {
        id: 'skills',
        label: 'Skills',
        icon: <BookOpen className="w-4 h-4" />,
        count: skills.length,
      },
      {
        id: 'experiences',
        label: 'Experiences',
        icon: <Lightbulb className="w-4 h-4" />,
        count: experiences.length,
      },
    ],
    [skills.length, experiences.length],
  )

  const handleRefresh = useCallback(() => {
    void refresh()
  }, [refresh])

  // Initial loading state with skeletons
  const isInitialLoad = loading && !data

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="bg-white dark:bg-[#0f0f17] border-b border-border-light dark:border-[#2a2a45]">
        <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h2 className="font-display text-lg font-semibold text-text-dark dark:text-[#f0f0f8]">
                XSkill — Continual Learning
              </h2>
              <p className="text-sm text-text-muted dark:text-[#6b6b85] mt-0.5">
                Hệ thống tự học: Skills + Experiences từ mỗi task
              </p>
            </div>

            {/* Stats Summary */}
            {stats && (
              <div className="flex items-center gap-5">
                <div className="text-center">
                  <div className="text-xl font-bold font-display text-brand-600 dark:text-blue-400">
                    {stats.skills?.total_skills ?? 0}
                  </div>
                  <div className="text-[10px] text-text-muted dark:text-[#6b6b85] uppercase tracking-wider">Skills</div>
                </div>
                <div className="w-px h-8 bg-border dark:bg-[#2a2a45]" />
                <div className="text-center">
                  <div className="text-xl font-bold font-display text-purple-600 dark:text-purple-400">
                    {stats.experiences?.total ?? 0}
                  </div>
                  <div className="text-[10px] text-text-muted dark:text-[#6b6b85] uppercase tracking-wider">Experiences</div>
                </div>
                {(stats.skills?.avg_effectiveness ?? 0) > 0 && (
                  <>
                    <div className="w-px h-8 bg-border dark:bg-[#2a2a45]" />
                    <div className="text-center">
                      <div className="text-xl font-bold font-display text-emerald-600 dark:text-emerald-400">
                        {((stats.skills?.avg_effectiveness ?? 0) * 100).toFixed(0)}%
                      </div>
                      <div className="text-[10px] text-text-muted dark:text-[#6b6b85] uppercase tracking-wider">Effectiveness</div>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Search + Tabs row */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mt-4">
            <Tabs
              tabs={tabs}
              activeTab={activeTab}
              onChange={setActiveTab}
              variant="pill"
              size="sm"
            />

            <div className="flex items-center gap-2">
              {/* Search input */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted dark:text-[#6b6b85]" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={activeTab === 'skills' ? 'Search skills...' : 'Search experiences...'}
                  className={cn(
                    'pl-9 pr-3 py-1.5 text-sm rounded-lg border border-border-light bg-white',
                    'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:text-[#e4e4ed] dark:placeholder:text-[#6b6b85]',
                    'focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500/20',
                    'w-48 sm:w-56 transition-colors',
                  )}
                />
              </div>

              {/* Refresh button */}
              <button
                onClick={handleRefresh}
                disabled={loading}
                className={cn(
                  'flex items-center justify-center w-8 h-8 rounded-lg border border-border-light',
                  'dark:border-[#2a2a45] hover:bg-[#f4f5f7] dark:hover:bg-[#1a1a2e]',
                  'text-text-muted dark:text-[#6b6b85] transition-colors',
                  'disabled:opacity-50',
                )}
                title="Refresh data"
              >
                <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border-b border-red-200 dark:border-red-800 px-4 py-2 flex items-center justify-center gap-2">
          <span className="text-[13px] text-red-700 dark:text-red-300 font-medium">
            Failed to load XSkill data: {error}
          </span>
          <button
            onClick={handleRefresh}
            className="text-[12px] text-red-600 dark:text-red-400 underline hover:text-red-800 dark:hover:text-red-200 ml-2"
          >
            Retry
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto bg-[#f8f9fb] dark:bg-[#0f0f17]">
        <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-3">
          {/* Initial loading skeletons */}
          {isInitialLoad && (
            <>
              {activeTab === 'skills'
                ? Array.from({ length: 3 }).map((_, i) => <SkillCardSkeleton key={i} />)
                : Array.from({ length: 3 }).map((_, i) => <ExperienceCardSkeleton key={i} />)}
            </>
          )}

          {/* Skills Tab */}
          {!isInitialLoad && activeTab === 'skills' && (
            <>
              {filteredSkills.length === 0 && (
                <EmptyState
                  icon="📚"
                  title={searchQuery ? 'Không tìm thấy skill nào' : 'Chưa có skill nào được học'}
                  description={
                    searchQuery
                      ? `Không có kết quả cho "${searchQuery}". Thử từ khóa khác.`
                      : 'Skills tự động được tạo sau mỗi task hoàn thành'
                  }
                  action={
                    searchQuery
                      ? { label: 'Xóa bộ lọc', onClick: () => setSearchQuery('') }
                      : undefined
                  }
                />
              )}
              {filteredSkills.map((skill, index) => {
                const effVariant = getEffectivenessVariant(skill.effectiveness)
                const isExpanded = expandedSkillId === skill.id
                return (
                  <Card
                    key={skill.id}
                    variant="interactive"
                    padding="md"
                    className="animate-slide-up cursor-pointer"
                    style={{ animationDelay: `${index * 50}ms` }}
                    onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <h3 className="font-display font-semibold text-text-dark dark:text-[#f0f0f8]">
                          {skill.title}
                        </h3>
                        <div className="flex flex-wrap items-center gap-2 mt-1.5">
                          <Badge variant="brand">{skill.task_type}</Badge>
                          <span className="text-xs text-text-muted dark:text-[#6b6b85]">
                            ✅ {skill.success_count} · ❌ {skill.fail_count}
                          </span>
                        </div>
                      </div>
                      <div className="text-right shrink-0 flex items-center gap-2">
                        <Badge variant={effVariant} size="md">
                          <TrendingUp className="w-3 h-3 mr-0.5" />
                          {(skill.effectiveness * 100).toFixed(0)}%
                        </Badge>
                        <button
                          onClick={(e) => handleDeleteSkill(e, skill.id)}
                          disabled={deletingId === skill.id}
                          className={cn(
                            'p-1.5 rounded-md text-text-muted hover:text-red-500 hover:bg-red-50',
                            'dark:hover:text-red-400 dark:hover:bg-red-900/20 transition-colors',
                            'disabled:opacity-50 disabled:pointer-events-none',
                          )}
                          title="Xóa skill"
                        >
                          <Trash2 className={cn('w-4 h-4', deletingId === skill.id && 'animate-pulse')} />
                        </button>
                        <span className="text-xs text-text-muted dark:text-[#6b6b85]">
                          {isExpanded ? '▲' : '▼'}
                        </span>
                      </div>
                    </div>

                    {/* Effectiveness bar */}
                    <ProgressBar
                      value={skill.effectiveness * 100}
                      variant={effVariant}
                      size="sm"
                      className="mt-3"
                    />

                    {/* Content — expandable */}
                    <p className={cn(
                      "mt-3 text-sm text-text-secondary dark:text-[#a0a0b8] leading-normal whitespace-pre-wrap",
                      !isExpanded && "line-clamp-3",
                    )}>
                      {skill.content}
                    </p>

                    {/* Tools */}
                    {skill.tools_used && skill.tools_used.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {skill.tools_used.map((tool) => (
                          <Badge key={tool} variant="purple" size="sm">
                            🔧 {tool}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {/* Expanded detail section */}
                    {isExpanded && (
                      <div className="mt-4 pt-4 border-t border-border-light dark:border-[#2a2a45] space-y-3">
                        <div className="grid grid-cols-2 gap-4 text-xs">
                          <div>
                            <span className="font-medium text-text-muted dark:text-[#6b6b85]">ID</span>
                            <p className="font-mono text-text-secondary dark:text-[#a0a0b8] mt-0.5 select-all text-[10px]">{skill.id}</p>
                          </div>
                          <div>
                            <span className="font-medium text-text-muted dark:text-[#6b6b85]">Task Type</span>
                            <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">{skill.task_type}</p>
                          </div>
                          <div>
                            <span className="font-medium text-text-muted dark:text-[#6b6b85]">Success Rate</span>
                            <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">
                              {skill.success_count + skill.fail_count > 0
                                ? `${((skill.success_count / (skill.success_count + skill.fail_count)) * 100).toFixed(0)}%`
                                : 'N/A'
                              } ({skill.success_count} success / {skill.fail_count} fail)
                            </p>
                          </div>
                          <div>
                            <span className="font-medium text-text-muted dark:text-[#6b6b85]">Effectiveness</span>
                            <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">
                              {(skill.effectiveness * 100).toFixed(1)}%
                            </p>
                          </div>
                          {skill.source_task_id && (
                            <div>
                              <span className="font-medium text-text-muted dark:text-[#6b6b85]">Source Task</span>
                              <p className="font-mono text-text-secondary dark:text-[#a0a0b8] mt-0.5 text-[10px]">{skill.source_task_id}</p>
                            </div>
                          )}
                          {skill.updated_at && (
                            <div>
                              <span className="font-medium text-text-muted dark:text-[#6b6b85]">Last Updated</span>
                              <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">{new Date(skill.updated_at).toLocaleString()}</p>
                            </div>
                          )}
                        </div>

                        {/* Metadata (agents used, strategy, etc.) */}
                        {skill.metadata && Object.keys(skill.metadata).length > 0 && (() => {
                          const meta = skill.metadata as Record<string, unknown>
                          const agents = Array.isArray(meta.agents_used) ? meta.agents_used as Array<Record<string, string>> : null
                          const strategy = meta.strategy ? String(meta.strategy) : null
                          return (
                            <div className="mt-3">
                              <span className="font-medium text-text-muted dark:text-[#6b6b85] text-xs">Metadata</span>
                              <div className="mt-1.5 flex flex-wrap gap-2">
                                {agents && (
                                  <div className="text-[11px] text-text-secondary dark:text-[#a0a0b8]">
                                    <span className="font-medium">Agents:</span>{' '}
                                    {agents.map((a) => `${a.name} (${a.role})`).join(', ')}
                                  </div>
                                )}
                                {strategy && (
                                  <Badge variant="brand" size="sm">
                                    Strategy: {strategy}
                                  </Badge>
                                )}
                              </div>
                            </div>
                          )
                        })()}
                      </div>
                    )}

                    <div className="text-[10px] text-text-muted dark:text-[#6b6b85] mt-3">
                      Created: {skill.created_at ? new Date(skill.created_at).toLocaleString() : 'N/A'}
                    </div>
                  </Card>
                )
              })}
            </>
          )}

          {/* Experiences Tab */}
          {!isInitialLoad && activeTab === 'experiences' && (
            <>
              {filteredExperiences.length === 0 && (
                <EmptyState
                  icon="💡"
                  title={searchQuery ? 'Không tìm thấy experience nào' : 'Chưa có experience nào'}
                  description={
                    searchQuery
                      ? `Không có kết quả cho "${searchQuery}". Thử từ khóa khác.`
                      : 'Experiences tự động rút ra từ mỗi task'
                  }
                  action={
                    searchQuery
                      ? { label: 'Xóa bộ lọc', onClick: () => setSearchQuery('') }
                      : undefined
                  }
                />
              )}
              {filteredExperiences.map((exp, index) => {
                const importance = getImportanceInfo(exp.importance)
                const typeVariant =
                  experienceTypeBadgeVariant[exp.type as ExperienceType] ?? 'default'
                const isExpanded = expandedExpId === exp.id

                return (
                  <Card
                    key={exp.id}
                    variant="interactive"
                    padding="md"
                    className="animate-slide-up cursor-pointer"
                    style={{ animationDelay: `${index * 50}ms` }}
                    onClick={() => setExpandedExpId(isExpanded ? null : exp.id)}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                          <Badge variant={typeVariant}>{exp.type}</Badge>
                          <Badge variant={importance.variant}>{importance.label}</Badge>
                          <span className="text-xs text-text-muted dark:text-[#6b6b85]">
                            Applied: {exp.times_applied}× · Helpful: {exp.times_helpful}×
                          </span>
                          <button
                            onClick={(e) => handleDeleteExperience(e, exp.id)}
                            disabled={deletingId === exp.id}
                            className={cn(
                              'p-1 rounded-md text-text-muted hover:text-red-500 hover:bg-red-50',
                              'dark:hover:text-red-400 dark:hover:bg-red-900/20 transition-colors',
                              'disabled:opacity-50 disabled:pointer-events-none ml-auto',
                            )}
                            title="Xóa experience"
                          >
                            <Trash2 className={cn('w-3.5 h-3.5', deletingId === exp.id && 'animate-pulse')} />
                          </button>
                          <span className="text-xs text-text-muted dark:text-[#6b6b85]">
                            {isExpanded ? '▲' : '▼'}
                          </span>
                        </div>
                        <p className={cn(
                          "text-sm text-text-primary dark:text-[#e4e4ed] leading-normal whitespace-pre-wrap",
                          !isExpanded && "line-clamp-3",
                        )}>
                          {exp.content}
                        </p>

                        {/* Lesson */}
                        {exp.lesson && (
                          <div className="mt-3 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                            <div className="text-xs font-medium text-amber-700 dark:text-amber-400 mb-0.5">
                              💡 Lesson Learned
                            </div>
                            <p className={cn(
                              "text-xs text-amber-800 dark:text-amber-300 leading-relaxed",
                              !isExpanded && "line-clamp-2",
                            )}>
                              {exp.lesson}
                            </p>
                          </div>
                        )}

                        {/* Context */}
                        {exp.context && (
                          <p className={cn(
                            "mt-2 text-xs text-text-muted dark:text-[#6b6b85]",
                            !isExpanded && "line-clamp-1",
                          )}>
                            📌 {exp.context}
                          </p>
                        )}

                        {/* Expanded detail section */}
                        {isExpanded && (
                          <div className="mt-4 pt-4 border-t border-border-light dark:border-[#2a2a45] space-y-3">
                            <div className="grid grid-cols-2 gap-4 text-xs">
                              <div>
                                <span className="font-medium text-text-muted dark:text-[#6b6b85]">ID</span>
                                <p className="font-mono text-text-secondary dark:text-[#a0a0b8] mt-0.5 select-all">{exp.id}</p>
                              </div>
                              <div>
                                <span className="font-medium text-text-muted dark:text-[#6b6b85]">Type</span>
                                <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">{exp.type}</p>
                              </div>
                              <div>
                                <span className="font-medium text-text-muted dark:text-[#6b6b85]">Importance</span>
                                <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">{(exp.importance * 100).toFixed(0)}%</p>
                              </div>
                              <div>
                                <span className="font-medium text-text-muted dark:text-[#6b6b85]">Helpfulness</span>
                                <p className="text-text-secondary dark:text-[#a0a0b8] mt-0.5">
                                  {exp.times_applied > 0
                                    ? `${((exp.times_helpful / exp.times_applied) * 100).toFixed(0)}% helpful`
                                    : 'Not yet applied'
                                  }
                                </p>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="text-[10px] text-text-muted dark:text-[#6b6b85] mt-3">
                      {exp.created_at ? new Date(exp.created_at).toLocaleString() : ''}
                    </div>
                  </Card>
                )
              })}
            </>
          )}

          {/* Inline loading indicator for subsequent polls */}
          {loading && data && (
            <div className="flex items-center justify-center py-4">
              <Spinner size="sm" className="text-brand-500" />
              <span className="ml-2 text-xs text-text-muted dark:text-[#6b6b85]">Refreshing...</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
