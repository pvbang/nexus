// ============================================================
// Nexus - TaskKnowledgeGraph
// Interactive SVG knowledge graph showing entities extracted
// from task results with relationship lines and tooltips
// ============================================================

import { useState, useEffect, useMemo, useRef } from 'react'
import { cn } from '../../lib/cn'
import { apiGet } from '../../api'
import { EmptyState } from '../ui/EmptyState'
import { Spinner } from '../ui/Spinner'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KGNode {
  id: string
  name: string
  entity_type: string
  description: string
  properties: Record<string, unknown>
}

interface KGEdge {
  id: string
  source: string
  target: string
  relation_type: string
  weight: number
}

interface KGData {
  task_id: string
  nodes: KGNode[]
  edges: KGEdge[]
  stats: {
    node_count: number
    edge_count: number
    entity_types: string[]
  }
}

interface TaskKnowledgeGraphProps {
  taskId: string
  className?: string
}

// ---------------------------------------------------------------------------
// Entity type colors
// ---------------------------------------------------------------------------

const TYPE_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  // Core types
  Repository:         { bg: '#dbeafe', border: '#3b82f6', text: '#1e40af' },
  Technology:         { bg: '#d1fae5', border: '#10b981', text: '#065f46' },
  Framework:          { bg: '#d1fae5', border: '#10b981', text: '#065f46' },
  Language:           { bg: '#fef3c7', border: '#f59e0b', text: '#92400e' },
  ProgrammingLanguage: { bg: '#fef3c7', border: '#f59e0b', text: '#92400e' },
  Person:             { bg: '#fce7f3', border: '#ec4899', text: '#9d174d' },
  Organization:       { bg: '#ede9fe', border: '#8b5cf6', text: '#5b21b6' },
  Company:            { bg: '#ede9fe', border: '#8b5cf6', text: '#5b21b6' },
  Topic:              { bg: '#e0f2fe', border: '#0ea5e9', text: '#0c4a6e' },
  Product:            { bg: '#fef2f2', border: '#ef4444', text: '#991b1b' },
  Service:            { bg: '#fef2f2', border: '#ef4444', text: '#991b1b' },
  API:                { bg: '#fff1f2', border: '#f43f5e', text: '#9f1239' },
  Platform:           { bg: '#f0f9ff', border: '#38bdf8', text: '#075985' },
  Metric:             { bg: '#f0fdf4', border: '#22c55e', text: '#14532d' },
  Statistic:          { bg: '#f0fdf4', border: '#22c55e', text: '#14532d' },
  Location:           { bg: '#fff7ed', border: '#f97316', text: '#9a3412' },
  Country:            { bg: '#fff7ed', border: '#f97316', text: '#9a3412' },
  City:               { bg: '#fff7ed', border: '#f97316', text: '#9a3412' },
  Event:              { bg: '#fdf4ff', border: '#d946ef', text: '#86198f' },
  Date:               { bg: '#fdf4ff', border: '#d946ef', text: '#86198f' },
  Concept:            { bg: '#f1f5f9', border: '#64748b', text: '#334155' },
  Theory:             { bg: '#f1f5f9', border: '#64748b', text: '#334155' },
  Method:             { bg: '#ecfdf5', border: '#34d399', text: '#064e3b' },
  Algorithm:          { bg: '#ecfdf5', border: '#34d399', text: '#064e3b' },
  Tool:               { bg: '#fef9c3', border: '#eab308', text: '#713f12' },
  Protocol:           { bg: '#e0e7ff', border: '#6366f1', text: '#3730a3' },
  Currency:           { bg: '#fefce8', border: '#facc15', text: '#854d0e' },
  Market:             { bg: '#fefce8', border: '#facc15', text: '#854d0e' },
  Industry:           { bg: '#f5f3ff', border: '#a78bfa', text: '#4c1d95' },
  Region:             { bg: '#fff7ed', border: '#fb923c', text: '#9a3412' },
}

function getTypeColor(type: string) {
  return TYPE_COLORS[type] || TYPE_COLORS.Concept
}

// ---------------------------------------------------------------------------
// Layout: force-directed simple positioning
// ---------------------------------------------------------------------------

function calculatePositions(nodes: KGNode[], _edges: KGEdge[], width: number, height: number) {
  const positions: Record<string, { x: number; y: number }> = {}
  const cx = width / 2
  const cy = height / 2

  if (nodes.length === 0) return positions

  // Group by entity_type, place in concentric rings
  const groups: Record<string, KGNode[]> = {}
  for (const n of nodes) {
    const t = n.entity_type || 'Concept'
    if (!groups[t]) groups[t] = []
    groups[t].push(n)
  }

  const typeKeys = Object.keys(groups)
  let globalIdx = 0

  for (let gi = 0; gi < typeKeys.length; gi++) {
    const group = groups[typeKeys[gi]]
    const ringRadius = 80 + gi * 90
    for (let ni = 0; ni < group.length; ni++) {
      const angle = (2 * Math.PI * ni) / group.length - Math.PI / 2
      const jitter = (globalIdx % 3 - 1) * 15
      positions[group[ni].id] = {
        x: cx + (ringRadius + jitter) * Math.cos(angle),
        y: cy + (ringRadius + jitter) * Math.sin(angle),
      }
      globalIdx++
    }
  }

  return positions
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TaskKnowledgeGraph({ taskId, className }: TaskKnowledgeGraphProps) {
  const [data, setData] = useState<KGData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  // Fetch knowledge graph for task — with polling for live updates
  useEffect(() => {
    let cancelled = false
    let retryCount = 0
    let pollTimer: ReturnType<typeof setTimeout> | null = null

    const fetchKG = () => {
      apiGet<KGData>(`/tasks/${taskId}/knowledge`)
        .then((result) => {
          if (!cancelled) {
            setData(result)
            setLoading(false)
            // Poll every 5s for incremental KG updates
            pollTimer = setTimeout(fetchKG, 5000)
          }
        })
        .catch((err) => {
          if (cancelled) return
          // Retry up to 3 times (entities may still be extracting)
          if (retryCount < 3) {
            retryCount++
            setTimeout(fetchKG, 3000 * retryCount)
          } else {
            setError(err instanceof Error ? err.message : 'Failed to load knowledge graph')
            setLoading(false)
          }
        })
    }
    fetchKG()

    return () => { cancelled = true; if (pollTimer) clearTimeout(pollTimer) }
  }, [taskId])

  // Calculate layout
  const width = containerRef.current?.clientWidth || 700
  const height = containerRef.current?.clientHeight || 500
  const positions = useMemo(
    () => data ? calculatePositions(data.nodes, data.edges, width, height) : {},
    [data, width, height],
  )

  // Connected nodes for highlighting
  const connectedIds = useMemo(() => {
    if (!hoveredNode || !data) return null
    const ids = new Set<string>([hoveredNode])
    for (const edge of data.edges) {
      if (edge.source === hoveredNode) ids.add(edge.target)
      if (edge.target === hoveredNode) ids.add(edge.source)
    }
    return ids
  }, [hoveredNode, data])

  // Zoom handlers
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const newZoom = Math.max(0.3, Math.min(3, zoom - e.deltaY * 0.001))
    setZoom(newZoom)
  }

  if (loading) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <Spinner size="md" className="text-brand-500" />
      </div>
    )
  }

  if (error) {
    return (
      <EmptyState
        icon="⚠️"
        title="Failed to load knowledge graph"
        description={error}
        className={className}
      />
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <EmptyState
        icon="🧬"
        title="No knowledge entities yet"
        description="Knowledge entities will be extracted when a task completes. Try sending a question!"
        className={className}
      />
    )
  }

  return (
    <div ref={containerRef} className={cn('relative h-full overflow-hidden', className)}>
      {/* Stats bar */}
      <div className="absolute top-2 left-2 z-10 flex items-center gap-2 flex-wrap">
        <span className="text-[10px] bg-white/90 dark:bg-[#1a1a2e]/90 px-2 py-1 rounded-md border border-gray-200 dark:border-[#2a2a45] text-gray-600 dark:text-gray-400">
          {data.stats.node_count} entities · {data.stats.edge_count} relations
        </span>
        {data.stats.entity_types.map((type) => {
          const c = getTypeColor(type)
          return (
            <span
              key={type}
              className="text-[9px] px-1.5 py-0.5 rounded-full font-medium"
              style={{ backgroundColor: c.bg, color: c.text, border: `1px solid ${c.border}` }}
            >
              {type}
            </span>
          )
        })}
      </div>

      {/* Zoom controls */}
      <div className="absolute top-2 right-2 z-10 flex flex-col gap-1">
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(3, z + 0.2))}
          className="w-7 h-7 bg-white/90 dark:bg-[#1a1a2e]/90 border border-gray-200 dark:border-[#2a2a45] rounded text-xs hover:bg-gray-50 dark:hover:bg-[#22223a]"
        >
          +
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.3, z - 0.2))}
          className="w-7 h-7 bg-white/90 dark:bg-[#1a1a2e]/90 border border-gray-200 dark:border-[#2a2a45] rounded text-xs hover:bg-gray-50 dark:hover:bg-[#22223a]"
        >
          −
        </button>
        <button
          type="button"
          onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}
          className="w-7 h-7 bg-white/90 dark:bg-[#1a1a2e]/90 border border-gray-200 dark:border-[#2a2a45] rounded text-[9px] hover:bg-gray-50 dark:hover:bg-[#22223a]"
        >
          ↻
        </button>
      </div>

      {/* SVG Graph */}
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${width} ${height}`}
        onWheel={handleWheel}
        className="cursor-grab"
      >
        <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
          {/* Edges */}
          {data.edges.map((edge) => {
            const from = positions[edge.source]
            const to = positions[edge.target]
            if (!from || !to) return null
            const isHighlighted = connectedIds?.has(edge.source) && connectedIds?.has(edge.target)
            const opacity = connectedIds ? (isHighlighted ? 0.8 : 0.15) : 0.4

            return (
              <g key={edge.id}>
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={isHighlighted ? '#6366f1' : '#94a3b8'}
                  strokeWidth={isHighlighted ? 2 : 1}
                  opacity={opacity}
                />
                {/* Relation label */}
                {isHighlighted && (
                  <text
                    x={(from.x + to.x) / 2}
                    y={(from.y + to.y) / 2 - 5}
                    textAnchor="middle"
                    fontSize={8}
                    fill="#6366f1"
                    opacity={0.9}
                  >
                    {edge.relation_type.replace(/_/g, ' ')}
                  </text>
                )}
              </g>
            )
          })}

          {/* Nodes */}
          {data.nodes.map((node) => {
            const pos = positions[node.id]
            if (!pos) return null
            const colors = getTypeColor(node.entity_type)
            const isHovered = hoveredNode === node.id
            const isConnected = connectedIds?.has(node.id) ?? true
            const opacity = connectedIds ? (isConnected ? 1 : 0.25) : 1
            const r = isHovered ? 28 : 22

            return (
              <g
                key={node.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                opacity={opacity}
                onMouseEnter={() => setHoveredNode(node.id)}
                onMouseLeave={() => setHoveredNode(null)}
                className="cursor-pointer"
              >
                {/* Node circle */}
                <circle
                  r={r}
                  fill={colors.bg}
                  stroke={isHovered ? '#6366f1' : colors.border}
                  strokeWidth={isHovered ? 2.5 : 1.5}
                />

                {/* Node label */}
                <text
                  textAnchor="middle"
                  dy="-1"
                  fontSize={node.name.length > 15 ? 7 : 8}
                  fontWeight="600"
                  fill={colors.text}
                >
                  {node.name.length > 20 ? node.name.slice(0, 18) + '…' : node.name}
                </text>

                {/* Type label */}
                <text
                  textAnchor="middle"
                  dy="9"
                  fontSize={6}
                  fill={colors.text}
                  opacity={0.7}
                >
                  {node.entity_type}
                </text>

                {/* Tooltip on hover */}
                {isHovered && node.description && (
                  <foreignObject x={-80} y={r + 5} width={160} height={60}>
                    <div className="bg-gray-900 text-white text-[9px] p-1.5 rounded shadow-lg leading-tight text-center">
                      {node.description.slice(0, 100)}
                    </div>
                  </foreignObject>
                )}
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}
