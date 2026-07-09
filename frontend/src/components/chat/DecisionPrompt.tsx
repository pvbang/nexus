// ============================================================
// DecisionPrompt - Interactive Decision UI
// Agent paused and needs user input to continue
// ============================================================

import { useState, useEffect, useCallback, useRef } from 'react'
import { Clock, CheckCircle, MessageSquareMore, Sparkles } from 'lucide-react'
import { cn } from '../../lib/cn'
import type { PendingDecision } from '../../api'
import { answerDecision } from '../../api'

interface DecisionPromptProps {
  decision: PendingDecision
  onResolved: () => void
}

/**
 * Calculate remaining seconds from expires_at timestamp.
 * Ensures timer is synced with server-side expiry.
 */
function calcRemainingSeconds(expiresAt: string, fallback: number): number {
  try {
    const expiresMs = new Date(expiresAt).getTime()
    const nowMs = Date.now()
    const remaining = Math.max(0, Math.floor((expiresMs - nowMs) / 1000))
    return remaining
  } catch {
    return fallback
  }
}

export function DecisionPrompt({ decision, onResolved }: DecisionPromptProps) {
  // Sync timer with server's expires_at (not raw timeout_seconds)
  const initialTimeLeft = decision.expires_at
    ? calcRemainingSeconds(decision.expires_at, decision.timeout_seconds)
    : decision.timeout_seconds

  const [timeLeft, setTimeLeft] = useState(initialTimeLeft)
  const [customValue, setCustomValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [selectedValue, setSelectedValue] = useState<string | null>(null)
  const [resolvedExternally, setResolvedExternally] = useState(false)

  // Use ref to track resolved state (accessible in timer closure)
  const resolvedRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Keep latest onResolved in ref to prevent stale closure in timer
  const onResolvedRef = useRef(onResolved)
  onResolvedRef.current = onResolved

  const handleSelect = useCallback(async (value: string) => {
    if (resolvedRef.current || submitting || selectedValue) return
    resolvedRef.current = true
    setSubmitting(true)
    setSelectedValue(value)

    // Stop timer immediately
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }

    try {
      const result = await answerDecision(decision.decision_id, value)
      // Handle both success and already_resolved gracefully
      if (result.status === 'ok' || result.status === 'already_resolved') {
        onResolved()
      }
    } catch (err) {
      console.error('[DecisionPrompt] Failed to submit answer:', err)
      // On error: still resolve the UI to prevent stuck state.
      // The server likely already resolved this decision.
      onResolved()
    }
  }, [decision.decision_id, submitting, selectedValue, onResolved])

  // Countdown timer
  useEffect(() => {
    timerRef.current = setInterval(() => {
      if (resolvedRef.current) {
        if (timerRef.current) {
          clearInterval(timerRef.current)
          timerRef.current = null
        }
        return
      }

      setTimeLeft((prev) => {
        if (prev <= 1) {
          // Auto-select first option
          if (!resolvedRef.current && decision.options.length > 0) {
            resolvedRef.current = true
            setTimeout(() => {
              answerDecision(decision.decision_id, decision.options[0].value)
                .then(() => onResolvedRef.current())
                .catch(() => {
                  // Server already resolved or expired — resolve UI anyway
                  onResolvedRef.current()
                })
            }, 0)
          }
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [decision.decision_id, decision.options])

  // Listen for external resolution (user_input_resolved SSE event)
  useEffect(() => {
    const handleResolved = (event: CustomEvent) => {
      if (event.detail?.decision_id === decision.decision_id) {
        if (!resolvedRef.current) {
          resolvedRef.current = true
          setResolvedExternally(true)
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          onResolved()
        }
      }
    }

    window.addEventListener('decision-resolved', handleResolved as EventListener)
    return () => {
      window.removeEventListener('decision-resolved', handleResolved as EventListener)
    }
  }, [decision.decision_id, onResolved])

  const handleCustomSubmit = useCallback(() => {
    if (!customValue.trim()) return
    handleSelect(customValue.trim())
  }, [customValue, handleSelect])

  // Timer progress (0-100, 100 = full time left)
  const timerPct = Math.round((timeLeft / (decision.timeout_seconds || 60)) * 100)
  const timerColor = timerPct > 50 ? 'text-emerald-600' : timerPct > 20 ? 'text-amber-500' : 'text-red-500'

  if (resolvedExternally) {
    return (
      <div className="flex items-start gap-3 animate-fade-in">
        <div className="w-8 h-8 rounded-[10px] bg-amber-50 dark:bg-amber-900/30 flex items-center justify-center shrink-0 mt-0.5">
          <Clock className="w-4 h-4 text-amber-600 dark:text-amber-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] text-text-secondary dark:text-gray-400">
            Đã tự động chọn: <span className="font-medium text-text-dark dark:text-gray-200">{decision.options[0]?.label}</span>
          </p>
        </div>
      </div>
    )
  }

  if (selectedValue) {
    // Show resolved state
    const selectedOpt = decision.options.find((o) => o.value === selectedValue)
    return (
      <div className="flex items-start gap-3 animate-fade-in">
        <div className="w-8 h-8 rounded-[10px] bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center shrink-0 mt-0.5">
          <CheckCircle className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] text-text-secondary dark:text-gray-400">
            Đã chọn: <span className="font-medium text-text-dark dark:text-gray-200">{selectedOpt?.label || selectedValue}</span>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="animate-slide-up">
      <div className={cn(
        'rounded-[20px] border-2 p-5 transition-all',
        'bg-white dark:bg-[#1a1a2e]',
        'border-brand-500/30 dark:border-brand-500/40',
        'shadow-[0_0_15px_rgba(59,130,246,0.08)]',
      )}>
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-[8px] bg-brand-500/10 dark:bg-brand-500/20 flex items-center justify-center">
              <MessageSquareMore className="w-3.5 h-3.5 text-brand-500" />
            </div>
            <span className="text-[12px] font-medium text-text-muted dark:text-gray-500 uppercase tracking-wider">
              Agent cần xác nhận
            </span>
          </div>
          {/* Timer badge */}
          <div className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-full-pill text-[12px] font-medium',
            timerPct > 50
              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
              : timerPct > 20
                ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                : 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400'
          )}>
            <Clock className={cn('w-3 h-3', timerColor)} />
            <span>{timeLeft}s</span>
          </div>
        </div>

        {/* Question */}
        <h3 className="font-display text-[18px] font-semibold text-text-dark dark:text-gray-100 mb-1.5 leading-snug">
          {decision.question}
        </h3>

        {/* Context */}
        {decision.context && (
          <p className="text-[13px] text-text-secondary dark:text-gray-400 mb-4 leading-relaxed">
            {decision.context}
          </p>
        )}

        {/* Options */}
        <div className="space-y-2 mb-1">
          {decision.options.map((opt, idx) => (
            <button
              key={opt.value}
              onClick={() => handleSelect(opt.value)}
              disabled={submitting}
              className={cn(
                'w-full text-left px-4 py-3 rounded-[12px] transition-all duration-200 group',
                'border border-transparent',
                idx === 0
                  ? 'bg-brand-500 text-white hover:bg-brand-600 shadow-[0_2px_8px_rgba(59,130,246,0.25)]'
                  : 'bg-surface-secondary dark:bg-[#252540] text-text-dark dark:text-gray-200 hover:bg-brand-50 dark:hover:bg-brand-500/10 hover:border-brand-200 dark:hover:border-brand-500/30',
                submitting && 'opacity-50 cursor-not-allowed',
              )}
            >
              <div className="flex items-start gap-3">
                {idx === 0 && (
                  <Sparkles className="w-4 h-4 mt-0.5 shrink-0 opacity-80" strokeWidth={2} />
                )}
                <div className="flex-1 min-w-0">
                  <div className={cn(
                    'text-[14px] font-medium',
                    idx === 0 ? '' : '',
                  )}>
                    {opt.label}
                  </div>
                  {opt.description && (
                    <div className={cn(
                      'text-[12px] mt-0.5 leading-relaxed',
                      idx === 0 ? 'text-white/70' : 'text-text-muted dark:text-gray-500',
                    )}>
                      {opt.description}
                    </div>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>

        {/* Custom input */}
        {decision.allow_custom && (
          <div className="mt-3 pt-3 border-t border-border-light dark:border-[#2a2a45]">
            <label className="text-[12px] text-text-muted dark:text-gray-500 mb-1.5 block font-medium">
              Hoặc nhập lựa chọn khác:
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={customValue}
                onChange={(e) => setCustomValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCustomSubmit()}
                placeholder="Nhập tại đây..."
                disabled={submitting}
                className={cn(
                  'flex-1 px-3 py-2 rounded-[8px] text-[13px]',
                  'border border-border dark:border-[#2a2a45]',
                  'bg-white dark:bg-[#15152a]',
                  'text-text-dark dark:text-gray-200',
                  'placeholder:text-text-muted dark:placeholder:text-gray-600',
                  'focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500',
                  submitting && 'opacity-50 cursor-not-allowed',
                )}
              />
              <button
                onClick={handleCustomSubmit}
                disabled={submitting || !customValue.trim()}
                className={cn(
                  'px-4 py-2 rounded-[8px] text-[13px] font-medium transition-colors',
                  'bg-brand-500 text-white hover:bg-brand-600',
                  'disabled:opacity-40 disabled:cursor-not-allowed',
                )}
              >
                Gửi
              </button>
            </div>
          </div>
        )}

        {/* Timer progress bar */}
        <div className="mt-3 h-1 rounded-full bg-gray-100 dark:bg-[#252540] overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-1000 ease-linear',
              timerPct > 50 ? 'bg-emerald-400' : timerPct > 20 ? 'bg-amber-400' : 'bg-red-400',
            )}
            style={{ width: `${timerPct}%` }}
          />
        </div>
        <p className="text-[11px] text-text-muted dark:text-gray-600 mt-1.5 text-center">
          Tự động chọn &ldquo;{decision.options[0]?.label}&rdquo; sau {timeLeft}s
        </p>
      </div>
    </div>
  )
}
