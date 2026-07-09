// ============================================================
// MekongAI Nexus - Session Insights
// Analytics panel showing usage stats per session
// ============================================================

import { useState, useEffect, useMemo, useCallback } from 'react'
import {
  BarChart3,
  Clock,
  MessageSquare,
  Bot,
  Wrench,
  Users,
  Swords,
  CheckCircle2,
  XCircle,
  Trophy,
  Zap,
  Loader2,
  AlertTriangle,
} from 'lucide-react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { cn } from '../../lib/cn'
import { Modal } from '../ui/Modal'
import { Card } from '../ui/Card'
import { Tabs } from '../ui/Tabs'
import { Badge } from '../ui/Badge'
import { getSessionStats, getSessionTimeline } from '../../api'
import type { SessionStats, ActionEntry } from '../../api'
import { format } from 'date-fns'

// ============================================================
// Types
// ============================================================

export interface SessionInsightsProps {
  sessionId: string
  onClose: () => void
}

interface ComputedInsights {
  totalMessages: number
  userMessages: number
  assistantMessages: number
  totalDuration: string
  toolsUsed: Array<{ name: string; count: number }>
  agentsSpawned: number
  debateRounds: number
  tasksCompleted: number
  tasksFailed: number
  mostActiveAgent: string | null
  avgResponseTime: string
  messagesOverTime: Array<{ time: string; count: number }>
}

// ============================================================
// Color palette for charts
// ============================================================

const CHART_COLORS = [
  '#6366f1', '#8b5cf6', '#a78bfa', '#c4b5fd',
  '#818cf8', '#7c3aed', '#5b21b6', '#4338ca',
  '#6d28d9', '#9333ea',
]

// ============================================================
// Stat card
// ============================================================

interface StatCardProps {
  icon: typeof MessageSquare
  label: string
  value: string | number
  color?: string
}

function StatCard({ icon: Icon, label, value, color = 'text-brand-500' }: StatCardProps) {
  return (
    <Card variant="default" padding="sm" className="flex items-center gap-3">
      <div className={cn('rounded-lg p-2', color.replace('text-', 'bg-').replace('500', '100'), 'dark:bg-opacity-20')}>
        <Icon className={cn('h-4 w-4', color)} />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-text-muted dark:text-gray-500">{label}</p>
        <p className="text-sm font-semibold text-text-primary dark:text-gray-100">{value}</p>
      </div>
    </Card>
  )
}

// ============================================================
// Compute insights from stats + timeline
// ============================================================

function computeInsights(
  stats: SessionStats | null,
  timeline: ActionEntry[],
): ComputedInsights {
  // Message counts from timeline
  const messageLikeActions = timeline.filter(
    (e) => e.action_type === 'user_message' || e.action_type === 'assistant_message' || e.action_type === 'llm_response',
  )
  const userMessages = timeline.filter((e) => e.action_type === 'user_message').length
  const assistantMessages = timeline.filter(
    (e) => e.action_type === 'assistant_message' || e.action_type === 'llm_response',
  ).length

  // Duration
  const durationMs = stats?.duration_ms ?? 0
  const durationSec = Math.floor(durationMs / 1000)
  const mins = Math.floor(durationSec / 60)
  const secs = durationSec % 60
  const totalDuration = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`

  // Tools used
  const toolMap = new Map<string, number>()
  for (const entry of timeline) {
    if (entry.action_type === 'tool_call' || entry.action_type === 'tool_start') {
      const toolName = (entry.details.tool_name as string) || entry.content.split(' ')[0] || 'unknown'
      toolMap.set(toolName, (toolMap.get(toolName) ?? 0) + 1)
    }
  }
  const toolsUsed = Array.from(toolMap.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)

  // Agents
  const agentSet = new Set<string>()
  for (const entry of timeline) {
    if (entry.agent_name) agentSet.add(entry.agent_name)
  }
  const agentsSpawned = stats?.agents_involved?.length ?? agentSet.size

  // Debate rounds
  const debateRounds = timeline.filter(
    (e) => e.action_type === 'swarm_round' || e.action_type === 'debate_round',
  ).length

  // Tasks
  const tasksCompleted = timeline.filter((e) => e.action_type === 'task_completed').length
  const tasksFailed = timeline.filter((e) => e.action_type === 'task_failed').length

  // Most active agent
  const agentCounts = new Map<string, number>()
  for (const entry of timeline) {
    if (entry.agent_name) {
      agentCounts.set(entry.agent_name, (agentCounts.get(entry.agent_name) ?? 0) + 1)
    }
  }
  let mostActiveAgent: string | null = null
  let maxCount = 0
  for (const [name, count] of agentCounts) {
    if (count > maxCount) {
      maxCount = count
      mostActiveAgent = name
    }
  }

  // Average response time
  const durations = timeline
    .filter((e) => e.duration_ms !== undefined && e.duration_ms > 0)
    .map((e) => e.duration_ms!)
  const avgMs = durations.length > 0
    ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
    : 0
  const avgResponseTime = avgMs > 1000 ? `${(avgMs / 1000).toFixed(1)}s` : `${avgMs}ms`

  // Messages over time — group by minute
  const timeMap = new Map<string, number>()
  for (const entry of messageLikeActions) {
    try {
      const timeKey = format(new Date(entry.timestamp), 'HH:mm')
      timeMap.set(timeKey, (timeMap.get(timeKey) ?? 0) + 1)
    } catch {
      // Skip invalid timestamps
    }
  }
  const messagesOverTime = Array.from(timeMap.entries())
    .map(([time, count]) => ({ time, count }))
    .sort((a, b) => a.time.localeCompare(b.time))

  return {
    totalMessages: userMessages + assistantMessages,
    userMessages,
    assistantMessages,
    totalDuration,
    toolsUsed,
    agentsSpawned,
    debateRounds,
    tasksCompleted,
    tasksFailed,
    mostActiveAgent,
    avgResponseTime,
    messagesOverTime,
  }
}

// ============================================================
// Custom tooltip for charts
// ============================================================

interface ChartTooltipPayload {
  name: string
  value: number
  payload: Record<string, unknown>
}

interface ChartTooltipProps {
  active?: boolean
  payload?: ChartTooltipPayload[]
  label?: string
}

function ChartTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="rounded-lg border border-border bg-white px-3 py-2 shadow-card dark:border-[#2a2a45] dark:bg-[#1a1a2e]">
      <p className="text-xs font-medium text-text-primary dark:text-gray-100">{label}</p>
      {payload.map((entry, i) => (
        <p key={i} className="text-xs text-text-muted dark:text-gray-400">
          {entry.name}: {entry.value}
        </p>
      ))}
    </div>
  )
}

// ============================================================
// SessionInsights component
// ============================================================

export function SessionInsights({ sessionId, onClose }: SessionInsightsProps) {
  const [stats, setStats] = useState<SessionStats | null>(null)
  const [timeline, setTimeline] = useState<ActionEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('overview')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsResult, timelineResult] = await Promise.all([
        getSessionStats(sessionId),
        getSessionTimeline(sessionId),
      ])
      setStats(statsResult)
      setTimeline(timelineResult)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load insights'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    void fetchData()
  }, [fetchData])

  const insights = useMemo(
    () => computeInsights(stats, timeline),
    [stats, timeline],
  )

  const tabs = [
    { id: 'overview', label: 'Overview', icon: <BarChart3 className="h-4 w-4" /> },
    { id: 'charts', label: 'Charts', icon: <Zap className="h-4 w-4" /> },
  ]

  return (
    <Modal open onClose={onClose} title="Session Insights" size="lg">
      {loading ? (
        <div className="flex flex-col items-center justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-brand-500" />
          <p className="mt-3 text-sm text-text-muted dark:text-gray-500">Loading insights…</p>
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-16">
          <AlertTriangle className="h-8 w-8 text-amber-500" />
          <p className="mt-3 text-sm text-text-muted dark:text-gray-500">{error}</p>
          <button
            type="button"
            onClick={() => void fetchData()}
            className="mt-3 text-sm font-medium text-brand-500 hover:text-brand-600"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="space-y-5">
          <Tabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} variant="pill" size="sm" />

          {activeTab === 'overview' && (
            <div className="space-y-4">
              {/* Stats grid */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <StatCard icon={MessageSquare} label="Total Messages" value={insights.totalMessages} color="text-blue-500" />
                <StatCard icon={Clock} label="Duration" value={insights.totalDuration} color="text-emerald-500" />
                <StatCard icon={Wrench} label="Tools Used" value={stats?.total_tool_calls ?? insights.toolsUsed.reduce((s, t) => s + t.count, 0)} color="text-purple-500" />
                <StatCard icon={Bot} label="Agents" value={insights.agentsSpawned} color="text-indigo-500" />
                <StatCard icon={Swords} label="Debate Rounds" value={insights.debateRounds} color="text-amber-500" />
                <StatCard icon={Zap} label="Avg Response" value={insights.avgResponseTime} color="text-cyan-500" />
              </div>

              {/* Tasks row */}
              {(insights.tasksCompleted > 0 || insights.tasksFailed > 0) && (
                <div className="flex items-center gap-3">
                  {insights.tasksCompleted > 0 && (
                    <Badge variant="success" dot size="md">
                      <CheckCircle2 className="mr-1 h-3 w-3" />
                      {insights.tasksCompleted} completed
                    </Badge>
                  )}
                  {insights.tasksFailed > 0 && (
                    <Badge variant="error" dot size="md">
                      <XCircle className="mr-1 h-3 w-3" />
                      {insights.tasksFailed} failed
                    </Badge>
                  )}
                </div>
              )}

              {/* Most active agent */}
              {insights.mostActiveAgent && (
                <Card variant="interactive" padding="sm" className="flex items-center gap-3">
                  <Trophy className="h-5 w-5 text-amber-500" />
                  <div>
                    <p className="text-xs text-text-muted dark:text-gray-500">Most Active Agent</p>
                    <p className="text-sm font-semibold text-text-primary dark:text-gray-100">
                      {insights.mostActiveAgent}
                    </p>
                  </div>
                </Card>
              )}

              {/* Agents involved list */}
              {stats?.agents_involved && stats.agents_involved.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
                    Agents Involved
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {stats.agents_involved.map((name) => (
                      <Badge key={name} variant="purple" size="sm">
                        <Bot className="mr-1 h-3 w-3" />
                        {name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Action type breakdown */}
              {stats?.action_types && Object.keys(stats.action_types).length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
                    Action Types
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(stats.action_types)
                      .sort(([, a], [, b]) => b - a)
                      .slice(0, 8)
                      .map(([type, count]) => (
                        <div
                          key={type}
                          className="flex items-center justify-between rounded-md bg-gray-50 px-3 py-1.5 dark:bg-white/5"
                        >
                          <span className="text-xs text-text-secondary dark:text-gray-400">
                            {type.replace(/_/g, ' ')}
                          </span>
                          <span className="text-xs font-semibold text-text-primary dark:text-gray-200">
                            {count}
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'charts' && (
            <div className="space-y-6">
              {/* Messages over time */}
              <div>
                <p className="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
                  Messages Over Time
                </p>
                {insights.messagesOverTime.length > 0 ? (
                  <div className="h-48">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={insights.messagesOverTime}>
                        <XAxis
                          dataKey="time"
                          tick={{ fontSize: 10, fill: '#888' }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: '#888' }}
                          tickLine={false}
                          axisLine={false}
                          allowDecimals={false}
                        />
                        <Tooltip content={<ChartTooltip />} />
                        <Bar dataKey="count" name="Messages" fill="#6366f1" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <Card variant="default" padding="md" className="text-center">
                    <p className="text-sm text-text-muted dark:text-gray-500">No time-series data available</p>
                  </Card>
                )}
              </div>

              {/* Tool usage distribution */}
              <div>
                <p className="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
                  Tool Usage Distribution
                </p>
                {insights.toolsUsed.length > 0 ? (
                  <div className="flex items-center gap-4">
                    <div className="h-48 w-48 shrink-0">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={insights.toolsUsed}
                            dataKey="count"
                            nameKey="name"
                            cx="50%"
                            cy="50%"
                            outerRadius={70}
                            innerRadius={35}
                            paddingAngle={2}
                          >
                            {insights.toolsUsed.map((_, i) => (
                              <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip content={<ChartTooltip />} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="flex-1 space-y-1.5">
                      {insights.toolsUsed.slice(0, 6).map((tool, i) => (
                        <div key={tool.name} className="flex items-center gap-2">
                          <span
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }}
                          />
                          <span className="flex-1 truncate text-xs text-text-secondary dark:text-gray-400">
                            {tool.name}
                          </span>
                          <span className="text-xs font-medium text-text-primary dark:text-gray-200">
                            {tool.count}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <Card variant="default" padding="md" className="text-center">
                    <p className="text-sm text-text-muted dark:text-gray-500">No tool usage data</p>
                  </Card>
                )}
              </div>

              {/* Message breakdown */}
              <Card variant="default" padding="sm">
                <p className="mb-2 text-xs font-medium text-text-muted dark:text-gray-500">Message Breakdown</p>
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <Users className="h-4 w-4 text-blue-500" />
                    <span className="text-sm text-text-primary dark:text-gray-200">
                      User: <strong>{insights.userMessages}</strong>
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Bot className="h-4 w-4 text-emerald-500" />
                    <span className="text-sm text-text-primary dark:text-gray-200">
                      Assistant: <strong>{insights.assistantMessages}</strong>
                    </span>
                  </div>
                </div>
                {/* Progress bar */}
                {insights.totalMessages > 0 && (
                  <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
                    <div
                      className="bg-blue-500 transition-all"
                      style={{ width: `${(insights.userMessages / insights.totalMessages) * 100}%` }}
                    />
                    <div
                      className="bg-emerald-500 transition-all"
                      style={{ width: `${(insights.assistantMessages / insights.totalMessages) * 100}%` }}
                    />
                  </div>
                )}
              </Card>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
