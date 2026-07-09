import { useState, useEffect, useRef, useCallback } from 'react'
import { RefreshCw, Maximize2, Minimize2, Filter, ZoomIn, ZoomOut, MousePointer } from 'lucide-react'
import { apiGet } from '../api'

// ── Types ──────────────────────────────────────────────────────
interface GraphNodeRaw {
  id: string
  name: string
  entity_type: string
  description: string
  properties: Record<string, unknown>
}

interface GraphNode extends GraphNodeRaw {
  // Simulation fields
  x: number
  y: number
  vx: number
  vy: number
  pinned: boolean
}

interface GraphEdge {
  id: string
  source: string
  target: string
  relation_type: string
  weight: number
}

interface GraphData {
  nodes: GraphNodeRaw[]
  edges: GraphEdge[]
  stats: {
    node_count: number
    edge_count: number
    entity_types: string[]
    type_counts: Record<string, number>
  }
}

interface NodeRelation {
  direction: 'outgoing' | 'incoming'
  relation: string
  source?: { name: string; id: string }
  target?: { name: string; id: string }
}

interface NodeDetail {
  relations: NodeRelation[]
}

// ── Color Palette per Entity Type ──────────────────────────────
const TYPE_COLORS: Record<string, string> = {
  Person: '#3b82f6',
  Organization: '#ef4444',
  Location: '#22c55e',
  Event: '#f59e0b',
  Concept: '#8b5cf6',
  Technology: '#06b6d4',
  Product: '#ec4899',
  Document: '#64748b',
  Agent: '#14b8a6',
  Skill: '#f97316',
  Tool: '#6366f1',
  Task: '#84cc16',
  University: '#e11d48',
  Student: '#d946ef',
  Professor: '#0891b2',
  Entity: '#475569',
}
const DEFAULT_COLOR = '#94a3b8'

function getColor(type: string): string {
  return TYPE_COLORS[type] || DEFAULT_COLOR
}

// ── Force-Directed Layout Engine ───────────────────────────────
function applyForces(
  nodes: GraphNode[],
  edges: GraphEdge[],
  width: number,
  height: number,
) {
  const REPULSION = 8000
  const ATTRACTION = 0.005
  const DAMPING = 0.85
  const CENTER_GRAVITY = 0.01

  // Repulsion between nodes
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j]
      const dx = a.x - b.x
      const dy = a.y - b.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const force = REPULSION / (dist * dist)
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      if (!a.pinned) { a.vx += fx; a.vy += fy }
      if (!b.pinned) { b.vx -= fx; b.vy -= fy }
    }
  }

  // Attraction along edges
  const nodeMap = new Map(nodes.map(n => [n.id, n]))
  for (const edge of edges) {
    const s = nodeMap.get(edge.source)
    const t = nodeMap.get(edge.target)
    if (!s || !t) continue
    const dx = t.x - s.x
    const dy = t.y - s.y
    const dist = Math.sqrt(dx * dx + dy * dy) || 1
    const force = dist * ATTRACTION * edge.weight
    const fx = (dx / dist) * force
    const fy = (dy / dist) * force
    if (!s.pinned) { s.vx += fx; s.vy += fy }
    if (!t.pinned) { t.vx -= fx; t.vy -= fy }
  }

  // Center gravity + apply velocity
  const cx = width / 2, cy = height / 2
  for (const n of nodes) {
    if (n.pinned) continue
    n.vx += (cx - n.x) * CENTER_GRAVITY
    n.vy += (cy - n.y) * CENTER_GRAVITY
    n.vx *= DAMPING
    n.vy *= DAMPING
    n.x += n.vx
    n.y += n.vy
    // Bounds
    n.x = Math.max(30, Math.min(width - 30, n.x))
    n.y = Math.max(30, Math.min(height - 30, n.y))
  }
}

// ── Component ──────────────────────────────────────────────────
export function KnowledgeGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const animRef = useRef<number>(0)
  const nodesRef = useRef<GraphNode[]>([])
  const edgesRef = useRef<GraphEdge[]>([])

  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [nodeRelations, setNodeRelations] = useState<NodeRelation[]>([])
  const [showEdgeLabels, setShowEdgeLabels] = useState(true)
  const [filterType, setFilterType] = useState('')
  const [fullscreen, setFullscreen] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState<GraphNode | null>(null)
  const [panning, setPanning] = useState(false)
  const panStart = useRef({ x: 0, y: 0 })
  const [canvasSize, setCanvasSize] = useState({ w: 900, h: 600 })

  // ── Fetch Data ──
  const fetchGraph = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const url = filterType
        ? `/system/graph?limit=500&entity_type=${encodeURIComponent(filterType)}`
        : '/system/graph?limit=500'
      const result = await apiGet<GraphData>(url)
      setData(result)

      // Initialize positions
      const w = canvasSize.w, h = canvasSize.h
      const nodes: GraphNode[] = (result.nodes || []).map((n: GraphNodeRaw, i: number) => ({
        ...n,
        x: w / 2 + (Math.cos(i * 2.39996) * Math.min(w, h) * 0.35),
        y: h / 2 + (Math.sin(i * 2.39996) * Math.min(w, h) * 0.35),
        vx: 0, vy: 0, pinned: false,
      }))
      nodesRef.current = nodes
      edgesRef.current = result.edges || []
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : 'Failed to load graph'
      setError(message)
      // Clear any stale data — do NOT generate demo/mock data
      nodesRef.current = []
      edgesRef.current = []
    } finally {
      setLoading(false)
    }
  }, [filterType, canvasSize])

  useEffect(() => { fetchGraph() }, [fetchGraph])

  // ── Resize Observer ──
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          setCanvasSize({ w: Math.floor(width), h: Math.floor(height) })
        }
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── Animation Loop ──
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const render = () => {
      const nodes = nodesRef.current
      const edges = edgesRef.current
      const w = canvasSize.w, h = canvasSize.h
      canvas.width = w * window.devicePixelRatio
      canvas.height = h * window.devicePixelRatio
      canvas.style.width = w + 'px'
      canvas.style.height = h + 'px'
      ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0)

      // Apply physics
      if (nodes.length > 0) {
        applyForces(nodes, edges, w, h)
      }

      // Clear
      ctx.clearRect(0, 0, w, h)
      ctx.save()
      ctx.translate(panOffset.x, panOffset.y)
      ctx.scale(zoom, zoom)

      // Draw edges
      const nodeMap = new Map(nodes.map(n => [n.id, n]))
      ctx.lineWidth = 0.8
      for (const edge of edges) {
        const s = nodeMap.get(edge.source)
        const t = nodeMap.get(edge.target)
        if (!s || !t) continue

        ctx.strokeStyle = 'rgba(148, 163, 184, 0.4)'
        ctx.beginPath()
        ctx.moveTo(s.x, s.y)
        ctx.lineTo(t.x, t.y)
        ctx.stroke()

        // Arrow head
        const angle = Math.atan2(t.y - s.y, t.x - s.x)
        const arrowLen = 6
        const mx = t.x - Math.cos(angle) * 12
        const my = t.y - Math.sin(angle) * 12
        ctx.fillStyle = 'rgba(148, 163, 184, 0.5)'
        ctx.beginPath()
        ctx.moveTo(mx, my)
        ctx.lineTo(mx - arrowLen * Math.cos(angle - 0.4), my - arrowLen * Math.sin(angle - 0.4))
        ctx.lineTo(mx - arrowLen * Math.cos(angle + 0.4), my - arrowLen * Math.sin(angle + 0.4))
        ctx.closePath()
        ctx.fill()

        // Edge label
        if (showEdgeLabels && zoom > 0.6) {
          const lx = (s.x + t.x) / 2
          const ly = (s.y + t.y) / 2
          ctx.fillStyle = 'rgba(100, 116, 139, 0.7)'
          ctx.font = `${Math.max(8, 10 / zoom)}px Inter, system-ui, sans-serif`
          ctx.textAlign = 'center'
          ctx.fillText(edge.relation_type, lx, ly - 3)
        }
      }

      // Draw nodes
      for (const node of nodes) {
        const color = getColor(node.entity_type)
        const isSelected = selectedNode?.id === node.id
        const radius = isSelected ? 10 : 7

        // Glow for selected
        if (isSelected) {
          ctx.shadowColor = color
          ctx.shadowBlur = 15
        }

        // Node circle
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2)
        ctx.fill()

        // Border
        ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255,255,255,0.6)'
        ctx.lineWidth = isSelected ? 2.5 : 1
        ctx.stroke()

        ctx.shadowColor = 'transparent'
        ctx.shadowBlur = 0

        // Label
        if (zoom > 0.5 || isSelected) {
          ctx.fillStyle = '#1e293b'
          ctx.font = `${isSelected ? 'bold ' : ''}${Math.max(9, 11 / zoom)}px Inter, system-ui, sans-serif`
          ctx.textAlign = 'center'
          const label = node.name.length > 20 ? node.name.slice(0, 18) + '…' : node.name
          ctx.fillText(label, node.x, node.y + radius + 13)
        }
      }

      ctx.restore()
      animRef.current = requestAnimationFrame(render)
    }

    animRef.current = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animRef.current)
  }, [canvasSize, zoom, panOffset, showEdgeLabels, selectedNode])

  // ── Mouse Handlers ──
  const getNodeAt = (cx: number, cy: number): GraphNode | null => {
    const mx = (cx - panOffset.x) / zoom
    const my = (cy - panOffset.y) / zoom
    for (const n of nodesRef.current) {
      const dx = n.x - mx, dy = n.y - my
      if (dx * dx + dy * dy < 144) return n // radius ~12
    }
    return null
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    const node = getNodeAt(cx, cy)

    if (node) {
      setDragging(node)
      node.pinned = true
    } else {
      setPanning(true)
      panStart.current = { x: e.clientX - panOffset.x, y: e.clientY - panOffset.y }
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (dragging) {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return
      dragging.x = (e.clientX - rect.left - panOffset.x) / zoom
      dragging.y = (e.clientY - rect.top - panOffset.y) / zoom
    } else if (panning) {
      setPanOffset({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y })
    }
  }

  const handleMouseUp = () => {
    if (dragging) {
      dragging.pinned = false
      setDragging(null)
    }
    setPanning(false)
  }

  const handleClick = async (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    const node = getNodeAt(cx, cy)
    setSelectedNode(node)

    if (node) {
      try {
        const detail = await apiGet<NodeDetail>(`/system/graph/node/${node.id}`)
        setNodeRelations(detail.relations || [])
      } catch { setNodeRelations([]) }
    } else {
      setNodeRelations([])
    }
  }

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.1 : 0.1
    setZoom(z => Math.max(0.2, Math.min(3, z + delta)))
  }

  // ── Render ──
  return (
    <div className={`flex flex-col h-full ${fullscreen ? 'fixed inset-0 z-50 bg-white' : ''}`}>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-light bg-surface-secondary">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-primary">Knowledge Graph</h2>
          {data && (
            <span className="text-xs text-text-tertiary">
              {data.stats.node_count} nodes · {data.stats.edge_count} edges
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {/* Entity Type Filter */}
          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            className="text-xs border border-border-light rounded-md px-2 py-1 bg-white text-text-secondary focus:outline-none focus:ring-1 focus:ring-blue-400"
          >
            <option value="">All Types</option>
            {(data?.stats.entity_types || []).map(t => (
              <option key={t} value={t}>{t} ({data?.stats.type_counts[t] || 0})</option>
            ))}
          </select>

          {/* Toggle Edge Labels */}
          <button
            onClick={() => setShowEdgeLabels(!showEdgeLabels)}
            className={`p-1.5 rounded-md text-xs ${showEdgeLabels ? 'bg-blue-50 text-blue-600' : 'text-text-tertiary hover:bg-surface-hover'}`}
            title="Toggle edge labels"
          >
            <Filter className="w-3.5 h-3.5" />
          </button>

          {/* Zoom */}
          <button onClick={() => setZoom(z => Math.min(3, z + 0.2))} className="p-1.5 rounded-md text-text-tertiary hover:bg-surface-hover" title="Zoom in">
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <span className="text-xs text-text-tertiary w-8 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => Math.max(0.2, z - 0.2))} className="p-1.5 rounded-md text-text-tertiary hover:bg-surface-hover" title="Zoom out">
            <ZoomOut className="w-3.5 h-3.5" />
          </button>

          {/* Reset */}
          <button onClick={() => { setZoom(1); setPanOffset({ x: 0, y: 0 }) }} className="p-1.5 rounded-md text-text-tertiary hover:bg-surface-hover" title="Reset view">
            <MousePointer className="w-3.5 h-3.5" />
          </button>

          {/* Refresh */}
          <button onClick={fetchGraph} disabled={loading} className="p-1.5 rounded-md text-text-tertiary hover:bg-surface-hover" title="Refresh">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>

          {/* Fullscreen */}
          <button onClick={() => setFullscreen(!fullscreen)} className="p-1.5 rounded-md text-text-tertiary hover:bg-surface-hover" title="Fullscreen">
            {fullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Main Area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Canvas */}
        <div ref={containerRef} className="flex-1 relative bg-[#fafbfc]">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/60 z-10">
              <div className="flex items-center gap-2 text-sm text-text-secondary">
                <RefreshCw className="w-4 h-4 animate-spin" /> Loading graph…
              </div>
            </div>
          )}
          {error && !data && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 z-10">
              <div className="text-3xl" role="img" aria-hidden="true">🧬</div>
              <div className="text-sm text-red-500">{error}</div>
              <button
                onClick={fetchGraph}
                className="px-3 py-1.5 text-xs font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-md transition-colors"
              >
                Retry
              </button>
            </div>
          )}
          {/* UI-05: Empty state for fresh installs / no data */}
          {!loading && !error && (!data || data.stats.node_count === 0) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 z-10">
              <div className="w-16 h-16 rounded-full bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 flex items-center justify-center">
                <span className="text-3xl" role="img" aria-label="Knowledge graph">🧬</span>
              </div>
              <div className="text-center max-w-[280px]">
                <p className="text-sm font-medium text-text-secondary dark:text-gray-300">
                  No knowledge yet
                </p>
                <p className="text-xs text-text-tertiary dark:text-gray-500 mt-1 leading-relaxed">
                  Start chatting to build the graph! Entities and relationships will appear here as agents process information.
                </p>
              </div>
              <button
                onClick={fetchGraph}
                className="mt-1 px-3 py-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-colors"
              >
                <RefreshCw className="w-3 h-3 inline mr-1" />
                Refresh
              </button>
            </div>
          )}
          <canvas
            ref={canvasRef}
            className="cursor-grab active:cursor-grabbing"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onClick={handleClick}
            onWheel={handleWheel}
          />

          {/* Legend */}
          <div className="absolute bottom-3 left-3 bg-white/90 backdrop-blur-sm border border-border-light rounded-lg p-2.5 shadow-sm max-w-[200px]">
            <div className="text-[10px] font-semibold text-text-secondary mb-1.5 uppercase tracking-wider">Entity Types</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {(data?.stats.entity_types || []).map(type => (
                <button
                  key={type}
                  onClick={() => setFilterType(filterType === type ? '' : type)}
                  className={`flex items-center gap-1 text-[10px] ${filterType === type ? 'font-bold' : 'text-text-tertiary'}`}
                >
                  <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: getColor(type) }} />
                  {type}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Side Panel — Node Detail */}
        {selectedNode && (
          <div className="w-72 border-l border-border-light bg-white overflow-y-auto">
            <div className="p-4">
              {/* Header */}
              <div className="flex items-start gap-2 mb-3">
                <span className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-white" style={{ backgroundColor: getColor(selectedNode.entity_type) }}>
                  ENTITY
                </span>
                <div>
                  <h3 className="text-sm font-semibold text-text-primary">{selectedNode.name}</h3>
                  <span className="text-[11px] text-text-tertiary">{selectedNode.entity_type}</span>
                </div>
              </div>

              {/* Description */}
              {selectedNode.description && (
                <p className="text-xs text-text-secondary mb-3 leading-relaxed">{selectedNode.description}</p>
              )}

              {/* Properties */}
              {selectedNode.properties && Object.keys(selectedNode.properties).length > 0 && (
                <div className="mb-3">
                  <div className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1.5">Attributes</div>
                  {Object.entries(selectedNode.properties).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-xs py-0.5">
                      <span className="text-text-tertiary font-mono">{k}</span>
                      <span className="text-text-secondary">{String(v)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Relations */}
              <div className="mt-3 pt-3 border-t border-border-light">
                <div className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1.5">
                  Relations ({nodeRelations.length})
                </div>
                {nodeRelations.length === 0 ? (
                  <p className="text-xs text-text-tertiary">No relations found</p>
                ) : (
                  <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
                    {nodeRelations.map((rel: NodeRelation, i: number) => (
                      <div key={i} className="text-xs bg-surface-secondary rounded-md p-2">
                        <div className="flex items-center gap-1">
                          <span className={`text-[10px] px-1 rounded ${rel.direction === 'outgoing' ? 'bg-blue-50 text-blue-600' : 'bg-green-50 text-green-600'}`}>
                            {rel.direction === 'outgoing' ? '→' : '←'}
                          </span>
                          <span className="font-medium text-text-primary">
                            {rel.relation}
                          </span>
                        </div>
                        <div className="mt-0.5 text-text-tertiary">
                          {rel.direction === 'outgoing'
                            ? `→ ${rel.target?.name || 'unknown'}`
                            : `← ${rel.source?.name || 'unknown'}`}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <button
                onClick={() => { setSelectedNode(null); setNodeRelations([]) }}
                className="mt-3 w-full text-xs text-text-tertiary hover:text-text-secondary py-1"
              >
                Close panel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
