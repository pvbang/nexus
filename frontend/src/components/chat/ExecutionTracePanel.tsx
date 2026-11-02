// ============================================================
// Nexus - Execution Trace Panel
// Real-time visibility with full content & Smart Renderer
// ============================================================
// Issue fixes:
// 1. Spinner: tool_done merges into tool_start at store level,
//    so ToolEventCard checks _merged/output to show done state
// 2. Click-to-modal: TraceDetailModal for full tool/thinking detail
// 3. SwarmRoundCard: expanded proposals/rebuttals/verdict display
// ============================================================

import React, { useState, useEffect, useRef } from 'react'
import {
  Brain, Wrench, Bot, ChevronDown, ChevronRight,
  Sparkles, CheckCircle, XCircle, Loader2, Users, Zap,
  Search, FileText, Terminal, Globe, Code,
  Maximize2, X, Code2, Clock, User, MessageSquare,
  ChevronUp, Shield,
  Microscope, ScanEye, Target, ClipboardCheck, BarChart3, PenLine,
  AlertTriangle,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { sanitizeDebateContent } from '../../lib/utils'
import { getToolSource } from '../process/cards/tool-renderers'

// ============================================================
// Types — re-exported from shared type definitions
// ============================================================

export type {
  ThinkingEvent,
  ToolEvent,
  AgentSpawnEvent,
  SwarmRoundEvent,
  StepEvent,
  DeepAnalysisExecEvent,
  SelfReflectionExecEvent,
  AutoVerifyExecEvent,
  CompletionCheckExecEvent,
  MidAnalysisExecEvent,
  ReactPhaseExecEvent,
  DebateCritiqueExecEvent,
  DebateRevisionExecEvent,
  DebateProposalExecEvent,
  DebateRebuttalExecEvent,
  DebateVerdictExecEvent,
  ExecutionEvent,
} from '../../types/execution'

import type {
  ExecutionEvent,
  ThinkingEvent,
  ToolEvent,
  SwarmRoundEvent,
  ReactPhaseExecEvent,
} from '../../types/execution'

// ============================================================
// Shared Components
// ============================================================

function SmartContent({ value, className }: { value: unknown; className?: string }) {
  const [mode, setMode] = useState<'pretty' | 'raw'>('pretty')
  if (value === null || value === undefined || value === '') {
    return <span className="text-gray-400 italic text-[11px]">(trống)</span>
  }
  const isStr = typeof value === 'string'
  let parsed: unknown = value
  let isJson = false
  if (isStr) {
    const s = value as string
    if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
      try { parsed = JSON.parse(s); isJson = true } catch { isJson = false }
    }
  } else if (typeof value === 'object') {
    isJson = true
    parsed = value
  }
  const rawStr = isStr ? (value as string) : JSON.stringify(value, null, 2)
  const prettyJson = isJson ? JSON.stringify(parsed, null, 2) : null
  
  if (isJson && prettyJson) {
    return (
      <div className={className}>
        <div className="flex gap-1 mb-1">
          {(['pretty', 'raw'] as const).map((m) => (
            <button key={m} onClick={(e) => { e.stopPropagation(); setMode(m) }} className={cn('px-1.5 py-0.5 rounded text-[9px] font-semibold transition-colors', mode === m ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500')}>
              {m === 'pretty' ? 'JSON' : 'Raw'}
            </button>
          ))}
        </div>
        <pre className="text-[10px] font-mono text-gray-700 bg-gray-50/50 rounded-md p-2 border border-gray-100 overflow-x-auto whitespace-pre-wrap max-h-[400px] overflow-y-auto leading-relaxed">
          {mode === 'pretty' ? prettyJson : rawStr}
        </pre>
      </div>
    )
  }
  return (
    <div className={cn("text-[11px] text-gray-700 whitespace-pre-wrap leading-relaxed", className)}>
      {rawStr}
    </div>
  )
}

// ============================================================
// Event Renderers
// ============================================================

const getToolIcon = (tool: string) => {
  const lowerTool = (tool || '').toLowerCase()
  if (lowerTool.includes('search') || lowerTool.includes('web')) return <Search className="w-3.5 h-3.5" />
  if (lowerTool.includes('file') || lowerTool.includes('read') || lowerTool.includes('write')) return <FileText className="w-3.5 h-3.5" />
  if (lowerTool.includes('terminal') || lowerTool.includes('command') || lowerTool.includes('execute')) return <Terminal className="w-3.5 h-3.5" />
  if (lowerTool.includes('browser') || lowerTool.includes('fetch') || lowerTool.includes('url')) return <Globe className="w-3.5 h-3.5" />
  if (lowerTool.includes('code') || lowerTool.includes('python')) return <Code className="w-3.5 h-3.5" />
  return <Wrench className="w-3.5 h-3.5" />
}

function ThinkingEventCard({ event, expanded, onToggle, onMaximize }: {
  event: ThinkingEvent
  expanded: boolean
  onToggle: () => void
  onMaximize: () => void
}) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-purple-100 rounded-xl overflow-hidden hover:shadow-sm transition-shadow shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <div className="w-6 h-6 rounded-lg bg-purple-50 flex items-center justify-center">
            <Brain className="w-3.5 h-3.5 text-purple-600" />
        </div>
        <div className="flex-1 min-w-0 cursor-pointer" onClick={onToggle}>
            <div className="flex items-center gap-2">
                <span className="text-[12px] font-bold text-gray-800">{event.agent_name}</span>
                <span className="text-[10px] text-purple-500 font-medium">{event.agent_role === 'response' ? 'tổng hợp kết quả...' : (event as any).action === 'discussion' ? 'Discussion Response' : 'đang suy nghĩ...'}</span>
            </div>
            {!expanded && (
                <p className="text-[11px] text-gray-500 truncate mt-0.5">{event.content.slice(0, 100)}</p>
            )}
        </div>
        <div className="flex items-center gap-1">
            <button onClick={onMaximize} className="p-1 rounded-md hover:bg-gray-100 text-gray-400"><Maximize2 className="w-3 h-3" /></button>
            <button onClick={onToggle} className="p-1 rounded-md hover:bg-gray-100 text-gray-400">
                {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
        </div>
      </div>
      {expanded && (
        <div className="px-3 pb-3 border-t border-purple-50 bg-purple-50/10 pt-2 animate-fade-in">
          <SmartContent value={event.content} className="max-h-[300px] overflow-y-auto" />
          {(event as any).action === 'discussion' && (
            <div className="mt-1.5 space-y-1">
              {(event as any).target_agents?.length > 0 && (
                <div className="text-[10px] text-blue-400">
                  → {(event as any).target_agents.map((a: string) => `@${a}`).join(', ')}
                </div>
              )}
              {(event as any).challenges?.length > 0 && (
                <div className="space-y-0.5">
                  <span className="text-[9px] font-semibold text-amber-500 uppercase">Challenges</span>
                  {(event as any).challenges.map((c: string, i: number) => (
                    <div key={i} className="flex items-start gap-1 text-[10px] text-amber-300/90">
                      <span className="shrink-0">⚡</span>
                      <span>{c}</span>
                    </div>
                  ))}
                </div>
              )}
              {(event as any).supports?.length > 0 && (
                <div className="space-y-0.5">
                  <span className="text-[9px] font-semibold text-green-500 uppercase">Supports</span>
                  {(event as any).supports.map((s: string, i: number) => (
                    <div key={i} className="flex items-start gap-1 text-[10px] text-green-300/90">
                      <span className="shrink-0">✅</span>
                      <span>{s}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * ToolEventCard — now simplified since store merges tool_done into tool_start.
 * A tool_start with _merged=true or output means it's completed.
 * A tool_start without _merged means it's still pending (spinner).
 * Standalone tool_done events (no matching start) show as completed.
 */
function ToolEventCard({ event, onOpenDetail }: {
  event: ToolEvent
  onOpenDetail: (ev: ToolEvent) => void
}) {
  const [expanded, setExpanded] = useState(false)

  // Determine completion state:
  // - tool_start with _merged or output → completed
  // - tool_start without → pending (spinner)
  // - tool_done → always completed
  const isCompleted = event.type === 'tool_done' || event._merged === true || event.output !== undefined
  const isError = event.success === false
  const isPending = !isCompleted

  const params = event.params
  const output = event.output
  const execTime = event.execution_time_ms

  // Preview text for collapsed state
  const outputPreview = output
    ? (typeof output === 'string' ? output : JSON.stringify(output)).slice(0, 100)
    : isPending ? 'đang chạy...' : '—'

  return (
    <div
      className={cn("bg-white border rounded-xl overflow-hidden shadow-[0_1px_2px_rgba(0,0,0,0.02)] cursor-pointer hover:shadow-md transition-shadow",
        isError ? 'border-red-100' : isPending ? 'border-blue-100' : 'border-emerald-100'
      )}
      onClick={() => onOpenDetail(event)}
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        <div className={cn("w-6 h-6 rounded-lg flex items-center justify-center",
          isError ? 'bg-red-50 text-red-600' : isPending ? 'bg-blue-50 text-blue-600' : 'bg-emerald-50 text-emerald-600'
        )}>
           {isError ? <XCircle className="w-3.5 h-3.5" /> : isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : getToolIcon(event.tool)}
        </div>
        <div className="flex-1 min-w-0" onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}>
           <div className="flex items-center gap-2 flex-wrap">
               <span className="text-[12px] font-bold text-gray-800 dark:text-gray-100">{event.agent_name}</span>
               <span className="text-[10px] text-gray-400">→</span>
               <span className="text-[12px] font-semibold text-gray-700 dark:text-gray-200">{event.tool}</span>
               {/* Tool source badge */}
               {(() => {
                 const src = getToolSource(event.tool)
                 if (src.source === 'core' || src.source === 'unknown') return null
                 return (
                   <span className={cn(
                     'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium border leading-none',
                     src.bgColor, src.color,
                   )}>
                     <span className="text-[8px]">{src.emoji}</span>
                     {src.label}
                   </span>
                 )
               })()}
               {execTime && (
                 <span className={cn("text-[9px] font-mono px-1 rounded",
                   isError ? "text-red-500 bg-red-50" : "text-emerald-600 bg-emerald-50"
                 )}>
                   {execTime < 1000 ? `${execTime}ms` : `${(execTime / 1000).toFixed(1)}s`}
                 </span>
               )}
               {isError && <span className="text-[9px] font-bold text-red-500 bg-red-50 px-1 rounded">LỖI</span>}
               {isCompleted && !isError && <CheckCircle className="w-3 h-3 text-emerald-500" />}
           </div>
           {!expanded && (
               <p className="text-[11px] text-gray-500 truncate mt-0.5">{outputPreview}</p>
           )}
        </div>
        <div className="flex items-center gap-1">
            <button
              onClick={(e) => { e.stopPropagation(); onOpenDetail(event) }}
              className="p-1 rounded-md hover:bg-gray-100 text-gray-400"
              title="Xem đầy đủ"
            >
              <Maximize2 className="w-3 h-3" />
            </button>
            <button onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }} className="p-1 rounded-md hover:bg-gray-100 text-gray-400">
                {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
        </div>
      </div>
      {expanded && (
        <div className="px-3 pb-3 border-t bg-gray-50/10 pt-3 space-y-3 animate-fade-in" onClick={(e) => e.stopPropagation()}>
           {params && (
             <section>
               <div className="text-[9px] font-bold text-gray-400 uppercase mb-1.5 flex items-center gap-1"><Code className="w-2.5 h-2.5" /> Input Params</div>
               <SmartContent value={params} />
             </section>
           )}
           {output !== undefined && (
             <section>
               <div className={cn("text-[9px] font-bold uppercase mb-1.5 flex items-center gap-1", isError ? "text-red-500" : "text-emerald-600")}>
                 <CheckCircle className="w-2.5 h-2.5" /> Output
               </div>
               <SmartContent value={output} className={isError ? 'text-red-700 bg-red-50/50 p-2 rounded border border-red-100' : ''} />
             </section>
           )}
           {!output && !params && (
             <p className="text-[11px] text-gray-400 italic">Không có dữ liệu chi tiết</p>
           )}
        </div>
      )}
    </div>
  )
}

/**
 * SwarmRoundCard — expanded to show full proposals, rebuttals, and verdict.
 */
function SwarmRoundCard({ event, onOpenDetail }: { event: SwarmRoundEvent; onOpenDetail: (ev: SwarmRoundEvent) => void }) {
  const [expanded, setExpanded] = useState(false)
  const hasProposals = event.proposals.length > 0
  const hasRebuttals = (event.rebuttals?.length || 0) > 0
  const hasVerdict = !!event.verdict

  return (
    <div
      className="bg-gradient-to-r from-violet-50/30 to-fuchsia-50/30 border border-violet-100 rounded-xl shadow-sm overflow-hidden cursor-pointer hover:shadow-md transition-shadow"
      onClick={() => onOpenDetail(event)}
    >
      {/* Header */}
      <div className="flex items-center gap-2 p-3">
        <div className="w-7 h-7 rounded-lg bg-violet-600 flex items-center justify-center shadow-sm shrink-0">
          <Users className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-[13px] font-bold text-violet-900 leading-none">
            Vòng thảo luận {event.round}/{event.total_rounds}
          </h3>
          <p className="text-[10px] text-violet-500 mt-1 font-medium">
            {event.proposals.length} kiến nghị • {event.rebuttals?.length || 0} phản biện
            {hasVerdict && ` • ${event.verdict?.verdict === 'resolved' ? '✅ Đã kết luận' : '🔄 Tiếp tục'}`}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); onOpenDetail(event) }}
            className="p-1 rounded-md hover:bg-violet-100 text-violet-400"
            title="Xem đầy đủ"
          >
            <Maximize2 className="w-3 h-3" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
            className="p-1 rounded-md hover:bg-violet-100 text-violet-400"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Expanded content: proposals, rebuttals, verdict */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-violet-100 space-y-3 animate-fade-in" onClick={(e) => e.stopPropagation()}>
          {/* Proposals */}
          {hasProposals && (
            <section className="mt-2">
              <div className="text-[9px] font-bold text-violet-500 uppercase mb-1.5 flex items-center gap-1">
                <MessageSquare className="w-2.5 h-2.5" /> Kiến nghị ({event.proposals.length})
              </div>
              <div className="space-y-2">
                {event.proposals.map((proposal, idx) => (
                  <div key={idx} className="bg-white/80 border border-violet-50 rounded-lg p-2.5">
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="text-[11px] font-bold text-violet-800">{proposal.agent}</span>
                      {proposal.role && (
                        <span className="text-[9px] px-1.5 py-0.5 bg-violet-100 text-violet-600 rounded-full font-medium">{proposal.role}</span>
                      )}
                      {proposal.tools_used && proposal.tools_used.length > 0 && (
                        <span className="text-[9px] text-gray-400 ml-auto">🔧 {proposal.tools_used.join(', ')}</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap line-clamp-6">
                      {proposal.content}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Rebuttals */}
          {hasRebuttals && (
            <section>
              <div className="text-[9px] font-bold text-orange-500 uppercase mb-1.5 flex items-center gap-1">
                <Shield className="w-2.5 h-2.5" /> Phản biện ({event.rebuttals!.length})
              </div>
              <div className="space-y-2">
                {event.rebuttals!.map((rebuttal, idx) => (
                  <div key={idx} className="bg-orange-50/40 border border-orange-100 rounded-lg p-2.5">
                    <span className="text-[11px] font-bold text-orange-700">{rebuttal.agent}</span>
                    <p className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap mt-1 line-clamp-4">
                      {rebuttal.content}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Verdict */}
          {hasVerdict && (
            <div className="bg-white/80 border border-violet-100 rounded-lg p-2.5">
              <div className="flex items-center gap-1.5 text-violet-800 font-bold text-[11px] mb-1">
                <Zap className="w-3 h-3 text-amber-500 fill-amber-500" />
                Phán quyết: {event.verdict!.winner || (event.verdict!.verdict === 'resolved' ? 'Đồng thuận' : 'Đang thống nhất')}
              </div>
              {event.verdict!.synthesis && (
                <p className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap mt-1">
                  <span className="font-semibold text-violet-600">Tổng hợp: </span>{event.verdict!.synthesis}
                </p>
              )}
              {event.verdict!.reasoning && (
                <p className="text-[11px] text-gray-600 leading-relaxed italic mt-1 whitespace-pre-wrap">
                  {event.verdict!.reasoning}
                </p>
              )}
            </div>
          )}

          {/* No content state */}
          {!hasProposals && !hasRebuttals && !hasVerdict && (
            <p className="text-[11px] text-violet-400 italic py-2">Đang chờ dữ liệu thảo luận...</p>
          )}
        </div>
      )}

      {/* Collapsed verdict preview */}
      {!expanded && hasVerdict && (
        <div className="px-3 pb-2.5">
          <div className="bg-white/80 border border-violet-100 rounded-lg p-2.5">
            <div className="flex items-center gap-1.5 text-violet-800 font-bold text-[11px] mb-1">
              <Zap className="w-3 h-3 text-amber-500 fill-amber-500" /> Phán quyết: {event.verdict!.winner || 'Đồng thuận'}
            </div>
            <p className="text-[11px] text-gray-700 leading-relaxed italic line-clamp-2">{event.verdict!.reasoning || event.verdict!.synthesis}</p>
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================
// Trace Detail Modal — full-screen popup for detailed inspection
// ============================================================

function TraceDetailModal({ event, onClose }: { event: ExecutionEvent; onClose: () => void }) {
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  let title = 'Chi tiết sự kiện'
  let content: React.ReactNode = null

  if (event.type === 'thinking') {
    title = `💭 Nội dung suy nghĩ — ${event.agent_name}`
    content = (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-[12px] text-gray-500">
          <User className="h-3.5 w-3.5" />
          <span>Agent: <strong className="text-gray-700">{event.agent_name}</strong></span>
          {event.agent_role && (
            <span className="text-[10px] px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded-full font-medium">{event.agent_role}</span>
          )}
        </div>
        <section>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-2 flex items-center gap-1">
            <Brain className="w-3.5 h-3.5" /> Nội dung
          </div>
          <SmartContent value={event.content} className="p-3 bg-purple-50/30 rounded-xl border border-purple-100" />
        </section>
      </div>
    )
  } else if (event.type === 'tool_start' || event.type === 'tool_done') {
    const isError = event.success === false
    const isCompleted = event.type === 'tool_done' || event._merged === true || event.output !== undefined
    title = `🔧 ${event.tool} — ${event.agent_name}`
    content = (
      <div className="space-y-4">
        {/* Meta info */}
        <div className="flex items-center gap-3 flex-wrap text-[12px] text-gray-500">
          <div className="flex items-center gap-1">
            <User className="h-3.5 w-3.5" />
            <span>Agent: <strong className="text-gray-700">{event.agent_name}</strong></span>
          </div>
          {event.execution_time_ms && (
            <div className="flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" />
              <span className="font-mono tabular-nums">
                {event.execution_time_ms < 1000 ? `${event.execution_time_ms}ms` : `${(event.execution_time_ms / 1000).toFixed(1)}s`}
              </span>
            </div>
          )}
          <span className={cn(
            "text-[10px] px-2 py-0.5 rounded-full font-bold",
            isError ? "bg-red-100 text-red-700" : isCompleted ? "bg-emerald-100 text-emerald-700" : "bg-blue-100 text-blue-700"
          )}>
            {isError ? '❌ Lỗi' : isCompleted ? '✅ Hoàn thành' : '⏳ Đang chạy'}
          </span>
        </div>

        {/* Params */}
        {event.params && (
          <section>
            <div className="flex items-center gap-1.5 mb-2">
              <Code2 className="h-3.5 w-3.5 text-gray-500" />
              <span className="text-[11px] font-bold text-gray-600 uppercase tracking-wide">Input Arguments</span>
            </div>
            <SmartContent value={event.params} className="p-3 bg-gray-50 rounded-xl border border-gray-100" />
          </section>
        )}

        {/* Output */}
        {event.output !== undefined && (
          <section>
            <div className="flex items-center gap-1.5 mb-2">
              <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
              <span className={cn("text-[11px] font-bold uppercase tracking-wide", isError ? "text-red-600" : "text-emerald-600")}>
                Output / Result
              </span>
            </div>
            <SmartContent
              value={event.output}
              className={cn(
                "p-3 rounded-xl border",
                isError ? "bg-red-50/30 border-red-100 text-red-700" : "bg-white border-gray-100"
              )}
            />
          </section>
        )}

        {!event.params && event.output === undefined && (
          <p className="text-[12px] text-gray-400 italic">Không có dữ liệu chi tiết cho tool call này.</p>
        )}
      </div>
    )
  } else if (event.type === 'agent_spawn') {
    title = `🤖 Agent: ${event.agent_name}`
    content = (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-[12px] text-gray-500">
          <Bot className="h-3.5 w-3.5" />
          <span>Agent: <strong className="text-gray-700">{event.agent_name}</strong></span>
          <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full font-bold">{event.agent_role}</span>
        </div>
        {event.system_prompt ? (
          <section>
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-2">System Prompt</div>
            <SmartContent value={event.system_prompt} className="p-3 bg-blue-50/30 rounded-xl border border-blue-100" />
          </section>
        ) : (
          <p className="text-gray-400 italic text-[12px]">Không có system prompt</p>
        )}
      </div>
    )
  } else if (event.type === 'swarm_round') {
    title = `🗣️ Vòng thảo luận ${event.round}/${event.total_rounds}`
    content = (
      <div className="space-y-5">
        {/* Summary */}
        <div className="flex items-center gap-3 text-[12px] text-violet-600 bg-violet-50/50 p-3 rounded-xl border border-violet-100">
          <Users className="w-4 h-4" />
          <span>{event.proposals.length} kiến nghị • {event.rebuttals?.length || 0} phản biện</span>
        </div>

        {/* Proposals */}
        {event.proposals.length > 0 && (
          <section>
            <div className="text-[11px] font-bold text-violet-600 uppercase mb-2 flex items-center gap-1">
              <MessageSquare className="w-3.5 h-3.5" /> Kiến nghị
            </div>
            <div className="space-y-3">
              {event.proposals.map((p, i) => (
                <div key={i} className="bg-white border border-violet-100 rounded-xl p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[12px] font-bold text-violet-800">{p.agent}</span>
                    {p.role && <span className="text-[9px] px-1.5 py-0.5 bg-violet-100 text-violet-600 rounded-full">{p.role}</span>}
                    {p.tools_used && p.tools_used.length > 0 && (
                      <span className="text-[9px] text-gray-400 ml-auto">🔧 {p.tools_used.join(', ')}</span>
                    )}
                  </div>
                  <div className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap">{p.content}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Rebuttals */}
        {(event.rebuttals?.length || 0) > 0 && (
          <section>
            <div className="text-[11px] font-bold text-orange-600 uppercase mb-2 flex items-center gap-1">
              <Shield className="w-3.5 h-3.5" /> Phản biện
            </div>
            <div className="space-y-3">
              {event.rebuttals!.map((r, i) => (
                <div key={i} className="bg-orange-50/30 border border-orange-100 rounded-xl p-3">
                  <span className="text-[12px] font-bold text-orange-700 mb-1 block">{r.agent}</span>
                  <div className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap">{r.content}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Verdict */}
        {event.verdict && (
          <section>
            <div className="text-[11px] font-bold text-amber-600 uppercase mb-2 flex items-center gap-1">
              <Zap className="w-3.5 h-3.5 fill-amber-500" /> Phán quyết
            </div>
            <div className="bg-amber-50/30 border border-amber-200 rounded-xl p-3 space-y-2">
              {event.verdict.winner && (
                <p className="text-[12px]"><strong className="text-amber-700">Người thắng:</strong> {event.verdict.winner}</p>
              )}
              {event.verdict.verdict && (
                <p className="text-[12px]"><strong className="text-amber-700">Kết quả:</strong> {event.verdict.verdict}</p>
              )}
              {event.verdict.synthesis && (
                <div>
                  <p className="text-[11px] font-semibold text-violet-600 mb-1">Tổng hợp:</p>
                  <p className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap">{event.verdict.synthesis}</p>
                </div>
              )}
              {event.verdict.reasoning && (
                <div>
                  <p className="text-[11px] font-semibold text-gray-600 mb-1">Lý do:</p>
                  <p className="text-[11px] text-gray-600 leading-relaxed italic whitespace-pre-wrap">{event.verdict.reasoning}</p>
                </div>
              )}
            </div>
          </section>
        )}
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className={cn(
        'relative z-10 w-full max-w-3xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden',
        'bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800',
      )}>
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800 shrink-0 bg-gray-50/80 dark:bg-gray-900/80">
          <h3 className="flex-1 text-[15px] font-bold text-gray-900 dark:text-gray-100 truncate">{title}</h3>
          <span className="text-[10px] text-gray-400 font-mono tabular-nums shrink-0">
            {new Date(event.timestamp).toLocaleTimeString('vi-VN')}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors ml-2"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {content}
        </div>
      </div>
    </div>
  )
}

// ============================================================
// Main Component
// ============================================================

interface ExecutionTracePanelProps {
  events: ExecutionEvent[]
  isActive: boolean
  onClear?: () => void
}

export function ExecutionTracePanel({ events, isActive, onClear }: ExecutionTracePanelProps) {
  const [showAll, setShowAll] = useState(true)
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set())
  const [modalEvent, setModalEvent] = useState<ExecutionEvent | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    if (containerRef.current && isActive) {
      containerRef.current.scrollTop = 0
    }
  }, [events, isActive])
  
  // Filter events: in "showAll" mode, skip tool_done events since they are
  // merged into tool_start at store level. In collapsed mode, only show
  // swarm_round, step, agent_spawn.
  const filteredEvents = (showAll
    ? events.filter((e) => e.type !== 'tool_done')
    : events.filter((e) =>
        e.type === 'swarm_round' || e.type === 'step' || e.type === 'agent_spawn'
      )
  ).slice().reverse()
  
  const toggleThinking = (id: string) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  
  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[300px] px-6 text-center">
        <div className="w-16 h-16 rounded-2xl bg-gray-50 flex items-center justify-center mb-4 border border-gray-100">
          <Sparkles className="w-8 h-8 text-gray-300" />
        </div>
        <h4 className="text-[14px] font-bold text-gray-600">Trình theo dõi thực thi</h4>
        <p className="text-[11px] text-gray-400 mt-2 max-w-[200px]">Các sự kiện tư duy, sử dụng công cụ của Agent sẽ xuất hiện ở đây theo thời gian thực.</p>
      </div>
    )
  }
  
  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-950 relative overflow-hidden">
      {/* Full-screen detail modal */}
      {modalEvent && <TraceDetailModal event={modalEvent} onClose={() => setModalEvent(null)} />}

      {/* Header */}
      <div className="flex items-center justify-between px-3.5 py-3 border-b border-gray-100 bg-white/80 backdrop-blur-md sticky top-0 z-10">
        <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-gray-800">Hoạt động</span>
            {isActive && <span className="flex items-center gap-1.5 px-2 py-0.5 bg-emerald-50 text-emerald-600 text-[9px] font-bold rounded-full border border-emerald-100 animate-in fade-in zoom-in duration-300"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" /> Trực tiếp</span>}
            <span className="text-[10px] text-gray-400 font-medium">({events.length})</span>
        </div>
        <div className="flex items-center gap-1.5">
           <button onClick={() => setShowAll(!showAll)} className={cn("px-2 py-1 rounded-lg text-[10px] font-bold transition-all", showAll ? "bg-blue-600 text-white shadow-sm" : "bg-gray-100 text-gray-500 hover:bg-gray-200")}>
              {showAll ? 'Tất cả' : 'Thu gọn'}
           </button>
           {onClear && <button onClick={onClear} className="p-1 text-gray-300 hover:text-gray-500 transition-colors"><RefreshCw className="w-3.5 h-3.5" /></button>}
        </div>
      </div>
      
      {/* List */}
      <div ref={containerRef} className="flex-1 overflow-y-auto p-3 space-y-3 scroll-smooth">
        {filteredEvents.map((event) => {
          switch (event.type) {
            case 'thinking':
              return (
                <ThinkingEventCard
                  key={event.id}
                  event={event}
                  expanded={expandedThinking.has(event.id)}
                  onToggle={() => toggleThinking(event.id)}
                  onMaximize={() => setModalEvent(event)}
                />
              )
            case 'tool_start':
              return (
                <ToolEventCard
                  key={event.id}
                  event={event}
                  onOpenDetail={setModalEvent}
                />
              )
            case 'tool_done':
              // Standalone tool_done (not merged) — show directly
              return (
                <ToolEventCard
                  key={event.id}
                  event={event}
                  onOpenDetail={setModalEvent}
                />
              )
            case 'agent_spawn': return (
                <div key={event.id} className="bg-blue-50/30 border border-blue-50 rounded-xl p-2.5 flex items-center gap-2 cursor-pointer hover:shadow-sm transition-shadow" onClick={() => setModalEvent(event)}>
                     <div className="w-6 h-6 rounded-lg bg-blue-600 flex items-center justify-center text-white"><Bot className="w-3.5 h-3.5" /></div>
                     <div className="flex-1 min-w-0">
                         <div className="flex items-center gap-2"><span className="text-[12px] font-bold text-blue-900">{event.agent_name}</span> <span className="text-[9px] px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full font-bold">{event.agent_role}</span></div>
                         <p className="text-[10px] text-blue-500 font-medium italic">Agent mới đã tham gia hệ thống</p>
                     </div>
                     {event.system_prompt && <button onClick={(e) => { e.stopPropagation(); setModalEvent(event) }} className="p-1 rounded hover:bg-blue-100 text-blue-400"><Code2 className="w-3 h-3" /></button>}
                </div>
            )
            case 'swarm_round':
              return <SwarmRoundCard key={event.id} event={event} onOpenDetail={setModalEvent} />
            case 'step': {
              const isStepActive = isActive && event.status === 'active'
              return (
                <div key={event.id} className={cn("px-3 py-2 rounded-xl flex items-center gap-3 transition-colors", isStepActive ? "bg-blue-50/50 border border-blue-100" : "bg-gray-50/50 border border-transparent opacity-60")}>
                    <span className="text-lg">{event.icon}</span>
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                            <span className={cn("text-[12px] font-bold", isStepActive ? "text-blue-700" : "text-gray-700")}>{event.title}</span>
                            {isStepActive && <Loader2 className="w-3 h-3 animate-spin text-blue-500" />}
                            {!isActive && event.status === 'active' && <CheckCircle className="w-3 h-3 text-emerald-500" />}
                        </div>
                        <p className="text-[10px] text-gray-500 line-clamp-2">{event.description}</p>
                    </div>
                </div>
              )
            }
            // ── Deep Reasoning events (compact inline cards) ──
            case 'deep_analysis': {
              const d = event.data as Record<string, unknown>
              const complexity = (d.complexity_estimate as string) || 'medium'
              const strategy = (d.strategy as string) || ''
              const complexityColors: Record<string, string> = {
                low: 'bg-emerald-50 text-emerald-700 border-emerald-100',
                medium: 'bg-amber-50 text-amber-700 border-amber-100',
                high: 'bg-orange-50 text-orange-700 border-orange-100',
                very_high: 'bg-red-50 text-red-700 border-red-100',
              }
              return (
                <div key={event.id} className={cn('border rounded-xl p-2.5', complexityColors[complexity] || complexityColors.medium)}>
                  <div className="flex items-center gap-2">
                    <Microscope className="w-3.5 h-3.5 shrink-0" />
                    <span className="text-[12px] font-bold">Deep Analysis</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full font-bold bg-white/50">{complexity}</span>
                  </div>
                  {strategy && <p className="text-[11px] mt-1 leading-relaxed whitespace-pre-line">{strategy.replace(/(?<=\S)\s*(\d+\))/g, '\n$1')}</p>}
                </div>
              )
            }
            case 'self_reflection': {
              const d = event.data as Record<string, unknown>
              const score = typeof d.quality_score === 'number' ? Math.round(d.quality_score * 100) : 0
              const onTrack = d.on_track !== false
              return (
                <div key={event.id} className={cn('border rounded-xl p-2.5', onTrack ? 'bg-violet-50/50 border-violet-100' : 'bg-red-50/50 border-red-100')}>
                  <div className="flex items-center gap-2">
                    <ScanEye className="w-3.5 h-3.5 text-violet-600 shrink-0" />
                    <span className="text-[12px] font-bold text-violet-800">Self-Reflection</span>
                    {d.round != null && <span className="text-[10px] text-violet-500">Round {String(d.round)}</span>}
                    <span className={cn('text-[9px] px-1.5 py-0.5 rounded-full font-bold ml-auto', onTrack ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700')}>
                      {onTrack ? '✅ On Track' : '❌ Off Track'} • {score}%
                    </span>
                  </div>
                </div>
              )
            }
            case 'auto_verify': {
              const d = event.data as Record<string, unknown>
              const result = (d.verification_result as string) || 'pass'
              const toolName = (d.tool_name as string) || ''
              const resultConfig: Record<string, { icon: typeof CheckCircle; color: string }> = {
                pass: { icon: CheckCircle, color: 'bg-emerald-50 border-emerald-100 text-emerald-700' },
                fail: { icon: XCircle, color: 'bg-red-50 border-red-100 text-red-700' },
                warning: { icon: AlertTriangle, color: 'bg-amber-50 border-amber-100 text-amber-700' },
              }
              const cfg = resultConfig[result] || resultConfig.pass
              const Icon = cfg.icon
              return (
                <div key={event.id} className={cn('border rounded-xl p-2.5', cfg.color)}>
                  <div className="flex items-center gap-2">
                    <Icon className="w-3.5 h-3.5 shrink-0" />
                    <span className="text-[12px] font-bold">Auto-Verify</span>
                    {toolName && <span className="text-[10px] font-mono bg-white/50 px-1 rounded">{toolName}</span>}
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full font-bold bg-white/50 ml-auto">{result}</span>
                  </div>
                </div>
              )
            }
            case 'completion_check': {
              const d = event.data as Record<string, unknown>
              const verdict = (d.verdict as string) || 'pass'
              const quality = typeof d.quality_score === 'number' ? Math.round(d.quality_score * 100) : 0
              const verdictColors: Record<string, string> = {
                pass: 'bg-emerald-50 border-emerald-100 text-emerald-700',
                needs_improvement: 'bg-amber-50 border-amber-100 text-amber-700',
                fail: 'bg-red-50 border-red-100 text-red-700',
              }
              return (
                <div key={event.id} className={cn('border rounded-xl p-2.5', verdictColors[verdict] || verdictColors.pass)}>
                  <div className="flex items-center gap-2">
                    <ClipboardCheck className="w-3.5 h-3.5 shrink-0" />
                    <span className="text-[12px] font-bold">Completion Check</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full font-bold bg-white/50 ml-auto">{verdict} • {quality}%</span>
                  </div>
                </div>
              )
            }
            case 'mid_analysis': {
              const d = event.data as Record<string, unknown>
              const onTrack = d.on_track !== false
              return (
                <div key={event.id} className={cn('border rounded-xl p-2.5', onTrack ? 'bg-sky-50/50 border-sky-100' : 'bg-red-50/50 border-red-100')}>
                  <div className="flex items-center gap-2">
                    <BarChart3 className="w-3.5 h-3.5 text-sky-600 shrink-0" />
                    <span className="text-[12px] font-bold text-sky-800">Mid-Analysis</span>
                    {d.round != null && <span className="text-[10px] text-sky-500">Round {String(d.round)}</span>}
                    <span className={cn('text-[9px] px-1.5 py-0.5 rounded-full font-bold ml-auto', onTrack ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700')}>
                      {onTrack ? '✅ On Track' : '❌ Off Track'}
                    </span>
                  </div>
                  {!!d.strategy_adjustment && <p className="text-[11px] text-sky-700 mt-1 line-clamp-2">{String(d.strategy_adjustment)}</p>}
                </div>
              )
            }
            case 'react_phase': {
              const phase = (event as ReactPhaseExecEvent).phase || ''
              const round = (event as ReactPhaseExecEvent).round
              return (
                <div key={event.id} className="bg-slate-50/50 border border-slate-200 rounded-xl p-2.5">
                  <div className="flex items-center gap-2">
                    <span className="text-[14px]">⚛️</span>
                    <span className="text-[12px] font-bold text-slate-700">ReAct Phase</span>
                    <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full font-bold">{phase}</span>
                    {round != null && <span className="text-[10px] text-slate-500">Round {round}</span>}
                  </div>
                </div>
              )
            }
            case 'debate_critique': {
              const d = event.data as Record<string, unknown>
              const quality = typeof d.overall_quality === 'number' ? d.overall_quality : 5
              return (
                <div key={event.id} className="bg-rose-50/50 border border-rose-100 rounded-xl p-2.5">
                  <div className="flex items-center gap-2">
                    <Target className="w-3.5 h-3.5 text-rose-600 shrink-0" />
                    <span className="text-[12px] font-bold text-rose-800">Critic Feedback</span>
                    {!!d.agent_name && <span className="text-[10px] text-rose-500">{String(d.agent_name)}</span>}
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full font-bold bg-white/50 ml-auto text-rose-700">Quality: {quality}/10</span>
                  </div>
                </div>
              )
            }
            case 'debate_revision': {
              const d = event.data as Record<string, unknown>
              const revContent = sanitizeDebateContent(String(d.revised_content || d.content || ''))
              return (
                <div key={event.id} className="bg-indigo-50/50 border border-indigo-100 rounded-xl p-2.5">
                  <div className="flex items-center gap-2">
                    <PenLine className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
                    <span className="text-[12px] font-bold text-indigo-800">Revision</span>
                    {!!d.agent_name && <span className="text-[10px] px-1.5 py-0.5 bg-indigo-100 text-indigo-700 rounded-full font-medium">{String(d.agent_name)}</span>}
                  </div>
                  {!!revContent && <p className="text-[11px] text-indigo-700 mt-1 line-clamp-2">{revContent.slice(0, 100)}{revContent.length > 100 ? '…' : ''}</p>}
                </div>
              )
            }
            case 'micro_step': {
              return (
                <div key={event.id} className="px-3 py-1.5 flex items-center gap-2 opacity-70 text-[11px] text-gray-500">
                  <span>⚡</span>
                  <span>{event.agent ? `${event.agent}: ` : ''}{String(event.description || event.title || event.phase || '')}</span>
                </div>
              )
            }

            case 'planning_start': {
              return (
                <div key={event.id} className="border border-blue-200 bg-blue-50 rounded-xl p-3 my-1">
                  <div className="flex items-center gap-2">
                    <span className="text-blue-600">🧠</span>
                    <span className="text-xs font-semibold text-blue-800">Nexus Orchestrator Planning...</span>
                  </div>
                </div>
              )
            }

            case 'planning_done': {
              return (
                <div key={event.id} className="border border-blue-200 bg-blue-50 rounded-xl p-3 my-1">
                  <div className="flex items-center gap-2">
                    <span className="text-blue-600">✅</span>
                    <span className="text-xs font-semibold text-blue-800">Plan Ready</span>
                    {!!event.agents_count && <span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">{event.agents_count} agents</span>}
                    {!!event.complexity && <span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">{event.complexity}</span>}
                  </div>
                  {event.selected_agents && <p className="text-[10px] text-blue-600 mt-1">Team: {event.selected_agents.join(', ')}</p>}
                  {event.intent && <p className="text-[10px] text-blue-600 mt-1 line-clamp-2">{event.intent}</p>}
                </div>
              )
            }

            case 'evaluation_done': {
              const pct = event.progress_pct || 0
              return (
                <div key={event.id} className="border border-amber-200 bg-amber-50 rounded-xl p-3 my-1">
                  <div className="flex items-center gap-2">
                    <span className="text-amber-600">📊</span>
                    <span className="text-xs font-semibold text-amber-800">Evaluation</span>
                    <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">{pct}%</span>
                    <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">{event.decision || ''}</span>
                  </div>
                  {event.missing_items && event.missing_items.length > 0 && (
                    <p className="text-[10px] text-amber-600 mt-1">Missing: {event.missing_items.slice(0, 3).join(', ')}</p>
                  )}
                </div>
              )
            }

            case 'document_created': {
              const docEvent = event as any
              const docIcons: Record<string, string> = {
                'TASK_BRIEF': '📋',
                'ROE': '⚖️',
                'TODO': '✅',
                'TODO_UPDATE': '🔄',
              }
              const icon = docIcons[docEvent.doc_type] || '📄'
              return (
                <div key={event.id} className="border border-blue-500/30 bg-blue-950/20 rounded-xl p-2.5 space-y-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm">{icon}</span>
                    <span className="text-[11px] font-semibold text-blue-300">
                      {docEvent.doc_type || 'Document'}
                    </span>
                    {docEvent.turn > 0 && (
                      <span className="text-[9px] text-blue-400/60 ml-auto">Turn {docEvent.turn}</span>
                    )}
                  </div>
                  {docEvent.title && (
                    <div className="text-[10px] text-blue-200/80 font-medium">{docEvent.title}</div>
                  )}
                  {docEvent.content_preview && (
                    <div className="text-[10px] text-zinc-400 line-clamp-3 whitespace-pre-wrap">
                      {docEvent.content_preview}
                    </div>
                  )}
                  {docEvent.path && (
                    <div className="text-[9px] text-zinc-500 font-mono truncate">{docEvent.path}</div>
                  )}
                </div>
              )
            }

            case 'next_turn_plan': {
              const planEvent = event as any
              return (
                <div key={event.id} className="border border-violet-500/30 bg-violet-950/20 rounded-xl p-2.5 space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm">🗺️</span>
                    <span className="text-[11px] font-semibold text-violet-300">
                      Next Turn Plan
                    </span>
                    {planEvent.turn > 0 && (
                      <span className="text-[9px] text-violet-400/60 ml-auto">Turn {planEvent.turn}</span>
                    )}
                  </div>
                  {planEvent.reasoning && (
                    <div className="text-[10px] text-zinc-300/80 whitespace-pre-wrap line-clamp-4">
                      {planEvent.reasoning}
                    </div>
                  )}
                  {planEvent.assignments?.length > 0 && (
                    <div className="space-y-1 mt-1">
                      <span className="text-[9px] font-semibold text-violet-400 uppercase">Assignments</span>
                      {planEvent.assignments.map((a: any, i: number) => (
                        <div key={i} className="flex items-start gap-1.5 text-[10px] bg-violet-900/20 rounded-lg p-1.5">
                          <span className="shrink-0 text-violet-400">👤</span>
                          <div className="min-w-0">
                            <span className="font-medium text-violet-200">{a.agent || a.agent_name}</span>
                            {a.role && <span className="text-violet-400/60 ml-1">({a.role})</span>}
                            {(a.task || a.description) && (
                              <div className="text-zinc-400 mt-0.5 line-clamp-2">{a.task || a.description}</div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {planEvent.focus_areas?.length > 0 && (
                    <div className="mt-1">
                      <span className="text-[9px] font-semibold text-violet-400 uppercase">Focus Areas</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {planEvent.focus_areas.map((f: string, i: number) => (
                          <span key={i} className="text-[9px] bg-violet-800/30 text-violet-300 px-1.5 py-0.5 rounded-full">
                            {f}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            }

            case 'evolution_insight': {
              const evoEvent = event as any
              return (
                <div key={event.id} className="border border-amber-500/30 bg-amber-950/20 rounded-xl p-2.5 space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm">🧬</span>
                    <span className="text-[11px] font-semibold text-amber-300">
                      Evolution Agent Observations
                    </span>
                    {evoEvent.severity_score > 0 && (
                      <span className={`text-[9px] ml-auto px-1.5 py-0.5 rounded-full ${
                        evoEvent.severity_score >= 7 ? 'bg-red-900/40 text-red-300' :
                        evoEvent.severity_score >= 4 ? 'bg-amber-900/40 text-amber-300' :
                        'bg-green-900/40 text-green-300'
                      }`}>
                        Severity: {evoEvent.severity_score}/10
                      </span>
                    )}
                  </div>
                  {evoEvent.quality_trajectory && (
                    <div className="text-[10px] text-zinc-300/80">
                      📈 Trajectory: {evoEvent.quality_trajectory}
                    </div>
                  )}
                  {evoEvent.improvement_suggestions?.length > 0 && (
                    <div className="space-y-0.5">
                      <span className="text-[9px] font-semibold text-amber-400 uppercase">Improvements</span>
                      {evoEvent.improvement_suggestions.map((s: string, i: number) => (
                        <div key={i} className="flex items-start gap-1 text-[10px] text-amber-200/80">
                          <span className="shrink-0">💡</span>
                          <span>{s}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {evoEvent.blind_spots?.length > 0 && (
                    <div className="space-y-0.5">
                      <span className="text-[9px] font-semibold text-red-400 uppercase">Blind Spots</span>
                      {evoEvent.blind_spots.map((b: string, i: number) => (
                        <div key={i} className="flex items-start gap-1 text-[10px] text-red-300/80">
                          <span className="shrink-0">🔍</span>
                          <span>{b}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {evoEvent.challenge_questions?.length > 0 && (
                    <div className="space-y-0.5">
                      <span className="text-[9px] font-semibold text-cyan-400 uppercase">Challenge Questions</span>
                      {evoEvent.challenge_questions.map((q: string, i: number) => (
                        <div key={i} className="flex items-start gap-1 text-[10px] text-cyan-300/80">
                          <span className="shrink-0">❓</span>
                          <span>{q}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            }

            default: return null
          }
        })}
      </div>
    </div>
  )
}

function RefreshCw({ className }: { className?: string }) {
    return <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg>
}
