// ============================================================
// MekongAI Nexus - BuildProgress
// Phase tracker component showing project build progress
// with step indicators, stats, and error display.
// ============================================================

import { useProjectStore } from '../../stores/projectStore'
import type { BuildPhase } from '../../stores/projectStore'

// ============================================================
// Status visual config
// ============================================================

const STATUS_CONFIG: Record<
  BuildPhase['status'],
  { bg: string; text: string; ring: string }
> = {
  pending: { bg: 'bg-zinc-700', text: 'text-zinc-500', ring: '' },
  active: { bg: 'bg-blue-500 animate-pulse', text: 'text-blue-400', ring: 'ring-2 ring-blue-400/50' },
  done: { bg: 'bg-green-500', text: 'text-green-400', ring: '' },
  error: { bg: 'bg-red-500', text: 'text-red-400', ring: '' },
  skipped: { bg: 'bg-zinc-600', text: 'text-zinc-500', ring: '' },
}

// ============================================================
// PhaseStep — single step in the vertical pipeline
// ============================================================

interface PhaseStepProps {
  phase: BuildPhase
  isLast: boolean
}

function PhaseStep({ phase, isLast }: PhaseStepProps) {
  const config = STATUS_CONFIG[phase.status]

  // Determine the icon to display inside the circle
  const stepIcon =
    phase.status === 'done'
      ? '✓'
      : phase.status === 'error'
        ? '✕'
        : phase.status === 'skipped'
          ? '—'
          : phase.icon

  return (
    <div className="flex items-start gap-3" role="listitem">
      {/* Step indicator + connector line */}
      <div className="flex flex-col items-center">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${config.bg} ${config.ring}`}
          aria-label={`${phase.description}: ${phase.status}`}
        >
          {stepIcon}
        </div>
        {!isLast && (
          <div
            className={`w-0.5 h-6 mt-1 ${
              phase.status === 'done' ? 'bg-green-500/50' : 'bg-zinc-700'
            }`}
          />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 pb-4">
        <div className={`text-sm font-medium ${config.text}`}>
          {phase.description}
        </div>
        {phase.status === 'active' && (
          <div className="text-xs text-blue-400/70 mt-0.5">Đang xử lý...</div>
        )}
        {phase.status === 'error' && (
          <div className="text-xs text-red-400/70 mt-0.5">Lỗi xảy ra</div>
        )}
        {phase.status === 'done' && phase.completedAt && (
          <div className="text-xs text-zinc-600 mt-0.5">
            {new Date(phase.completedAt).toLocaleTimeString('vi-VN')}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// StatItem — single stat in the bottom grid
// ============================================================

interface StatItemProps {
  value: string
  label: string
  colorClass?: string
}

function StatItem({ value, label, colorClass = 'text-zinc-200' }: StatItemProps) {
  return (
    <div className="text-center">
      <div className={`text-lg font-bold ${colorClass}`}>{value}</div>
      <div className="text-xs text-zinc-500">{label}</div>
    </div>
  )
}

// ============================================================
// BuildProgress — main component
// ============================================================

export function BuildProgress() {
  const {
    isBuilding,
    phases,
    qualityScore,
    iterationCount,
    filesWritten,
    errorsFixed,
    error,
  } = useProjectStore()

  // Don't show if no build has started
  const hasActivity = phases.some((p) => p.status !== 'pending')
  if (!isBuilding && !hasActivity) return null

  const completedCount = phases.filter((p) => p.status === 'done').length
  const progressPct = (completedCount / phases.length) * 100

  // Quality score color
  const qualityColor =
    qualityScore >= 0.85
      ? 'text-green-400'
      : qualityScore >= 0.5
        ? 'text-yellow-400'
        : 'text-red-400'

  const showStats = filesWritten > 0 || iterationCount > 0 || qualityScore > 0

  return (
    <div className="bg-zinc-800/50 rounded-lg border border-zinc-700 p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
          🏗️ Project Builder
          {isBuilding && (
            <span className="text-xs text-blue-400 animate-pulse">
              Building...
            </span>
          )}
          {!isBuilding && hasActivity && completedCount === phases.length && (
            <span className="text-xs text-green-400">✓ Complete</span>
          )}
        </h3>
        <div className="text-xs text-zinc-500">
          {completedCount}/{phases.length} phases
        </div>
      </div>

      {/* Progress bar */}
      <div
        className="h-1.5 bg-zinc-700 rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={progressPct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Build progress"
      >
        <div
          className="h-full bg-gradient-to-r from-blue-500 to-green-500 rounded-full transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      {/* Phase steps */}
      <div className="pl-1" role="list" aria-label="Build phases">
        {phases.map((phase, idx) => (
          <PhaseStep
            key={phase.name}
            phase={phase}
            isLast={idx === phases.length - 1}
          />
        ))}
      </div>

      {/* Stats grid */}
      {showStats && (
        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-zinc-700">
          <StatItem
            value={String(filesWritten)}
            label="Files"
          />
          <StatItem
            value={String(iterationCount)}
            label="Iterations"
          />
          <StatItem
            value={`${(qualityScore * 100).toFixed(0)}%`}
            label="Quality"
            colorClass={qualityColor}
          />
        </div>
      )}

      {/* Errors fixed badge */}
      {errorsFixed > 0 && (
        <div className="flex items-center justify-center gap-1 text-xs text-amber-400/80 pt-1">
          <span>🔧</span>
          <span>{errorsFixed} lỗi đã sửa</span>
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="p-2 bg-red-500/10 border border-red-500/30 rounded text-xs text-red-400">
          <span className="font-medium">⚠️ Lỗi:</span> {error}
        </div>
      )}
    </div>
  )
}
