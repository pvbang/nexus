// DEPRECATED - unused after Live tab removal from UnifiedProcessPanel
// ============================================================
// MekongAI Nexus - SubagentTreeView
// Live tree visualization for multi-agent swarm execution
// Shows agent hierarchy, tool executions, token/cost tracking,
// and mini sparkline charts per agent.
// ============================================================

import { useState, useMemo, useCallback, useId } from 'react'
import {
  ChevronRight,
  ChevronDown,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  Wrench,
  BarChart3,
  TreePine,
  Zap,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { Badge } from '../ui/Badge'
import type { ToolExecution, AgentMessage, ProcessStage } from '../../types/process'

// ── Constants ─────────────────────────────────────────────────

/** Approximate cost per token (blended input/output estimate) */
const COST_PER_TOKEN = 0.00001

/** Max sparkline data points */
const MAX_SPARKLINE_POINTS = 10

/** Indent width in pixels per tree level */
const INDENT_PX = 20

// ── Agent Node Data Model ─────────────────────────────────────

type AgentStatus = 'idle' | 'working' | 'waiting' | 'done' | 'error'

interface AgentNode {
  id: string
  name: string
  role: string
  status: AgentStatus
  children: AgentNode[]
  tools: ToolExecution[]
  tokenCount: number
  estimatedCost: number
  tokenHistory: number[]
  startedAt?: number
}

// ── MiniSparkline (pure SVG) ──────────────────────────────────

interface MiniSparklineProps {
  data: number[]
  width?: number
  height?: number
  active?: boolean
}

function MiniSparkline({
  data,
  width = 60,
  height = 16,
  active = true,
}: MiniSparklineProps) {
  const gradientId = useId()

  if (data.length < 2) {
    // Single point or empty → show flat line
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="shrink-0"
        aria-hidden
      >
        <line
          x1={2}
          y1={height - 2}
          x2={width - 2}
          y2={height - 2}
          stroke={active ? '#3b82f6' : '#6b7280'}
          strokeWidth={1.5}
          strokeOpacity={0.4}
        />
      </svg>
    )
  }

  const maxVal = Math.max(...data, 1)
  const padding = 2
  const usableW = width - padding * 2
  const usableH = height - padding * 2

  const points = data.map((val, i) => {
    const x = padding + (i / (data.length - 1)) * usableW
    const y = padding + usableH - (val / maxVal) * usableH
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const polyline = points.join(' ')

  // Area fill: same path but close to bottom
  const firstX = padding
  const lastX = padding + usableW
  const bottom = height - padding
  const areaPath = `M${firstX},${bottom} L${points.join(' L')} L${lastX},${bottom} Z`

  const strokeColor = active ? '#3b82f6' : '#6b7280'
  const lastPoint = data[data.length - 1]
  const lastX2 = padding + usableW
  const lastY = padding + usableH - (lastPoint / maxVal) * usableH

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity={0.3} />
          <stop offset="100%" stopColor={strokeColor} stopOpacity={0.02} />
        </linearGradient>
      </defs>
      {/* Area fill */}
      <path d={areaPath} fill={`url(#${gradientId})`} />
      {/* Line */}
      <polyline
        points={polyline}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Last point highlight */}
      <circle cx={lastX2} cy={lastY} r={2} fill={strokeColor} />
    </svg>
  )
}

// ── Tree Builder ──────────────────────────────────────────────

function inferAgentStatus(
  agentId: string,
  tools: ToolExecution[],
  messages: AgentMessage[],
  stage: ProcessStage,
): AgentStatus {
  if (stage === 'completed') return 'done'
  if (stage === 'failed') {
    const hasFailed = tools.some((t) => t.status === 'failed')
    return hasFailed ? 'error' : 'done'
  }

  if (tools.some((t) => t.status === 'running')) return 'working'
  if (tools.some((t) => t.status === 'failed')) return 'error'

  const hasMessages = messages.some((m) => m.fromAgent.id === agentId)
  const hasCompletedTools = tools.length > 0 && tools.every((t) => t.status === 'completed')

  if (hasCompletedTools && hasMessages) return 'done'
  if (hasMessages || tools.length > 0) return 'working'

  // Agent received delegate but hasn't produced output yet
  const receivedDelegate = messages.some(
    (m) => m.type === 'delegate' && m.toAgent?.id === agentId,
  )
  if (receivedDelegate) return 'waiting'

  return 'idle'
}

function buildAgentTree(
  agentMessages: AgentMessage[],
  toolExecutions: ToolExecution[],
  stage: ProcessStage,
  startTime: number | null,
): AgentNode[] {
  // 1. Collect unique agents
  const agentMap = new Map<string, { id: string; name: string; role: string }>()
  for (const msg of agentMessages) {
    agentMap.set(msg.fromAgent.id, msg.fromAgent)
    if (msg.toAgent) {
      agentMap.set(msg.toAgent.id, msg.toAgent)
    }
  }
  for (const tool of toolExecutions) {
    if (!agentMap.has(tool.agentId)) {
      agentMap.set(tool.agentId, {
        id: tool.agentId,
        name: tool.agentName,
        role: 'agent',
      })
    }
  }

  if (agentMap.size === 0) return []

  // 2. Map tools to agents
  const toolsByAgent = new Map<string, ToolExecution[]>()
  for (const tool of toolExecutions) {
    const list = toolsByAgent.get(tool.agentId) ?? []
    list.push(tool)
    toolsByAgent.set(tool.agentId, list)
  }

  // 3. Compute token counts and history per agent
  const runningTokens = new Map<string, number>()
  const tokenHistory = new Map<string, number[]>()

  for (const msg of agentMessages) {
    const aid = msg.fromAgent.id
    const tokens = Math.ceil(msg.content.length / 4)
    const prev = runningTokens.get(aid) ?? 0
    const next = prev + tokens
    runningTokens.set(aid, next)

    const history = tokenHistory.get(aid) ?? []
    history.push(next)
    // Keep only last N points
    if (history.length > MAX_SPARKLINE_POINTS) {
      history.splice(0, history.length - MAX_SPARKLINE_POINTS)
    }
    tokenHistory.set(aid, history)
  }

  // 4. Build parent-child from delegate messages
  const parentOf = new Map<string, string>() // childId → parentId
  for (const msg of agentMessages) {
    if (msg.type === 'delegate' && msg.toAgent) {
      // First delegation wins (prevents cycles)
      if (!parentOf.has(msg.toAgent.id)) {
        parentOf.set(msg.toAgent.id, msg.fromAgent.id)
      }
    }
  }

  // 5. Determine first message time per agent
  const firstSeen = new Map<string, number>()
  for (const msg of agentMessages) {
    const aid = msg.fromAgent.id
    if (!firstSeen.has(aid)) {
      firstSeen.set(aid, msg.timestamp)
    }
  }

  // 6. Build node objects
  const nodeMap = new Map<string, AgentNode>()
  for (const [id, agent] of agentMap) {
    const tools = toolsByAgent.get(id) ?? []
    const tokens = runningTokens.get(id) ?? 0
    nodeMap.set(id, {
      id,
      name: agent.name,
      role: agent.role,
      status: inferAgentStatus(id, tools, agentMessages, stage),
      children: [],
      tools,
      tokenCount: tokens,
      estimatedCost: tokens * COST_PER_TOKEN,
      tokenHistory: tokenHistory.get(id) ?? [],
      startedAt: firstSeen.get(id) ?? startTime ?? undefined,
    })
  }

  // 7. Assemble tree
  const rootNodes: AgentNode[] = []
  for (const [id, node] of nodeMap) {
    const parentId = parentOf.get(id)
    if (parentId) {
      const parent = nodeMap.get(parentId)
      if (parent) {
        parent.children.push(node)
        continue
      }
    }
    rootNodes.push(node)
  }

  // Sort children by first-seen timestamp
  const sortChildren = (nodes: AgentNode[]) => {
    nodes.sort((a, b) => (a.startedAt ?? 0) - (b.startedAt ?? 0))
    for (const node of nodes) {
      sortChildren(node.children)
    }
  }
  sortChildren(rootNodes)

  return rootNodes
}

// ── Status Icon ───────────────────────────────────────────────

function AgentStatusIcon({ status }: { status: AgentStatus }) {
  switch (status) {
    case 'working':
      return <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin shrink-0" />
    case 'done':
      return <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
    case 'error':
      return <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
    case 'waiting':
      return <Clock className="w-3.5 h-3.5 text-amber-500 shrink-0" />
    case 'idle':
    default:
      return <Clock className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0" />
  }
}

const STATUS_LABELS: Record<AgentStatus, string> = {
  idle: 'Idle',
  working: 'Working',
  waiting: 'Waiting',
  done: 'Done',
  error: 'Error',
}

// ── Role color mapping ────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  orchestrator: 'bg-purple-500',
  coordinator: 'bg-purple-500',
  researcher: 'bg-blue-500',
  analyst: 'bg-emerald-500',
  writer: 'bg-orange-500',
  coder: 'bg-amber-500',
  reviewer: 'bg-rose-500',
  judge: 'bg-red-500',
}

function roleColor(role: string): string {
  const key = (role || '').toLowerCase()
  return ROLE_COLORS[key] ?? 'bg-gray-500'
}

// ── Tool Status Line ──────────────────────────────────────────

function ToolLine({ tool }: { tool: ToolExecution }) {
  const durationText = tool.duration
    ? `${(tool.duration / 1000).toFixed(1)}s`
    : tool.status === 'running'
      ? '...'
      : ''

  return (
    <div className="flex items-center gap-1.5 py-0.5 text-micro text-text-muted dark:text-gray-500">
      <Wrench className="w-3 h-3 shrink-0" />
      <span className="font-mono truncate">{tool.toolName}</span>
      {tool.status === 'running' && (
        <Loader2 className="w-2.5 h-2.5 animate-spin text-blue-500 shrink-0" />
      )}
      {tool.status === 'completed' && (
        <CheckCircle className="w-2.5 h-2.5 text-emerald-500 shrink-0" />
      )}
      {tool.status === 'failed' && (
        <XCircle className="w-2.5 h-2.5 text-red-500 shrink-0" />
      )}
      {durationText && (
        <span className="font-data tabular-nums ml-auto shrink-0">
          {durationText}
        </span>
      )}
    </div>
  )
}

// ── Recursive Agent Tree Node ─────────────────────────────────

interface AgentTreeNodeProps {
  node: AgentNode
  depth: number
  collapsedNodes: Set<string>
  onToggle: (id: string) => void
}

function AgentTreeNode({ node, depth, collapsedNodes, onToggle }: AgentTreeNodeProps) {
  const isCollapsed = collapsedNodes.has(node.id)
  const hasChildren = (node.children?.length ?? 0) > 0 || (node.tools?.length ?? 0) > 0
  const isActive = node.status === 'working'
  const paddingLeft = depth * INDENT_PX

  const formattedTokens = node.tokenCount.toLocaleString()
  const formattedCost = node.estimatedCost < 0.001
    ? '<$0.001'
    : `~$${node.estimatedCost.toFixed(3)}`

  return (
    <div
      className="animate-fade-in"
      style={{ '--tree-depth': depth } as React.CSSProperties}
    >
      {/* Agent row */}
      <div
        className={cn(
          'flex items-center gap-1.5 py-1.5 px-2 rounded-lg transition-colors cursor-pointer group',
          'hover:bg-gray-50 dark:hover:bg-white/[0.03]',
          isActive && 'bg-blue-50/50 dark:bg-blue-900/10',
        )}
        style={{ paddingLeft: `${paddingLeft + 8}px` }}
        onClick={() => hasChildren && onToggle(node.id)}
        role={hasChildren ? 'button' : undefined}
        tabIndex={hasChildren ? 0 : undefined}
        onKeyDown={(e) => {
          if (hasChildren && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault()
            onToggle(node.id)
          }
        }}
      >
        {/* Tree connector line */}
        {depth > 0 && (
          <span className="text-micro text-gray-300 dark:text-gray-600 select-none shrink-0">
            {hasChildren ? '' : '─'}
          </span>
        )}

        {/* Expand/collapse chevron */}
        {hasChildren ? (
          <span className="shrink-0 text-gray-400 dark:text-gray-500">
            {isCollapsed ? (
              <ChevronRight className="w-3 h-3" />
            ) : (
              <ChevronDown className="w-3 h-3" />
            )}
          </span>
        ) : (
          <span className="w-3 shrink-0" />
        )}

        {/* Role avatar */}
        <span
          className={cn(
            'w-5 h-5 rounded-full flex items-center justify-center text-white text-micro font-bold shrink-0',
            roleColor(node.role),
          )}
          title={node.role}
        >
          {node.name.charAt(0).toUpperCase()}
        </span>

        {/* Agent name + role */}
        <div className="flex-1 min-w-0 flex items-center gap-1">
          <span className="text-tiny font-medium text-text-primary dark:text-gray-200 truncate">
            {node.name}
          </span>
          {depth === 0 && (
            <Badge variant="purple" size="sm" className="text-micro">
              {node.role}
            </Badge>
          )}
        </div>

        {/* Status */}
        <AgentStatusIcon status={node.status} />
        <span className={cn(
          'text-micro font-medium shrink-0',
          node.status === 'working' && 'text-blue-500',
          node.status === 'done' && 'text-emerald-500',
          node.status === 'error' && 'text-red-500',
          node.status === 'waiting' && 'text-amber-500',
          node.status === 'idle' && 'text-gray-400',
        )}>
          {STATUS_LABELS[node.status]}
        </span>
      </div>

      {/* Expanded content */}
      {!isCollapsed && (
        <div
          className={cn(
            'overflow-hidden transition-all duration-200',
            depth > 0 && 'border-l border-gray-200/60 dark:border-gray-700/40',
          )}
          style={{ marginLeft: `${paddingLeft + 16}px` }}
        >
          {/* Tool executions */}
          {(node.tools?.length ?? 0) > 0 && (
            <div className="pl-2 py-0.5">
              {node.tools.map((tool) => (
                <ToolLine key={tool.id} tool={tool} />
              ))}
            </div>
          )}

          {/* Token/cost line with sparkline */}
          {node.tokenCount > 0 && (
            <div className="flex items-center gap-1.5 pl-2 py-0.5 text-micro text-text-muted dark:text-gray-500">
              <BarChart3 className="w-3 h-3 shrink-0" />
              <span className="font-data tabular-nums">
                {formattedTokens} tokens
              </span>
              <span className="text-gray-300 dark:text-gray-600">·</span>
              <span className="font-data tabular-nums">{formattedCost}</span>
              <MiniSparkline
                data={node.tokenHistory}
                active={isActive}
                width={60}
                height={16}
              />
            </div>
          )}

          {/* Recursive children */}
          {node.children.map((child) => (
            <AgentTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              collapsedNodes={collapsedNodes}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Summary Footer ────────────────────────────────────────────

interface TreeSummaryProps {
  nodes: AgentNode[]
  startTime: number | null
}

function collectAllNodes(nodes: AgentNode[]): AgentNode[] {
  const result: AgentNode[] = []
  const stack = [...nodes]
  while (stack.length > 0) {
    const node = stack.pop()
    if (node) {
      result.push(node)
      stack.push(...node.children)
    }
  }
  return result
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${minutes}m ${secs}s`
}

function TreeSummaryFooter({ nodes, startTime }: TreeSummaryProps) {
  const allNodes = useMemo(() => collectAllNodes(nodes), [nodes])

  const totalTokens = useMemo(
    () => allNodes.reduce((sum, n) => sum + n.tokenCount, 0),
    [allNodes],
  )

  const totalCost = useMemo(
    () => allNodes.reduce((sum, n) => sum + n.estimatedCost, 0),
    [allNodes],
  )

  const agentCount = allNodes.length
  const activeCount = allNodes.filter(
    (n) => n.status === 'working' || n.status === 'waiting',
  ).length

  const elapsed = startTime ? Date.now() - startTime : 0

  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-2 text-micro border-t',
        'bg-gray-50/80 dark:bg-[#111125]/80',
        'border-gray-200 dark:border-[#2a2a45]',
        'text-text-muted dark:text-gray-400',
      )}
    >
      <Zap className="w-3 h-3 shrink-0" />
      <span className="font-data tabular-nums font-medium">
        {totalTokens.toLocaleString()} tokens
      </span>
      <span className="text-gray-300 dark:text-gray-600">·</span>
      <span className="font-data tabular-nums">
        ~${totalCost.toFixed(3)}
      </span>
      <span className="text-gray-300 dark:text-gray-600">·</span>
      <span>
        {activeCount > 0
          ? `${activeCount}/${agentCount} active`
          : `${agentCount} agents`}
      </span>
      {elapsed > 0 && (
        <>
          <span className="text-gray-300 dark:text-gray-600">·</span>
          <span className="font-data tabular-nums">
            {formatDuration(elapsed)}
          </span>
        </>
      )}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────

export function SubagentTreeView() {
  const agentMessages = useProcessStore((s) => s.agentMessages)
  const toolExecutions = useProcessStore((s) => s.toolExecutions)
  const stage = useProcessStore((s) => s.stage)
  const startTime = useProcessStore((s) => s.startTime)

  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(new Set())

  const tree = useMemo(
    () => buildAgentTree(agentMessages, toolExecutions, stage, startTime),
    [agentMessages, toolExecutions, stage, startTime],
  )

  // Count total unique agents in tree
  const totalAgents = useMemo(() => collectAllNodes(tree).length, [tree])

  const handleToggle = useCallback((id: string) => {
    setCollapsedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  // Self-hiding: only show when >= 2 agents (swarm mode)
  if (totalAgents < 2) return null

  return (
    <div
      className={cn(
        'rounded-comfortable border overflow-hidden mb-3 animate-fade-in',
        'bg-white dark:bg-[#13132b]',
        'border-gray-200 dark:border-[#2a2a45]',
      )}
    >
      {/* Header */}
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 border-b',
          'border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        <TreePine className="w-4 h-4 text-emerald-500 shrink-0" />
        <span className="text-tiny font-semibold text-text-primary dark:text-gray-200">
          Agent Tree
        </span>

        {/* Live indicator */}
        {stage !== 'completed' && stage !== 'failed' && stage !== 'idle' && (
          <Badge variant="success" size="sm" dot className="text-micro">
            Live
          </Badge>
        )}

        <span className="ml-auto text-micro text-text-muted dark:text-gray-500">
          {totalAgents} agent{totalAgents !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Tree body */}
      <div className="py-1 max-h-[400px] overflow-y-auto">
        {tree.map((root) => (
          <AgentTreeNode
            key={root.id}
            node={root}
            depth={0}
            collapsedNodes={collapsedNodes}
            onToggle={handleToggle}
          />
        ))}
      </div>

      {/* Summary footer */}
      <TreeSummaryFooter nodes={tree} startTime={startTime} />
    </div>
  )
}
