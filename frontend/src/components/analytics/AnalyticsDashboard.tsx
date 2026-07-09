// ============================================================
// MekongAI Nexus - Analytics Dashboard
// Token usage charts, model breakdown, cost tracking
// Pure SVG charts — no external chart libraries
// ============================================================

import { useState, useCallback, useEffect, useRef, type ReactNode } from 'react'
import { getAnalytics } from '../../api'
import type {
  AnalyticsData,
  DailyUsage,
  ModelBreakdown,
  HourlyPattern,
  TopSession,
} from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { POLLING_INTERVALS } from '../../lib/constants'
import { cn } from '../../lib/cn'
import { Spinner } from '../ui/Spinner'

// ------------------------------------------------------------------
// Constants
// ------------------------------------------------------------------

const PERIODS = ['1d', '7d', '30d', '90d'] as const
type Period = typeof PERIODS[number]

const MODEL_COLORS: Record<string, string> = {
  'gpt-4o': '#3b82f6',
  'gpt-4o-mini': '#60a5fa',
  'claude-sonnet-4-20250514': '#a855f7',
  'claude-haiku-3': '#c084fc',
  'gemini-2.0-flash': '#10b981',
  unknown: '#6b7280',
}

const FALLBACK_COLORS = ['#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#14b8a6', '#8b5cf6']

function getModelColor(model: string, index: number): string {
  return MODEL_COLORS[model] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length]
}

// ------------------------------------------------------------------
// Count-up hook (reused from StatsCard pattern)
// ------------------------------------------------------------------

function useCountUp(target: number, duration = 800): number {
  const [current, setCurrent] = useState(0)
  const rafRef = useRef<number | null>(null)
  const startTimeRef = useRef<number | null>(null)
  const prevTargetRef = useRef(0)

  useEffect(() => {
    const startValue = prevTargetRef.current
    prevTargetRef.current = target
    startTimeRef.current = null

    const animate = (timestamp: number) => {
      if (startTimeRef.current === null) {
        startTimeRef.current = timestamp
      }
      const elapsed = timestamp - startTimeRef.current
      const progress = Math.min(elapsed / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      const value = Math.round(startValue + (target - startValue) * eased)
      setCurrent(value)
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [target, duration])

  return current
}

// ------------------------------------------------------------------
// Format helpers
// ------------------------------------------------------------------

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString('en-US')
}

function formatCost(cost: number): string {
  if (cost === 0) return '$0.00'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
}

function shortDate(dateStr: string): string {
  const parts = dateStr.split('-')
  if (parts.length === 3) return `${parts[1]}/${parts[2]}`
  return dateStr
}

// ------------------------------------------------------------------
// KPI Card
// ------------------------------------------------------------------

interface KpiCardProps {
  icon: string
  label: string
  value: number
  format?: (n: number) => string
  gradient: string
}

function KpiCard({ icon, label, value, format, gradient }: KpiCardProps) {
  const animated = useCountUp(value)
  const display = format ? format(animated) : animated.toLocaleString('en-US')

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-generous p-5',
        'bg-gradient-to-br text-white shadow-lg',
        'dark:shadow-none dark:brightness-90',
        'transition-transform duration-200 hover:scale-[1.02]',
        'animate-scale-in',
        gradient,
      )}
    >
      <div
        className="absolute top-3 right-3 text-4xl opacity-20 select-none pointer-events-none"
        aria-hidden="true"
      >
        {icon}
      </div>
      <div className="text-3xl font-bold font-display tabular-nums font-data">
        {display}
      </div>
      <div className="text-sm font-medium opacity-90 mt-1">{label}</div>
    </div>
  )
}

// ------------------------------------------------------------------
// Bar Chart — Daily Token Usage
// ------------------------------------------------------------------

interface BarChartProps {
  data: DailyUsage[]
}

function BarChart({ data }: BarChartProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)

  if (data.length === 0) {
    return <EmptyChartMessage message="No daily usage data" />
  }

  const maxTokens = Math.max(...data.map((d) => d.tokens), 1)
  const width = 100
  const height = 60
  const barPad = 1
  const barWidth = Math.max((width - barPad * data.length) / data.length, 2)

  // Y-axis labels
  const ySteps = 4
  const yLabels = Array.from({ length: ySteps + 1 }, (_, i) =>
    formatNumber(Math.round((maxTokens / ySteps) * (ySteps - i))),
  )

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height + 12}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Daily token usage bar chart"
      >
        <defs>
          <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" />
            <stop offset="100%" stopColor="#1d4ed8" />
          </linearGradient>
        </defs>

        {/* Grid lines */}
        {Array.from({ length: ySteps + 1 }, (_, i) => {
          const y = (i / ySteps) * height
          return (
            <line
              key={`grid-${i}`}
              x1={0}
              x2={width}
              y1={y}
              y2={y}
              stroke="currentColor"
              strokeOpacity={0.08}
              strokeWidth={0.2}
            />
          )
        })}

        {/* Bars */}
        {data.map((d, i) => {
          const barH = (d.tokens / maxTokens) * height
          const x = i * (barWidth + barPad)
          const y = height - barH
          const isHovered = hoveredIdx === i

          return (
            <g
              key={d.date}
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
              className="cursor-pointer"
            >
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={barH}
                rx={0.8}
                fill="url(#barGrad)"
                opacity={isHovered ? 1 : 0.85}
                className="transition-opacity duration-150"
              />
              {/* X-axis label */}
              {(data.length <= 14 || i % Math.ceil(data.length / 10) === 0) && (
                <text
                  x={x + barWidth / 2}
                  y={height + 5}
                  textAnchor="middle"
                  className="fill-current text-text-muted dark:text-gray-500"
                  fontSize={2.5}
                >
                  {shortDate(d.date)}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* Tooltip */}
      {hoveredIdx !== null && data[hoveredIdx] && (
        <div className="absolute top-0 left-1/2 -translate-x-1/2 bg-gray-900 dark:bg-gray-800 text-white text-xs px-3 py-2 rounded-comfortable shadow-lg pointer-events-none z-10 animate-fade-in">
          <div className="font-semibold">{data[hoveredIdx].date}</div>
          <div className="font-data tabular-nums">{formatNumber(data[hoveredIdx].tokens)} tokens</div>
          <div className="font-data tabular-nums text-gray-300">{formatCost(data[hoveredIdx].cost)}</div>
          <div className="text-gray-400">{data[hoveredIdx].messages} messages</div>
        </div>
      )}

      {/* Y-axis labels (outside SVG for better text rendering) */}
      <div className="absolute top-0 left-0 h-full flex flex-col justify-between pointer-events-none">
        {yLabels.map((label, i) => (
          <span
            key={`y-${i}`}
            className="text-[9px] font-data text-text-muted dark:text-gray-600 tabular-nums leading-none"
          >
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------
// Donut Chart — Model Breakdown
// ------------------------------------------------------------------

interface DonutChartProps {
  data: ModelBreakdown[]
}

function DonutChart({ data }: DonutChartProps) {
  const [hoveredModel, setHoveredModel] = useState<string | null>(null)

  if (data.length === 0) {
    return <EmptyChartMessage message="No model data" />
  }

  const total = data.reduce((s, d) => s + d.tokens, 0) || 1
  const cx = 50
  const cy = 50
  const outerR = 42
  const innerR = 28

  // Build arc segments
  let currentAngle = -90 // Start from top
  const segments = data.map((d, i) => {
    const angle = (d.tokens / total) * 360
    const startAngle = currentAngle
    const endAngle = currentAngle + angle
    currentAngle = endAngle

    const startRad = (startAngle * Math.PI) / 180
    const endRad = (endAngle * Math.PI) / 180

    const x1Outer = cx + outerR * Math.cos(startRad)
    const y1Outer = cy + outerR * Math.sin(startRad)
    const x2Outer = cx + outerR * Math.cos(endRad)
    const y2Outer = cy + outerR * Math.sin(endRad)

    const x1Inner = cx + innerR * Math.cos(endRad)
    const y1Inner = cy + innerR * Math.sin(endRad)
    const x2Inner = cx + innerR * Math.cos(startRad)
    const y2Inner = cy + innerR * Math.sin(startRad)

    const largeArc = angle > 180 ? 1 : 0

    const pathD = [
      `M ${x1Outer} ${y1Outer}`,
      `A ${outerR} ${outerR} 0 ${largeArc} 1 ${x2Outer} ${y2Outer}`,
      `L ${x1Inner} ${y1Inner}`,
      `A ${innerR} ${innerR} 0 ${largeArc} 0 ${x2Inner} ${y2Inner}`,
      'Z',
    ].join(' ')

    const color = getModelColor(d.model, i)
    const isHovered = hoveredModel === d.model

    return { pathD, color, isHovered, model: d.model, percentage: d.percentage, tokens: d.tokens }
  })

  return (
    <div className="flex flex-col items-center gap-4">
      <svg
        viewBox="0 0 100 100"
        className="w-40 h-40 sm:w-48 sm:h-48"
        role="img"
        aria-label="Model breakdown donut chart"
      >
        {segments.map((seg) => (
          <path
            key={seg.model}
            d={seg.pathD}
            fill={seg.color}
            opacity={seg.isHovered ? 1 : 0.85}
            stroke="white"
            strokeWidth={0.5}
            className="transition-opacity duration-150 cursor-pointer dark:stroke-[#0f0f17]"
            onMouseEnter={() => setHoveredModel(seg.model)}
            onMouseLeave={() => setHoveredModel(null)}
          />
        ))}
        {/* Center text */}
        <text
          x={cx}
          y={cy - 3}
          textAnchor="middle"
          className="fill-current text-text-dark dark:text-gray-200"
          fontSize={8}
          fontWeight="bold"
        >
          {formatNumber(total)}
        </text>
        <text
          x={cx}
          y={cy + 5}
          textAnchor="middle"
          className="fill-current text-text-muted dark:text-gray-500"
          fontSize={4}
        >
          tokens
        </text>
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1">
        {data.map((d, i) => (
          <div
            key={d.model}
            className={cn(
              'flex items-center gap-1.5 text-xs transition-opacity',
              hoveredModel && hoveredModel !== d.model ? 'opacity-40' : '',
            )}
            onMouseEnter={() => setHoveredModel(d.model)}
            onMouseLeave={() => setHoveredModel(null)}
          >
            <span
              className="w-2.5 h-2.5 rounded-full shrink-0"
              style={{ backgroundColor: getModelColor(d.model, i) }}
            />
            <span className="text-text-secondary dark:text-gray-400 truncate max-w-[120px]">
              {d.model}
            </span>
            <span className="font-data font-semibold text-text-dark dark:text-gray-200 tabular-nums">
              {d.percentage}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------
// Area Chart — Hourly Pattern
// ------------------------------------------------------------------

interface AreaChartProps {
  data: HourlyPattern[]
}

function AreaChart({ data }: AreaChartProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)

  if (data.length === 0) {
    return <EmptyChartMessage message="No hourly data" />
  }

  const maxTokens = Math.max(...data.map((d) => d.tokens), 1)
  const width = 100
  const height = 50
  const padY = 2

  const points = data.map((d, i) => ({
    x: (i / (data.length - 1)) * width,
    y: height - padY - ((d.tokens / maxTokens) * (height - padY * 2)),
    hour: d.hour,
    tokens: d.tokens,
  }))

  // Build SVG path
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
  const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height + 10}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Hourly usage pattern area chart"
      >
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.05} />
          </linearGradient>
        </defs>

        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
          const y = padY + (1 - frac) * (height - padY * 2)
          return (
            <line
              key={`hgrid-${frac}`}
              x1={0} x2={width}
              y1={y} y2={y}
              stroke="currentColor"
              strokeOpacity={0.06}
              strokeWidth={0.2}
            />
          )
        })}

        {/* Area fill */}
        <path d={areaPath} fill="url(#areaGrad)" />

        {/* Line */}
        <path
          d={linePath}
          fill="none"
          stroke="#8b5cf6"
          strokeWidth={0.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Data points */}
        {points.map((p, i) => (
          <circle
            key={`pt-${i}`}
            cx={p.x}
            cy={p.y}
            r={hoveredIdx === i ? 1.8 : 0.8}
            fill={hoveredIdx === i ? '#8b5cf6' : 'white'}
            stroke="#8b5cf6"
            strokeWidth={0.4}
            className="transition-all duration-150 cursor-pointer"
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
          />
        ))}

        {/* X-axis labels (every 4 hours) */}
        {points
          .filter((_, i) => i % 4 === 0)
          .map((p) => (
            <text
              key={`xl-${p.hour}`}
              x={p.x}
              y={height + 6}
              textAnchor="middle"
              className="fill-current text-text-muted dark:text-gray-500"
              fontSize={2.5}
            >
              {`${p.hour}:00`}
            </text>
          ))}

        {/* Hover crosshair */}
        {hoveredIdx !== null && points[hoveredIdx] && (
          <line
            x1={points[hoveredIdx].x}
            x2={points[hoveredIdx].x}
            y1={0}
            y2={height}
            stroke="#8b5cf6"
            strokeWidth={0.3}
            strokeDasharray="1 1"
            opacity={0.5}
          />
        )}
      </svg>

      {/* Tooltip */}
      {hoveredIdx !== null && data[hoveredIdx] && (
        <div className="absolute top-0 left-1/2 -translate-x-1/2 bg-gray-900 dark:bg-gray-800 text-white text-xs px-3 py-2 rounded-comfortable shadow-lg pointer-events-none z-10 animate-fade-in">
          <div className="font-semibold">{data[hoveredIdx].hour}:00</div>
          <div className="font-data tabular-nums">{formatNumber(data[hoveredIdx].tokens)} tokens</div>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------
// Top Sessions Table
// ------------------------------------------------------------------

interface TopSessionsTableProps {
  data: TopSession[]
}

function TopSessionsTable({ data }: TopSessionsTableProps) {
  if (data.length === 0) {
    return <EmptyChartMessage message="No session data" />
  }

  const maxTokens = data[0]?.tokens || 1

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-text-muted dark:text-gray-500 text-xs uppercase tracking-wider">
            <th className="pb-2 font-medium">#</th>
            <th className="pb-2 font-medium">Session</th>
            <th className="pb-2 font-medium text-right">Tokens</th>
            <th className="pb-2 font-medium text-right">Msgs</th>
            <th className="pb-2 font-medium" style={{ width: '30%' }}></th>
          </tr>
        </thead>
        <tbody>
          {data.map((s, i) => (
            <tr
              key={s.session_id}
              className="border-t border-border-light dark:border-[#2a2a45] hover:bg-surface-secondary/50 dark:hover:bg-[#1a1a2e]/50 transition-colors"
            >
              <td className="py-2 pr-2 text-text-muted dark:text-gray-500 font-data tabular-nums">
                {i + 1}
              </td>
              <td className="py-2 pr-3 truncate max-w-[200px]">
                <span className="text-text-dark dark:text-gray-200" title={s.title}>
                  {s.title || s.session_id.slice(0, 12)}
                </span>
              </td>
              <td className="py-2 pr-2 text-right font-data tabular-nums text-text-secondary dark:text-gray-400">
                {formatNumber(s.tokens)}
              </td>
              <td className="py-2 pr-3 text-right font-data tabular-nums text-text-muted dark:text-gray-500">
                {s.messages}
              </td>
              <td className="py-2">
                <div className="h-1.5 bg-surface-secondary dark:bg-[#1a1a2e] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-blue-400 rounded-full transition-all duration-500"
                    style={{ width: `${Math.round((s.tokens / maxTokens) * 100)}%` }}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ------------------------------------------------------------------
// Shared: empty chart message
// ------------------------------------------------------------------

function EmptyChartMessage({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center h-32 text-text-muted dark:text-gray-500 text-sm">
      {message}
    </div>
  )
}

// ------------------------------------------------------------------
// Loading skeleton
// ------------------------------------------------------------------

function SkeletonCard() {
  return (
    <div className="rounded-generous p-5 bg-surface-secondary dark:bg-[#1a1a2e] animate-pulse">
      <div className="h-8 w-24 bg-gray-200 dark:bg-[#2a2a45] rounded mb-2" />
      <div className="h-4 w-16 bg-gray-200 dark:bg-[#2a2a45] rounded" />
    </div>
  )
}

function SkeletonChart() {
  return (
    <div className="rounded-generous p-5 bg-white dark:bg-[#12121f] border border-border-light dark:border-[#2a2a45] animate-pulse">
      <div className="h-4 w-32 bg-gray-200 dark:bg-[#2a2a45] rounded mb-4" />
      <div className="h-40 bg-gray-100 dark:bg-[#1a1a2e] rounded" />
    </div>
  )
}

// ------------------------------------------------------------------
// Period Selector
// ------------------------------------------------------------------

interface PeriodSelectorProps {
  value: Period
  onChange: (p: Period) => void
}

function PeriodSelector({ value, onChange }: PeriodSelectorProps) {
  return (
    <div className="flex items-center gap-[2px] bg-[#f4f5f7] dark:bg-[#1a1a2e] rounded-comfortable p-[3px]">
      {PERIODS.map((p) => (
        <button
          key={p}
          onClick={() => onChange(p)}
          className={cn(
            'px-3.5 py-[6px] rounded-[9px] text-[13px] font-medium transition-all duration-200',
            value === p
              ? 'bg-white dark:bg-[#2a2a45] text-text-dark dark:text-white shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
              : 'text-text-secondary dark:text-[#a0a0b8] hover:text-text-dark dark:hover:text-white',
          )}
        >
          {p}
        </button>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------
// Chart Card wrapper
// ------------------------------------------------------------------

interface ChartCardProps {
  title: string
  icon: string
  children: ReactNode
  className?: string
}

function ChartCard({ title, icon, children, className }: ChartCardProps) {
  return (
    <div
      className={cn(
        'rounded-generous p-5 bg-white dark:bg-[#12121f]',
        'border border-border-light dark:border-[#2a2a45]',
        'shadow-sm dark:shadow-none',
        'animate-slide-up',
        className,
      )}
    >
      <h3 className="text-sm font-semibold text-text-dark dark:text-gray-200 mb-4 flex items-center gap-2">
        <span>{icon}</span>
        {title}
      </h3>
      {children}
    </div>
  )
}

// ------------------------------------------------------------------
// Main Dashboard Component
// ------------------------------------------------------------------

export function AnalyticsDashboard() {
  const [period, setPeriod] = useState<Period>('7d')

  const fetcher = useCallback(
    () => getAnalytics(period),
    [period],
  )

  const { data, loading, error } = usePolling<AnalyticsData>({
    fetcher,
    interval: POLLING_INTERVALS.slow,
  })

  // Show skeleton while initial load
  if (loading && !data) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-2xl">📊</span>
              <h2 className="font-display text-2xl font-semibold text-text-dark dark:text-gray-100">
                Analytics
              </h2>
            </div>
            <PeriodSelector value={period} onChange={setPeriod} />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {Array.from({ length: 4 }, (_, i) => <SkeletonCard key={i} />)}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SkeletonChart />
            <SkeletonChart />
          </div>
        </div>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-500 dark:text-red-400 text-sm mb-2">Failed to load analytics</p>
          <p className="text-text-muted dark:text-gray-500 text-xs">{error}</p>
        </div>
      </div>
    )
  }

  const summary = data?.summary
  const dailyUsage = data?.daily_usage ?? []
  const modelBreakdown = data?.model_breakdown ?? []
  const topSessions = data?.top_sessions ?? []
  const hourlyPattern = data?.hourly_pattern ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between animate-fade-in">
          <div className="flex items-center gap-3">
            <span className="text-2xl">📊</span>
            <div>
              <h2 className="font-display text-2xl font-semibold text-text-dark dark:text-gray-100">
                Analytics
              </h2>
              <p className="text-xs text-text-muted dark:text-gray-500 mt-0.5">
                Token usage, cost tracking & model breakdown
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {loading && (
              <Spinner size="sm" className="text-brand-500" />
            )}
            <PeriodSelector value={period} onChange={setPeriod} />
          </div>
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard
            icon="🪙"
            label="Total Tokens"
            value={summary?.total_tokens ?? 0}
            format={formatNumber}
            gradient="from-blue-500 to-blue-600"
          />
          <KpiCard
            icon="💰"
            label="Estimated Cost"
            value={summary?.total_cost ?? 0}
            format={formatCost}
            gradient="from-emerald-500 to-emerald-600"
          />
          <KpiCard
            icon="💬"
            label="Sessions"
            value={summary?.total_sessions ?? 0}
            gradient="from-purple-500 to-purple-600"
          />
          <KpiCard
            icon="📝"
            label="Messages"
            value={summary?.total_messages ?? 0}
            gradient="from-amber-500 to-amber-600"
          />
        </div>

        {/* Charts Row 1 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ChartCard title="Daily Token Usage" icon="📈">
            <BarChart data={dailyUsage} />
          </ChartCard>
          <ChartCard title="Model Breakdown" icon="🧩">
            <DonutChart data={modelBreakdown} />
          </ChartCard>
        </div>

        {/* Charts Row 2 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ChartCard title="Hourly Usage Pattern" icon="⏰">
            <AreaChart data={hourlyPattern} />
          </ChartCard>
          <ChartCard title="Top Sessions" icon="🏆">
            <TopSessionsTable data={topSessions} />
          </ChartCard>
        </div>

        {/* Avg tokens per message footer */}
        {summary && summary.avg_tokens_per_message > 0 && (
          <div className="text-center text-xs text-text-muted dark:text-gray-500 animate-fade-in">
            Avg <span className="font-data font-semibold tabular-nums">{formatNumber(summary.avg_tokens_per_message)}</span> tokens/message
            &middot; Period: {data?.period ?? period}
          </div>
        )}
      </div>
    </div>
  )
}
