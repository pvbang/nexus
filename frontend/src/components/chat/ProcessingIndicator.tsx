// ============================================================
// MekongAI Nexus - ProcessingIndicator
// Thinking/progress display with step list and agent activities
// ============================================================

import { Sparkles, CheckCircle2 } from 'lucide-react'
import { cn } from '../../lib/cn'

// ── Types ─────────────────────────────────────────────────────

export interface StepInfo {
  step: number
  total_steps: number
  icon: string
  title: string
  description: string
  status: 'active' | 'done' | 'error' | 'pending'
}

export interface AgentActivity {
  id: string
  agent_name: string
  agent_role: string
  action: string
  detail: string
  timestamp: number
}

export interface ProcessingIndicatorProps {
  progressSteps: StepInfo[]
  agentActivities: AgentActivity[]
  statusText?: string
  isComplete?: boolean
}

// ── Activity emoji map ────────────────────────────────────────

function activityEmoji(action: string): string {
  switch (action) {
    case 'tool_start': return '🔧'
    case 'tool_done': return '✅'
    case 'agent_thinking': return '💭'
    default: return '💬'
  }
}

function activityLabel(action: string, detail: string): string {
  switch (action) {
    case 'tool_start': return `calling ${detail}...`
    case 'tool_done': return `${detail} done`
    default: return detail.slice(0, 60)
  }
}

function SpinnerSvg({ className }: { className?: string }) {
  return (
    <svg className={cn('animate-spin', className)} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2.5" opacity="0.15" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

// ── Component ─────────────────────────────────────────────────

export function ProcessingIndicator({ progressSteps, agentActivities, statusText, isComplete }: ProcessingIndicatorProps) {
  const hasSteps = progressSteps.length > 0

  return (
    <div className="flex justify-start animate-fade-in mb-4">
      <div className="max-w-[85%] sm:max-w-[75%] min-w-[320px]">
        {/* MekongAI label */}
        <div className="flex items-center gap-2 mb-1.5 px-1">
          <div className="w-6 h-6 rounded-[7px] bg-gradient-to-br from-[#3b82f6] to-[#818cf8] flex items-center justify-center">
            <Sparkles className="w-3 h-3 text-white" />
          </div>
          <span className="font-bold text-[11px] font-bold text-text-muted dark:text-gray-500 uppercase tracking-wider">
            Agent Process
          </span>
          {isComplete && (
            <div className="flex items-center gap-1 ml-auto text-emerald-500 font-bold text-[10px] uppercase">
              <CheckCircle2 className="w-3 h-3" />
               Completed
            </div>
          )}
        </div>

        {/* Processing bubble */}
        <div
          className={cn(
            'rounded-[16px] rounded-bl-[4px] px-4 py-3 border transition-all duration-300',
            isComplete 
              ? 'bg-emerald-50/20 dark:bg-emerald-900/5 border-emerald-100/50 dark:border-emerald-800/20' 
              : 'bg-[#f4f5f7] dark:bg-[#1e1e35] border-transparent',
          )}
        >
          {hasSteps ? (
            <div className="space-y-2">
              {/* Step list */}
              {progressSteps.map((step) => {
                 // Force 'done' if isComplete is true
                 const status = isComplete ? 'done' : step.status;
                 return (
                    <div
                      key={step.step}
                      className={cn(
                        'flex items-start gap-2.5 transition-all duration-300',
                        status === 'done' && 'opacity-60',
                      )}
                    >
                      <div className="shrink-0 mt-0.5">
                        {status === 'active' ? (
                          <div className="relative">
                            <span className="text-[16px]">{step.icon}</span>
                            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                          </div>
                        ) : status === 'done' ? (
                          <span className="text-[16px]">✅</span>
                        ) : (
                          <span className="text-[16px] opacity-40">{step.icon}</span>
                        )}
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className={cn(
                            'text-[12px] font-bold',
                            status === 'active' && 'text-[#1d4ed8] dark:text-blue-400',
                            status === 'done' && 'text-text-secondary dark:text-gray-400',
                          )}>
                          {step.title}
                        </div>
                        <div className="text-[11px] text-text-muted dark:text-gray-500 truncate">
                          {step.description}
                        </div>
                      </div>
                    </div>
                 )
              })}

              {/* Agent activities */}
              {agentActivities.length > 0 && (
                <div className="mt-2 pt-2 border-t border-[#e5e7eb]/60 dark:border-[#2a2a45]/60 space-y-1">
                  <div className="text-[10px] font-bold text-text-muted dark:text-gray-500 uppercase tracking-wider mb-1">
                    Activity Logs
                  </div>
                  {agentActivities.slice(-3).map((act) => (
                    <div key={act.id} className="flex items-center gap-1.5 text-[10px]">
                      <span className="shrink-0">{activityEmoji(act.action)}</span>
                      <span className="font-bold text-[#6366f1] dark:text-indigo-400 shrink-0">
                        {act.agent_name}:
                      </span>
                      <span className="text-text-muted dark:text-gray-500 truncate">
                        {activityLabel(act.action, act.detail)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Status bar */}
              {statusText && (
                <div className="flex items-center gap-2 pt-1 border-t border-black/5 dark:border-white/5 mt-2">
                   <SpinnerSvg className="w-3 h-3 text-blue-500" />
                   <span className="text-[11px] text-blue-600 dark:text-blue-400 font-medium italic animate-pulse">
                     {statusText}
                   </span>
                </div>
              )}
            </div>
          ) : (
            /* Simple thinking state */
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                {isComplete ? (
                    <div className="w-8 h-8 rounded-full bg-emerald-100 flex items-center justify-center">
                        <CheckCircle2 className="w-5 h-5 text-emerald-600" />
                    </div>
                ) : (
                    <SpinnerSvg className="w-4 h-4 text-blue-500" />
                )}
                <div className="flex flex-col">
                    <span className="text-[13px] font-bold text-text-dark dark:text-gray-200">
                      {isComplete ? 'Analysis Finished' : (statusText || 'Thinking...')}
                    </span>
                    {!isComplete && <span className="text-[10px] text-text-muted">Orchestrating agents and tools</span>}
                </div>
              </div>
              
              {!isComplete && (
                  <div className="flex gap-1">
                    {[0, 1, 2].map((i) => (
                      <span key={i} className="w-[4px] h-[4px] rounded-full bg-blue-500/40 animate-bounce" style={{ animationDelay: `${i * 0.1}s` }} />
                    ))}
                  </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
