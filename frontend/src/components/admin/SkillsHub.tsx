// ============================================================
// MekongAI Nexus - Skills Hub Admin View
// Skills marketplace with search, detail modal, and invoke
// ============================================================

import { useState, useCallback, useMemo } from 'react'
import { apiGet, apiPost } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { Input } from '../ui/Input'
import { Modal } from '../ui/Modal'
import { Skeleton } from '../ui/Skeleton'

// ============================================================
// Types
// ============================================================

interface Skill {
  name: string
  description: string
  category?: string
  type?: string
  commands?: string[]
  slash_commands?: string[]
  parameters?: Record<string, SkillParameter>
  invokable?: boolean
  file_path?: string
  metadata?: Record<string, unknown>
  command?: string
}

interface SkillRunnerResponse {
  skills: Array<{
    name: string
    description: string
    command?: string
    path?: string
    has_supporting_files?: boolean
    frontmatter?: Record<string, unknown>
  }>
}

interface SkillParameter {
  type: string
  description?: string
  required?: boolean
  default?: unknown
}

interface SkillInvokeResult {
  status: string
  result?: unknown
  error?: string
}


// ============================================================
// Skeleton loader for skill cards
// ============================================================

function SkillCardSkeleton() {
  return (
    <Card variant="default" padding="md">
      <Skeleton className="h-5 w-3/4 mb-2" />
      <Skeleton className="h-4 w-full mb-1" />
      <Skeleton className="h-4 w-2/3 mb-3" />
      <div className="flex items-center gap-2">
        <Skeleton className="h-5 w-16" variant="rectangular" />
        <Skeleton className="h-8 w-16" variant="rectangular" />
      </div>
    </Card>
  )
}

// ============================================================
// Main Component
// ============================================================

export function SkillsHub() {
  const [search, setSearch] = useState('')
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null)
  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [scanMessage, setScanMessage] = useState<string | null>(null)

  // Invoke state
  const [invokeParams, setInvokeParams] = useState<Record<string, string>>({})
  const [taskDescription, setTaskDescription] = useState('')
  const [invoking, setInvoking] = useState(false)
  const [invokeResult, setInvokeResult] = useState<SkillInvokeResult | null>(null)
  const [invokeError, setInvokeError] = useState<string | null>(null)

  // Fetch skills list with polling (30s) — uses /skill-runner (working endpoint)
  const { data: skills, loading, error, refresh } = usePolling<Skill[]>({
    fetcher: useCallback(async () => {
      const res = await apiGet<SkillRunnerResponse>('/skill-runner')
      return (res.skills || []).map((s) => ({
        name: s.name,
        description: s.description || '',
        category: s.frontmatter?.category as string | undefined,
        type: s.frontmatter?.category as string | undefined,
        commands: s.command ? [s.command] : undefined,
        invokable: true,
        file_path: s.path || undefined,
        metadata: s.frontmatter || undefined,
      }))
    }, []),
    interval: 30_000,
  })

  // Filter skills by search
  const filteredSkills = useMemo(() => {
    if (!skills) return []
    if (!search.trim()) return skills
    const query = search.toLowerCase()
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(query) ||
        s.description?.toLowerCase().includes(query) ||
        s.category?.toLowerCase().includes(query) ||
        s.type?.toLowerCase().includes(query),
    )
  }, [skills, search])

  // Scan skills
  const handleScan = async () => {
    setScanning(true)
    setScanMessage(null)
    try {
      const result = await apiPost<{ scanned?: number; message?: string }>('/skill-runner/scan', {})
      setScanMessage(
        result.scanned !== undefined
          ? `Scan complete: ${result.scanned} skills found`
          : result.message || 'Scan complete',
      )
      await refresh()
    } catch (err) {
      setScanMessage(err instanceof Error ? err.message : 'Scan failed')
    } finally {
      setScanning(false)
      setTimeout(() => setScanMessage(null), 4000)
    }
  }

  // Open detail modal
  const handleViewSkill = async (skill: Skill) => {
    try {
      const res = await apiGet<{ skill: SkillRunnerResponse['skills'][0]; content?: string }>(
        `/skill-runner/${encodeURIComponent(skill.name)}`
      )
      setSelectedSkill({
        ...skill,
        description: res.skill?.description || skill.description,
        metadata: res.skill?.frontmatter as Record<string, unknown> | undefined,
      })
    } catch {
      setSelectedSkill(skill)
    }
    setInvokeParams({})
    setTaskDescription('')
    setInvokeResult(null)
    setInvokeError(null)
    setDetailModalOpen(true)
  }

  // Close detail modal
  const handleCloseDetail = () => {
    setDetailModalOpen(false)
    setSelectedSkill(null)
    setInvokeResult(null)
    setInvokeError(null)
  }

  // Invoke skill
  const handleInvoke = async () => {
    if (!selectedSkill) return
    setInvoking(true)
    setInvokeResult(null)
    setInvokeError(null)
    try {
      // Backend expects {skill_name, instruction}
      const instruction = taskDescription.trim()
        || Object.values(invokeParams).filter(Boolean).join('; ')
      const result = await apiPost<SkillInvokeResult>('/skill-runner/invoke', {
        skill_name: selectedSkill.name,
        instruction,
      })
      setInvokeResult(result)
    } catch (err) {
      setInvokeError(err instanceof Error ? err.message : 'Invoke failed')
    } finally {
      setInvoking(false)
    }
  }

  // Resolve category badge variant
  const categoryVariant = (category?: string): 'info' | 'purple' | 'success' | 'warning' | 'default' => {
    if (!category) return 'default'
    const lower = category.toLowerCase()
    if (lower.includes('research') || lower.includes('search')) return 'info'
    if (lower.includes('code') || lower.includes('dev')) return 'purple'
    if (lower.includes('data') || lower.includes('analyst')) return 'success'
    if (lower.includes('admin') || lower.includes('system')) return 'warning'
    return 'default'
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6 animate-fade-in">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'w-10 h-10 rounded-[12px] flex items-center justify-center',
                'bg-purple-50 dark:bg-purple-900/30',
              )}
            >
              <span className="text-lg" role="img" aria-label="Skills">
                🧩
              </span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-display text-[20px] font-semibold text-text-dark dark:text-gray-100">
                  Skills Hub
                </h2>
                {skills && (
                  <Badge variant="purple" size="sm">
                    {skills.length} skills
                  </Badge>
                )}
              </div>
              <p className="text-[13px] text-text-muted dark:text-gray-400">
                Browse and invoke available skills
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {scanMessage && (
              <span className="text-xs text-text-muted dark:text-gray-400 animate-fade-in">
                {scanMessage}
              </span>
            )}
            <Button
              variant="secondary"
              size="sm"
              loading={scanning}
              onClick={handleScan}
              icon={
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
                  />
                </svg>
              }
            >
              Scan Skills
            </Button>
          </div>
        </div>

        {/* Search */}
        <div className="mb-6">
          <Input
            variant="search"
            placeholder="Search skills by name, category, or description..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            clearable
            onClear={() => setSearch('')}
          />
        </div>

        {/* Error state */}
        {error && (
          <Card variant="default" padding="md" className="mb-6 border-red-200 dark:border-red-800">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <Button variant="ghost" size="sm" onClick={refresh} className="mt-2">
              Retry
            </Button>
          </Card>
        )}

        {/* Loading skeleton */}
        {loading && !skills && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkillCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && skills && filteredSkills.length === 0 && (
          <EmptyState
            icon="🧩"
            title={search ? 'No skills match your search' : 'No skills available'}
            description={
              search
                ? 'Try adjusting your search query'
                : 'Click "Scan Skills" to discover available skills'
            }
            action={
              search
                ? { label: 'Clear search', onClick: () => setSearch('') }
                : { label: 'Scan Skills', onClick: handleScan }
            }
          />
        )}

        {/* Skills grid */}
        {filteredSkills.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 animate-fade-in">
            {filteredSkills.map((skill) => (
              <Card
                key={skill.name}
                variant="interactive"
                padding="md"
                className="flex flex-col"
              >
                <div className="flex-1">
                  <h3 className="text-sm font-semibold text-text-dark dark:text-gray-100 mb-1">
                    {skill.name}
                  </h3>
                  <p className="text-xs text-text-muted dark:text-gray-400 line-clamp-2 mb-3">
                    {skill.description || 'No description'}
                  </p>
                </div>
                <div className="flex items-center justify-between mt-auto pt-2">
                  <div className="flex items-center gap-1.5">
                    {(skill.category || skill.type) && (
                      <Badge variant={categoryVariant(skill.category || skill.type)} size="sm">
                        {skill.category || skill.type}
                      </Badge>
                    )}
                    {skill.invokable && (
                      <Badge variant="success" size="sm">
                        invokable
                      </Badge>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleViewSkill(skill)}
                  >
                    View
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}

        {/* Detail Modal */}
        <Modal
          open={detailModalOpen}
          onClose={handleCloseDetail}
          title={selectedSkill?.name || 'Skill Detail'}
          size="lg"
        >
          {selectedSkill && (
            <div className="space-y-5">
              {/* Description */}
              <div>
                <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-1">
                  Description
                </h4>
                <p className="text-sm text-text-primary dark:text-gray-200">
                  {selectedSkill.description || 'No description available'}
                </p>
              </div>

              {/* Category / Type */}
              <div className="flex gap-2">
                {selectedSkill.category && (
                  <Badge variant={categoryVariant(selectedSkill.category)} size="md">
                    {selectedSkill.category}
                  </Badge>
                )}
                {selectedSkill.type && selectedSkill.type !== selectedSkill.category && (
                  <Badge variant="default" size="md">
                    {selectedSkill.type}
                  </Badge>
                )}
              </div>

              {/* Commands */}
              {selectedSkill.commands && selectedSkill.commands.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Commands
                  </h4>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedSkill.commands.map((cmd) => (
                      <code
                        key={cmd}
                        className={cn(
                          'text-xs px-2 py-1 rounded-md font-mono',
                          'bg-gray-100 text-gray-700',
                          'dark:bg-[#2a2a45] dark:text-gray-300',
                        )}
                      >
                        {cmd}
                      </code>
                    ))}
                  </div>
                </div>
              )}

              {/* Slash Commands */}
              {selectedSkill.slash_commands && selectedSkill.slash_commands.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Slash Commands
                  </h4>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedSkill.slash_commands.map((cmd) => (
                      <code
                        key={cmd}
                        className={cn(
                          'text-xs px-2 py-1 rounded-md font-mono',
                          'bg-blue-50 text-blue-700',
                          'dark:bg-blue-900/30 dark:text-blue-400',
                        )}
                      >
                        /{cmd}
                      </code>
                    ))}
                  </div>
                </div>
              )}

              {/* Parameters */}
              {selectedSkill.parameters && Object.keys(selectedSkill.parameters).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Parameters
                  </h4>
                  <div className="space-y-2">
                    {Object.entries(selectedSkill.parameters).map(([key, param]) => (
                      <div
                        key={key}
                        className={cn(
                          'p-3 rounded-lg border',
                          'bg-[#fafbfc] border-[#e5e7eb]',
                          'dark:bg-[#1a1a2e]/50 dark:border-[#2a2a45]',
                        )}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <code className="text-xs font-mono font-semibold text-text-dark dark:text-gray-200">
                            {key}
                          </code>
                          <Badge variant="default" size="sm">
                            {param.type}
                          </Badge>
                          {param.required && (
                            <Badge variant="error" size="sm">
                              required
                            </Badge>
                          )}
                        </div>
                        {param.description && (
                          <p className="text-xs text-text-muted dark:text-gray-400">
                            {param.description}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Invoke Section */}
              {selectedSkill.invokable && (
                <div
                  className={cn(
                    'border-t pt-5',
                    'border-[#e5e7eb] dark:border-[#2a2a45]',
                  )}
                >
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-3">
                    Invoke Skill
                  </h4>

                  {/* Task description */}
                  <div className="mb-3">
                    <label className="block text-sm font-medium text-[#222] dark:text-gray-200 mb-1.5">
                      Task Description
                    </label>
                    <textarea
                      value={taskDescription}
                      onChange={(e) => setTaskDescription(e.target.value)}
                      rows={3}
                      placeholder="Describe what you want this skill to do..."
                      className={cn(
                        'w-full rounded-comfortable border bg-white text-sm p-3',
                        'border-[#e5e7eb] dark:border-[#2a2a45]',
                        'dark:bg-[#1a1a2e] dark:text-gray-100',
                        'placeholder:text-[#8e8e93] dark:placeholder:text-gray-500',
                        'focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 focus:outline-none',
                        'dark:focus:border-[#60a5fa] dark:focus:ring-[#60a5fa]/20',
                      )}
                    />
                  </div>

                  {/* Dynamic params */}
                  {selectedSkill.parameters && Object.keys(selectedSkill.parameters).length > 0 && (
                    <div className="space-y-2 mb-3">
                      {Object.entries(selectedSkill.parameters).map(([key, param]) => (
                        <Input
                          key={key}
                          label={`${key}${param.required ? ' *' : ''}`}
                          placeholder={param.description || `Enter ${key}`}
                          value={invokeParams[key] || ''}
                          onChange={(e) =>
                            setInvokeParams((prev) => ({
                              ...prev,
                              [key]: e.target.value,
                            }))
                          }
                        />
                      ))}
                    </div>
                  )}

                  <Button
                    variant="primary"
                    size="md"
                    loading={invoking}
                    onClick={handleInvoke}
                    disabled={!taskDescription.trim() && Object.values(invokeParams).every((v) => !v.trim())}
                    icon={
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347a1.125 1.125 0 01-1.667-.986V5.653z" />
                      </svg>
                    }
                  >
                    Invoke
                  </Button>

                  {/* Invoke Result */}
                  {invokeResult && (
                    <div
                      className={cn(
                        'mt-4 p-4 rounded-lg border',
                        invokeResult.status === 'success' || !invokeResult.error
                          ? 'bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-800'
                          : 'bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-800',
                      )}
                    >
                      <h5 className="text-xs font-semibold uppercase mb-2 text-text-muted dark:text-gray-400">
                        Result
                      </h5>
                      <pre className="text-xs text-text-primary dark:text-gray-200 whitespace-pre-wrap overflow-x-auto">
                        {JSON.stringify(invokeResult.result || invokeResult, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Invoke Error */}
                  {invokeError && (
                    <div className="mt-4 p-4 rounded-lg bg-red-50 border border-red-200 dark:bg-red-900/20 dark:border-red-800">
                      <p className="text-sm text-red-600 dark:text-red-400">{invokeError}</p>
                    </div>
                  )}
                </div>
              )}

              {/* Metadata */}
              {selectedSkill.metadata && Object.keys(selectedSkill.metadata).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider mb-2">
                    Metadata
                  </h4>
                  <pre
                    className={cn(
                      'text-xs p-3 rounded-lg overflow-x-auto',
                      'bg-gray-50 text-gray-700',
                      'dark:bg-[#1a1a2e] dark:text-gray-300',
                    )}
                  >
                    {JSON.stringify(selectedSkill.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </Modal>
      </div>
    </div>
  )
}
