// ============================================================
// MekongAI Nexus - TaskFlowGraph
// Interactive SVG graph: root task → subtasks → agents → tools
// ============================================================

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  type WheelEvent as ReactWheelEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import {
  Wrench,
  ZoomIn,
  ZoomOut,
  Maximize2,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { apiGet } from '../../api'
import { Spinner } from '../ui/Spinner'
import { EmptyState } from '../ui/EmptyState'
import { Badge } from '../ui/Badge'
import type { Task } from '../../types/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type GraphNodeType = 'task' | 'subtask' | 'agent' | 'tool'
type GraphNodeStatus = 'pending' | 'running' | 'completed' | 'failed'

interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  status: GraphNodeStatus
  x: number
  y: number
  metadata?: Record<string, string>
}

interface GraphEdge {
  from: string
  to: string
  label?: string
  animated?: boolean
}

interface TooltipData {
  node: GraphNode
  screenX: number
  screenY: number
}

interface TaskFlowGraphProps {
  taskId: string
  className?: string
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LAYER_HEIGHT = 120
const NODE_GAP = 100
const SVG_PADDING = 60

const NODE_DIMS: Record<GraphNodeType, { w: number; h: number }> = {
  task: { w: 160, h: 48 },
  subtask: { w: 140, h: 40 },
  agent: { w: 44, h: 44 },   // circle — diameter
  tool: { w: 120, h: 36 },
}

const STATUS_COLORS: Record<GraphNodeStatus, { stroke: string; fill: string; text: string }> = {
  pending: {
    stroke: '#9ca3af',
    fill: '#f9fafb',
    text: '#6b7280',
  },
  running: {
    stroke: '#3b82f6',
    fill: '#eff6ff',
    text: '#1d4ed8',
  },
  completed: {
    stroke: '#10b981',
    fill: '#ecfdf5',
    text: '#059669',
  },
  failed: {
    stroke: '#ef4444',
    fill: '#fef2f2',
    text: '#dc2626',
  },
}

const DARK_STATUS_COLORS: Record<GraphNodeStatus, { stroke: string; fill: string; text: string }> = {
  pending: {
    stroke: '#4b5563',
    fill: '#1f2937',
    text: '#9ca3af',
  },
  running: {
    stroke: '#60a5fa',
    fill: '#1e3a5f',
    text: '#93c5fd',
  },
  completed: {
    stroke: '#34d399',
    fill: '#064e3b',
    text: '#6ee7b7',
  },
  failed: {
    stroke: '#f87171',
    fill: '#450a0a',
    text: '#fca5a5',
  },
}

const ROLE_COLORS: Record<string, string> = {
  researcher: '#3b82f6',
  coder: '#10b981',
  writer: '#8b5cf6',
  analyst: '#f59e0b',
  critic: '#ef4444',
  default: '#6366f1',
}

// ---------------------------------------------------------------------------
// Layout algorithm
// ---------------------------------------------------------------------------

function calculateLayout(
  nodes: GraphNode[],
  _edges: GraphEdge[],
  containerWidth: number,
): void {
  const layers: GraphNode[][] = [[], [], [], []]
  const typeToLayer: Record<GraphNodeType, number> = {
    task: 0,
    subtask: 1,
    agent: 2,
    tool: 3,
  }

  for (const node of nodes) {
    layers[typeToLayer[node.type]].push(node)
  }

  for (let layerIdx = 0; layerIdx < layers.length; layerIdx++) {
    const layer = layers[layerIdx]
    if (layer.length === 0) continue

    const y = layerIdx * LAYER_HEIGHT + SVG_PADDING
    const totalWidth = layer.length * NODE_GAP
    const startX = Math.max(SVG_PADDING, (containerWidth - totalWidth) / 2)

    for (let nodeIdx = 0; nodeIdx < layer.length; nodeIdx++) {
      layer[nodeIdx].x = startX + nodeIdx * NODE_GAP + NODE_GAP / 2
      layer[nodeIdx].y = y
    }
  }
}

function computeViewBox(nodes: GraphNode[]): { x: number; y: number; w: number; h: number } {
  if (nodes.length === 0) return { x: 0, y: 0, w: 800, h: 400 }
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const n of nodes) {
    const dim = NODE_DIMS[n.type]
    minX = Math.min(minX, n.x - dim.w / 2)
    minY = Math.min(minY, n.y - dim.h / 2)
    maxX = Math.max(maxX, n.x + dim.w / 2)
    maxY = Math.max(maxY, n.y + dim.h / 2)
  }
  const pad = 40
  return {
    x: minX - pad,
    y: minY - pad,
    w: maxX - minX + pad * 2,
    h: maxY - minY + pad * 2,
  }
}

// ---------------------------------------------------------------------------
// Data extraction helpers
// ---------------------------------------------------------------------------

function normalizeStatus(status: string): GraphNodeStatus {
  if (['completed', 'done'].includes(status)) return 'completed'
  if (['executing', 'running'].includes(status)) return 'running'
  if (['failed', 'cancelled'].includes(status)) return 'failed'
  return 'pending'
}

interface SubtaskRaw {
  id?: string
  title?: string
  name?: string
  description?: string
  status?: string
  agent?: string
  agent_name?: string
  children?: SubtaskRaw[]
  subtasks?: SubtaskRaw[]
}

function extractGraphData(
  task: Task,
  agentMessages: Array<{ fromAgent: { id: string; name: string; role: string } }>,
  toolExecutions: Array<{ agentId: string; agentName: string; toolName: string; status: string }>,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  const seenAgents = new Map<string, string>() // agentId -> nodeId
  const seenTools = new Map<string, string>()  // agentId+toolName -> nodeId

  // Root task node
  nodes.push({
    id: `task-${task.id}`,
    type: 'task',
    label: task.title.length > 24 ? task.title.slice(0, 22) + '…' : task.title,
    status: normalizeStatus(task.status),
    x: 0,
    y: 0,
    metadata: { fullTitle: task.title, taskId: task.id },
  })

  // Extract subtasks from result
  const result = task.result as Record<string, unknown> | undefined
  const rawSubtasks = extractRawSubtasks(result)

  for (let i = 0; i < rawSubtasks.length; i++) {
    const sub = rawSubtasks[i]
    const subId = `subtask-${sub.id ?? i}`
    const subLabel = (sub.title ?? sub.name ?? sub.description ?? `Subtask ${i + 1}`)
    nodes.push({
      id: subId,
      type: 'subtask',
      label: subLabel.length > 20 ? subLabel.slice(0, 18) + '…' : subLabel,
      status: normalizeStatus(sub.status ?? 'pending'),
      x: 0,
      y: 0,
      metadata: { fullTitle: subLabel },
    })
    edges.push({
      from: `task-${task.id}`,
      to: subId,
      animated: normalizeStatus(sub.status ?? 'pending') === 'running',
    })

    // Agent linked to subtask
    const agentName = sub.agent ?? sub.agent_name
    if (agentName) {
      const agentNodeId = `agent-${agentName}`
      if (!seenAgents.has(agentName)) {
        seenAgents.set(agentName, agentNodeId)
        nodes.push({
          id: agentNodeId,
          type: 'agent',
          label: agentName.length > 10 ? agentName.slice(0, 8) + '…' : agentName,
          status: 'running',
          x: 0,
          y: 0,
          metadata: { fullName: agentName, role: 'default' },
        })
      }
      edges.push({ from: subId, to: agentNodeId })
    }
  }

  // Agents from live messages
  for (const msg of agentMessages) {
    const { id, name, role } = msg.fromAgent
    if (!seenAgents.has(id)) {
      const agentNodeId = `agent-${id}`
      seenAgents.set(id, agentNodeId)
      nodes.push({
        id: agentNodeId,
        type: 'agent',
        label: name.length > 10 ? name.slice(0, 8) + '…' : name,
        status: 'running',
        x: 0,
        y: 0,
        metadata: { fullName: name, role },
      })
      // Connect to root task if no subtask edges exist
      if (rawSubtasks.length === 0) {
        edges.push({ from: `task-${task.id}`, to: agentNodeId, animated: true })
      }
    }
  }

  // Tools from live tool executions
  for (const exec of toolExecutions) {
    const toolKey = `${exec.agentId}-${exec.toolName}`
    if (!seenTools.has(toolKey)) {
      const toolNodeId = `tool-${toolKey}`
      seenTools.set(toolKey, toolNodeId)
      nodes.push({
        id: toolNodeId,
        type: 'tool',
        label: exec.toolName.length > 14 ? exec.toolName.slice(0, 12) + '…' : exec.toolName,
        status: normalizeStatus(exec.status),
        x: 0,
        y: 0,
        metadata: { toolName: exec.toolName },
      })
      // Connect tool to agent
      const agentNodeId = seenAgents.get(exec.agentId) ?? seenAgents.get(exec.agentName)
      if (agentNodeId) {
        edges.push({ from: agentNodeId, to: toolNodeId, animated: exec.status === 'running' })
      }
    }
  }

  return { nodes, edges }
}

function extractRawSubtasks(result: Record<string, unknown> | undefined): SubtaskRaw[] {
  if (!result) return []
  if (Array.isArray(result.subtasks)) return result.subtasks as SubtaskRaw[]
  if (result.plan && typeof result.plan === 'object') {
    const plan = result.plan as Record<string, unknown>
    if (Array.isArray(plan.steps)) return plan.steps as SubtaskRaw[]
    if (Array.isArray(plan.subtasks)) return plan.subtasks as SubtaskRaw[]
  }
  if (Array.isArray(result.steps)) return result.steps as SubtaskRaw[]
  return []
}

// ---------------------------------------------------------------------------
// SVG Node renderers
// ---------------------------------------------------------------------------

function TaskNode({
  node,
  highlighted,
  isDark,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: {
  node: GraphNode
  highlighted: boolean
  isDark: boolean
  onClick: (node: GraphNode, e: ReactMouseEvent) => void
  onMouseEnter: (node: GraphNode) => void
  onMouseLeave: () => void
}) {
  const colors = isDark ? DARK_STATUS_COLORS[node.status] : STATUS_COLORS[node.status]
  const dim = NODE_DIMS[node.type]

  return (
    <g
      className="cursor-pointer transition-opacity"
      style={{ opacity: highlighted ? 1 : 0.4 }}
      onClick={(e) => onClick(node, e)}
      onMouseEnter={() => onMouseEnter(node)}
      onMouseLeave={onMouseLeave}
    >
      <rect
        x={node.x - dim.w / 2}
        y={node.y - dim.h / 2}
        width={dim.w}
        height={dim.h}
        rx={10}
        ry={10}
        fill={colors.fill}
        stroke={colors.stroke}
        strokeWidth={2}
      />
      {node.status === 'running' && (
        <rect
          x={node.x - dim.w / 2}
          y={node.y - dim.h / 2}
          width={dim.w}
          height={dim.h}
          rx={10}
          ry={10}
          fill="none"
          stroke={colors.stroke}
          strokeWidth={2}
          opacity={0.5}
        >
          <animate
            attributeName="stroke-width"
            values="2;4;2"
            dur="1.5s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0.5;0.2;0.5"
            dur="1.5s"
            repeatCount="indefinite"
          />
        </rect>
      )}
      {node.status === 'completed' && (
        <g transform={`translate(${node.x + dim.w / 2 - 12}, ${node.y - dim.h / 2 - 6})`}>
          <circle cx={8} cy={8} r={8} fill={colors.stroke} />
          <path d="M5 8l2 2 4-4" stroke="#fff" strokeWidth={1.5} fill="none" strokeLinecap="round" />
        </g>
      )}
      {node.status === 'failed' && (
        <g transform={`translate(${node.x + dim.w / 2 - 12}, ${node.y - dim.h / 2 - 6})`}>
          <circle cx={8} cy={8} r={8} fill={colors.stroke} />
          <path d="M5 5l6 6M11 5l-6 6" stroke="#fff" strokeWidth={1.5} strokeLinecap="round" />
        </g>
      )}
      <text
        x={node.x}
        y={node.y + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fill={colors.text}
        fontSize={node.type === 'task' ? 13 : node.type === 'subtask' ? 12 : 11}
        fontWeight={node.type === 'task' ? 600 : 500}
        fontFamily="DM Sans, sans-serif"
      >
        {node.label}
      </text>
    </g>
  )
}

function AgentNode({
  node,
  highlighted,
  isDark,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: {
  node: GraphNode
  highlighted: boolean
  isDark: boolean
  onClick: (node: GraphNode, e: ReactMouseEvent) => void
  onMouseEnter: (node: GraphNode) => void
  onMouseLeave: () => void
}) {
  const role = node.metadata?.role ?? 'default'
  const roleColor = ROLE_COLORS[role] ?? ROLE_COLORS.default
  const dim = NODE_DIMS.agent
  const r = dim.w / 2

  return (
    <g
      className="cursor-pointer transition-opacity"
      style={{ opacity: highlighted ? 1 : 0.4 }}
      onClick={(e) => onClick(node, e)}
      onMouseEnter={() => onMouseEnter(node)}
      onMouseLeave={onMouseLeave}
    >
      <circle
        cx={node.x}
        cy={node.y}
        r={r}
        fill={isDark ? `${roleColor}30` : `${roleColor}18`}
        stroke={roleColor}
        strokeWidth={2}
      />
      {node.status === 'running' && (
        <circle
          cx={node.x}
          cy={node.y}
          r={r}
          fill="none"
          stroke={roleColor}
          strokeWidth={2}
          opacity={0.4}
        >
          <animate
            attributeName="r"
            values={`${r};${r + 4};${r}`}
            dur="1.5s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0.4;0;0.4"
            dur="1.5s"
            repeatCount="indefinite"
          />
        </circle>
      )}
      <text
        x={node.x}
        y={node.y + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fill={isDark ? '#e5e7eb' : '#374151'}
        fontSize={10}
        fontWeight={500}
        fontFamily="DM Sans, sans-serif"
      >
        {node.label}
      </text>
    </g>
  )
}

function ToolNode({
  node,
  highlighted,
  isDark,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: {
  node: GraphNode
  highlighted: boolean
  isDark: boolean
  onClick: (node: GraphNode, e: ReactMouseEvent) => void
  onMouseEnter: (node: GraphNode) => void
  onMouseLeave: () => void
}) {
  const colors = isDark ? DARK_STATUS_COLORS[node.status] : STATUS_COLORS[node.status]
  const dim = NODE_DIMS.tool

  return (
    <g
      className="cursor-pointer transition-opacity"
      style={{ opacity: highlighted ? 1 : 0.4 }}
      onClick={(e) => onClick(node, e)}
      onMouseEnter={() => onMouseEnter(node)}
      onMouseLeave={onMouseLeave}
    >
      <rect
        x={node.x - dim.w / 2}
        y={node.y - dim.h / 2}
        width={dim.w}
        height={dim.h}
        rx={6}
        ry={6}
        fill={isDark ? '#1f2937' : '#f3f4f6'}
        stroke={isDark ? '#4b5563' : '#d1d5db'}
        strokeWidth={1.5}
        strokeDasharray="4 2"
      />
      {/* Wrench icon placeholder */}
      <text
        x={node.x - dim.w / 2 + 14}
        y={node.y + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={12}
      >
        🔧
      </text>
      <text
        x={node.x + 6}
        y={node.y + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fill={colors.text}
        fontSize={10}
        fontWeight={500}
        fontFamily="DM Sans, sans-serif"
      >
        {node.label}
      </text>
    </g>
  )
}

// ---------------------------------------------------------------------------
// Edge renderer
// ---------------------------------------------------------------------------

function EdgePath({
  edge,
  nodes,
  highlighted,
  isDark,
}: {
  edge: GraphEdge
  nodes: GraphNode[]
  highlighted: boolean
  isDark: boolean
}) {
  const fromNode = nodes.find((n) => n.id === edge.from)
  const toNode = nodes.find((n) => n.id === edge.to)
  if (!fromNode || !toNode) return null

  const fromDim = NODE_DIMS[fromNode.type]
  const toDim = NODE_DIMS[toNode.type]

  const x1 = fromNode.x
  const y1 = fromNode.type === 'agent'
    ? fromNode.y + fromDim.h / 2
    : fromNode.y + fromDim.h / 2
  const x2 = toNode.x
  const y2 = toNode.type === 'agent'
    ? toNode.y - toDim.h / 2
    : toNode.y - toDim.h / 2

  const midY = (y1 + y2) / 2
  const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`
  const color = isDark ? '#4b5563' : '#d1d5db'
  const highlightColor = isDark ? '#60a5fa' : '#3b82f6'

  return (
    <g style={{ opacity: highlighted ? 1 : 0.3 }}>
      <path
        d={d}
        fill="none"
        stroke={highlighted ? highlightColor : color}
        strokeWidth={highlighted ? 2 : 1.5}
        markerEnd="url(#arrowhead)"
      />
      {edge.animated && (
        <circle r={3} fill={highlightColor}>
          <animateMotion dur="2s" repeatCount="indefinite" path={d} />
        </circle>
      )}
    </g>
  )
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

function NodeTooltip({ data, onClose }: { data: TooltipData; onClose: () => void }) {
  const { node } = data

  return (
    <div
      className={cn(
        'absolute z-50 px-3 py-2 rounded-lg shadow-lg border max-w-[240px]',
        'bg-white border-gray-200 dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
        'animate-fade-in',
      )}
      style={{
        left: data.screenX,
        top: data.screenY,
        transform: 'translate(-50%, -100%) translateY(-12px)',
      }}
    >
      <div className="flex items-center gap-2 mb-1">
        <Badge
          variant={
            node.status === 'completed' ? 'success' :
            node.status === 'failed' ? 'error' :
            node.status === 'running' ? 'info' : 'default'
          }
          size="sm"
        >
          {node.type}
        </Badge>
        <span className="text-xs font-medium text-gray-700 dark:text-gray-200 truncate">
          {node.metadata?.fullTitle ?? node.metadata?.fullName ?? node.label}
        </span>
      </div>
      <div className="text-[11px] text-gray-500 dark:text-gray-400 space-y-0.5">
        <p>Status: <span className="capitalize font-medium">{node.status}</span></p>
        {node.metadata?.role && node.metadata.role !== 'default' && (
          <p>Role: <span className="capitalize">{node.metadata.role}</span></p>
        )}
        {node.metadata?.toolName && (
          <p>Tool: {node.metadata.toolName}</p>
        )}
        {node.metadata?.taskId && (
          <p className="font-mono">{node.metadata.taskId.slice(0, 12)}…</p>
        )}
      </div>
      <button
        type="button"
        onClick={onClose}
        className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-xs"
        aria-label="Close tooltip"
      >
        ×
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TaskFlowGraph({ taskId, className }: TaskFlowGraphProps) {
  const [task, setTask] = useState<Task | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Live process data
  const agentMessages = useProcessStore((s) => s.agentMessages)
  const toolExecutions = useProcessStore((s) => s.toolExecutions)

  // Zoom/pan state
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })

  // Interaction state
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null)
  const [tooltip, setTooltip] = useState<TooltipData | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)
  const isDark = useMemo(() => {
    if (typeof document === 'undefined') return false
    return document.documentElement.classList.contains('dark')
  }, [])

  // Fetch task — retry on 404 (task may not be committed yet during SSE stream)
  useEffect(() => {
    let cancelled = false
    let retryCount = 0
    const maxRetries = 3
    setLoading(true)
    setError(null)

    const fetchTask = () => {
      apiGet<Task>(`/tasks/${taskId}`)
        .then((data) => {
          if (!cancelled) {
            setTask(data)
            setLoading(false)
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return
          const msg = err instanceof Error ? err.message : 'Failed to load task'
          // Retry on 404 (task not committed yet) up to maxRetries
          if (msg.includes('404') && retryCount < maxRetries) {
            retryCount++
            setTimeout(fetchTask, 2000 * retryCount) // 2s, 4s, 6s
          } else {
            setError(msg)
            setLoading(false)
          }
        })
    }
    fetchTask()

    return () => { cancelled = true }
  }, [taskId])

  // Build graph data
  const { nodes, edges } = useMemo(() => {
    if (!task) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] }
    const containerWidth = containerRef.current?.clientWidth ?? 800
    const data = extractGraphData(task, agentMessages, toolExecutions)
    calculateLayout(data.nodes, data.edges, containerWidth)
    return data
  }, [task, agentMessages, toolExecutions])

  const viewBox = useMemo(() => computeViewBox(nodes), [nodes])

  // Connected node IDs for highlighting
  const connectedIds = useMemo(() => {
    if (!hoveredNodeId) return null
    const ids = new Set<string>([hoveredNodeId])
    for (const edge of edges) {
      if (edge.from === hoveredNodeId) ids.add(edge.to)
      if (edge.to === hoveredNodeId) ids.add(edge.from)
    }
    return ids
  }, [hoveredNodeId, edges])

  const isHighlighted = useCallback(
    (nodeId: string): boolean => {
      if (!connectedIds) return true
      return connectedIds.has(nodeId)
    },
    [connectedIds],
  )

  const isEdgeHighlighted = useCallback(
    (edge: GraphEdge): boolean => {
      if (!connectedIds) return true
      return connectedIds.has(edge.from) && connectedIds.has(edge.to)
    },
    [connectedIds],
  )

  // Zoom handler
  const handleWheel = useCallback((e: ReactWheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.1 : 0.1
    setZoom((prev) => Math.max(0.3, Math.min(3, prev + delta)))
  }, [])

  // Pan handlers
  const handleMouseDown = useCallback((e: ReactMouseEvent) => {
    if (e.target === e.currentTarget || (e.target as SVGElement).tagName === 'svg') {
      setDragging(true)
      dragStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y }
    }
  }, [pan])

  const handleMouseMove = useCallback(
    (e: ReactMouseEvent) => {
      if (!dragging) return
      const dx = e.clientX - dragStart.current.x
      const dy = e.clientY - dragStart.current.y
      setPan({ x: dragStart.current.panX + dx, y: dragStart.current.panY + dy })
    },
    [dragging],
  )

  const handleMouseUp = useCallback(() => {
    setDragging(false)
  }, [])

  // Node click → show tooltip
  const handleNodeClick = useCallback((node: GraphNode, e: ReactMouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    setTooltip({
      node,
      screenX: e.clientX - rect.left,
      screenY: e.clientY - rect.top,
    })
  }, [])

  // Auto-fit
  const handleFitView = useCallback(() => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }, [])

  // --- Loading ---
  if (loading) {
    return (
      <div className={cn('flex items-center justify-center py-16', className)}>
        <Spinner size="md" className="text-brand-500" />
      </div>
    )
  }

  // --- Error ---
  if (error) {
    return (
      <EmptyState
        icon="⚠️"
        title="Failed to load graph"
        description={error}
        className={className}
      />
    )
  }

  // --- Empty ---
  if (nodes.length === 0) {
    return (
      <EmptyState
        icon="🔗"
        title="No flow data"
        description="No task flow graph available yet."
        className={className}
      />
    )
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative w-full h-[420px] rounded-lg overflow-hidden border select-none',
        'bg-white border-gray-200 dark:bg-[#12122a] dark:border-[#2a2a45]',
        dragging ? 'cursor-grabbing' : 'cursor-grab',
        className,
      )}
    >
      {/* Toolbar */}
      <div className={cn(
        'absolute top-3 right-3 z-10 flex items-center gap-1',
      )}>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(3, z + 0.2))}
          className={cn(
            'p-1.5 rounded-md transition-colors',
            'bg-white/80 hover:bg-gray-100 border border-gray-200',
            'dark:bg-[#1a1a2e]/80 dark:hover:bg-[#2a2a45] dark:border-[#2a2a45]',
          )}
          aria-label="Zoom in"
        >
          <ZoomIn className="h-3.5 w-3.5 text-gray-600 dark:text-gray-400" />
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.3, z - 0.2))}
          className={cn(
            'p-1.5 rounded-md transition-colors',
            'bg-white/80 hover:bg-gray-100 border border-gray-200',
            'dark:bg-[#1a1a2e]/80 dark:hover:bg-[#2a2a45] dark:border-[#2a2a45]',
          )}
          aria-label="Zoom out"
        >
          <ZoomOut className="h-3.5 w-3.5 text-gray-600 dark:text-gray-400" />
        </button>
        <button
          type="button"
          onClick={handleFitView}
          className={cn(
            'p-1.5 rounded-md transition-colors',
            'bg-white/80 hover:bg-gray-100 border border-gray-200',
            'dark:bg-[#1a1a2e]/80 dark:hover:bg-[#2a2a45] dark:border-[#2a2a45]',
          )}
          aria-label="Fit view"
        >
          <Maximize2 className="h-3.5 w-3.5 text-gray-600 dark:text-gray-400" />
        </button>
      </div>

      {/* Legend */}
      <div className={cn(
        'absolute bottom-3 left-3 z-10 flex items-center gap-3 px-2.5 py-1.5 rounded-md text-[10px]',
        'bg-white/80 border border-gray-200 dark:bg-[#1a1a2e]/80 dark:border-[#2a2a45]',
        'text-gray-500 dark:text-gray-400',
      )}>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 rounded-sm border-2 border-blue-500 bg-blue-50 dark:bg-blue-900/30 inline-block" />
          Task
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 rounded-sm border-2 border-blue-300 bg-blue-50/50 dark:bg-blue-900/20 inline-block" />
          Subtask
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full border-2 border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 inline-block" />
          Agent
        </span>
        <span className="flex items-center gap-1">
          <Wrench className="h-2.5 w-2.5" />
          Tool
        </span>
      </div>

      {/* Zoom indicator */}
      <div className={cn(
        'absolute top-3 left-3 z-10 px-2 py-1 rounded text-[10px] font-mono',
        'bg-white/80 border border-gray-200 text-gray-500',
        'dark:bg-[#1a1a2e]/80 dark:border-[#2a2a45] dark:text-gray-400',
      )}>
        {Math.round(zoom * 100)}%
      </div>

      {/* SVG Graph */}
      <svg
        width="100%"
        height="100%"
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{ userSelect: 'none' }}
      >
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="6"
            refX="8"
            refY="3"
            orient="auto"
          >
            <polygon
              points="0 0, 8 3, 0 6"
              fill={isDark ? '#4b5563' : '#d1d5db'}
            />
          </marker>
        </defs>

        <g transform={`translate(${pan.x / zoom}, ${pan.y / zoom}) scale(${zoom})`}>
          {/* Edges */}
          {edges.map((edge, i) => (
            <EdgePath
              key={`edge-${i}`}
              edge={edge}
              nodes={nodes}
              highlighted={isEdgeHighlighted(edge)}
              isDark={isDark}
            />
          ))}

          {/* Nodes */}
          {nodes.map((node) => {
            const hl = isHighlighted(node.id)
            if (node.type === 'agent') {
              return (
                <AgentNode
                  key={node.id}
                  node={node}
                  highlighted={hl}
                  isDark={isDark}
                  onClick={handleNodeClick}
                  onMouseEnter={(n) => setHoveredNodeId(n.id)}
                  onMouseLeave={() => setHoveredNodeId(null)}
                />
              )
            }
            if (node.type === 'tool') {
              return (
                <ToolNode
                  key={node.id}
                  node={node}
                  highlighted={hl}
                  isDark={isDark}
                  onClick={handleNodeClick}
                  onMouseEnter={(n) => setHoveredNodeId(n.id)}
                  onMouseLeave={() => setHoveredNodeId(null)}
                />
              )
            }
            return (
              <TaskNode
                key={node.id}
                node={node}
                highlighted={hl}
                isDark={isDark}
                onClick={handleNodeClick}
                onMouseEnter={(n) => setHoveredNodeId(n.id)}
                onMouseLeave={() => setHoveredNodeId(null)}
              />
            )
          })}
        </g>
      </svg>

      {/* Tooltip overlay */}
      {tooltip && (
        <NodeTooltip data={tooltip} onClose={() => setTooltip(null)} />
      )}
    </div>
  )
}
