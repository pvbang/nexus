// ============================================================
// Nexus - Project Store (Zustand)
// Manages project builder state, build phases, preview, and
// handles SSE events from the backend pipeline.
// ============================================================

import { create } from 'zustand'

// ============================================================
// Types
// ============================================================

export interface ProjectBlueprint {
  name: string
  description: string
  techStack: {
    frontend: string
    backend: string
    database: string
    cssFramework: string
  }
  pages: Array<{ name: string; route: string; description: string }>
  components: Array<{ name: string; type: string; description: string }>
  implementationOrder: string[]
  successCriteria: string[]
}

export interface ProjectServer {
  serverId: string
  url: string
  port: number
  tech: string
  status: string
}

export interface ProjectFile {
  path: string
  status: 'pending' | 'writing' | 'done' | 'error'
  language: string
}

export interface BuildPhase {
  name: string
  status: 'pending' | 'active' | 'done' | 'error' | 'skipped'
  description: string
  icon: string
  startedAt?: string
  completedAt?: string
}

// ============================================================
// State + Actions interface
// ============================================================

export interface ProjectState {
  // Build state
  isBuilding: boolean
  currentPhase: string
  phases: BuildPhase[]

  // Data
  blueprint: ProjectBlueprint | null
  server: ProjectServer | null
  files: ProjectFile[]

  // Progress
  qualityScore: number
  iterationCount: number
  totalFiles: number
  filesWritten: number
  errorsFixed: number

  // Preview
  previewUrl: string
  showPreview: boolean
  previewViewport: 'desktop' | 'tablet' | 'mobile'

  // Error
  error: string | null

  // Actions
  startBuild: (request: string) => void
  setPhase: (phase: string) => void
  updatePhaseStatus: (phaseName: string, status: BuildPhase['status']) => void
  setBlueprint: (blueprint: ProjectBlueprint) => void
  setServer: (server: ProjectServer) => void
  addFile: (file: ProjectFile) => void
  updateFileStatus: (path: string, status: ProjectFile['status']) => void
  setQuality: (score: number) => void
  setIteration: (count: number) => void
  setPreviewUrl: (url: string) => void
  togglePreview: () => void
  setPreviewViewport: (viewport: 'desktop' | 'tablet' | 'mobile') => void
  setError: (error: string | null) => void
  reset: () => void

  // Handle SSE events from backend
  handleProjectEvent: (event: Record<string, unknown>) => void
}

// ============================================================
// Default phases for the project builder pipeline
// ============================================================

const DEFAULT_PHASES: BuildPhase[] = [
  { name: 'blueprint', status: 'pending', description: 'Thiết kế kiến trúc', icon: '📐' },
  { name: 'scaffold', status: 'pending', description: 'Tạo cấu trúc project', icon: '🏗️' },
  { name: 'implement', status: 'pending', description: 'Code các file', icon: '💻' },
  { name: 'test', status: 'pending', description: 'Build & Test', icon: '🧪' },
  { name: 'visual_qa', status: 'pending', description: 'Visual QA', icon: '👁️' },
  { name: 'iterate', status: 'pending', description: 'Tối ưu & Fix', icon: '🔄' },
  { name: 'deliver', status: 'pending', description: 'Hoàn thành', icon: '🚀' },
]

// ============================================================
// Initial state
// ============================================================

const initialState: Omit<ProjectState, 'startBuild' | 'setPhase' | 'updatePhaseStatus' | 'setBlueprint' | 'setServer' | 'addFile' | 'updateFileStatus' | 'setQuality' | 'setIteration' | 'setPreviewUrl' | 'togglePreview' | 'setPreviewViewport' | 'setError' | 'reset' | 'handleProjectEvent'> = {
  isBuilding: false,
  currentPhase: '',
  phases: DEFAULT_PHASES.map((p) => ({ ...p })),

  blueprint: null,
  server: null,
  files: [],

  qualityScore: 0,
  iterationCount: 0,
  totalFiles: 0,
  filesWritten: 0,
  errorsFixed: 0,

  previewUrl: '',
  showPreview: false,
  previewViewport: 'desktop',

  error: null,
}

// ============================================================
// Store
// ============================================================

export const useProjectStore = create<ProjectState>((set, get) => ({
  ...initialState,

  // -- Build lifecycle ------------------------------------------

  startBuild: (_request: string) => {
    set({
      isBuilding: true,
      currentPhase: 'blueprint',
      phases: DEFAULT_PHASES.map((p) => ({ ...p })),
      blueprint: null,
      server: null,
      files: [],
      qualityScore: 0,
      iterationCount: 0,
      totalFiles: 0,
      filesWritten: 0,
      errorsFixed: 0,
      previewUrl: '',
      showPreview: false,
      error: null,
    })
  },

  // -- Phase management -----------------------------------------

  setPhase: (phase: string) => {
    const { phases } = get()
    const updatedPhases = phases.map((p) => {
      if (p.name === phase) {
        return { ...p, status: 'active' as const, startedAt: new Date().toISOString() }
      }
      // Mark previous phases as done if they were active
      if (p.status === 'active' && p.name !== phase) {
        return { ...p, status: 'done' as const, completedAt: new Date().toISOString() }
      }
      return p
    })
    set({ currentPhase: phase, phases: updatedPhases })
  },

  updatePhaseStatus: (phaseName: string, status: BuildPhase['status']) => {
    const { phases } = get()
    const updatedPhases = phases.map((p) => {
      if (p.name !== phaseName) return p
      return {
        ...p,
        status,
        ...(status === 'active' ? { startedAt: new Date().toISOString() } : {}),
        ...(status === 'done' || status === 'error' ? { completedAt: new Date().toISOString() } : {}),
      }
    })
    set({ phases: updatedPhases })
  },

  // -- Data setters ---------------------------------------------

  setBlueprint: (blueprint: ProjectBlueprint) => {
    set({
      blueprint,
      totalFiles: blueprint.implementationOrder.length,
    })
  },

  setServer: (server: ProjectServer) => {
    set({
      server,
      previewUrl: server.url,
      showPreview: true,
    })
  },

  // -- File tracking --------------------------------------------

  addFile: (file: ProjectFile) => {
    const { files } = get()
    const exists = files.find((f) => f.path === file.path)
    if (exists) {
      // Update existing file
      const updated = files.map((f) =>
        f.path === file.path ? { ...f, ...file } : f,
      )
      set({ files: updated })
    } else {
      set({ files: [...files, file] })
    }
    // Update filesWritten count if done
    if (file.status === 'done') {
      const { files: currentFiles } = get()
      const writtenCount = currentFiles.filter((f) => f.status === 'done').length
      set({ filesWritten: writtenCount })
    }
  },

  updateFileStatus: (path: string, status: ProjectFile['status']) => {
    const { files } = get()
    const updated = files.map((f) =>
      f.path === path ? { ...f, status } : f,
    )
    set({ files: updated })
    // Recount filesWritten
    const writtenCount = updated.filter((f) => f.status === 'done').length
    set({ filesWritten: writtenCount })
  },

  // -- Progress -------------------------------------------------

  setQuality: (score: number) => {
    set({ qualityScore: Math.min(1, Math.max(0, score)) })
  },

  setIteration: (count: number) => {
    set({ iterationCount: count })
  },

  // -- Preview --------------------------------------------------

  setPreviewUrl: (url: string) => {
    set({ previewUrl: url, showPreview: !!url })
  },

  togglePreview: () => {
    set((state) => ({ showPreview: !state.showPreview }))
  },

  setPreviewViewport: (viewport: 'desktop' | 'tablet' | 'mobile') => {
    set({ previewViewport: viewport })
  },

  // -- Error ----------------------------------------------------

  setError: (error: string | null) => {
    set({ error, isBuilding: error ? false : get().isBuilding })
  },

  // -- Reset ----------------------------------------------------

  reset: () => {
    set({
      ...initialState,
      phases: DEFAULT_PHASES.map((p) => ({ ...p })),
    })
  },

  // -- SSE Event Handler ----------------------------------------

  handleProjectEvent: (event: Record<string, unknown>) => {
    const type = event.type as string | undefined
    const store = get()

    switch (type) {
      case 'project_phase': {
        const phase = event.phase as string
        const status = (event.status as BuildPhase['status']) || 'active'

        if (status === 'active') {
          store.setPhase(phase)
        } else {
          store.updatePhaseStatus(phase, status)
        }

        // Update currentPhase
        set({ currentPhase: phase })
        break
      }

      case 'project_blueprint': {
        const blueprint = event.blueprint as ProjectBlueprint
        if (blueprint) {
          store.setBlueprint(blueprint)
        }
        break
      }

      case 'project_server': {
        const server = event.server as ProjectServer
        if (server) {
          store.setServer(server)
        }
        break
      }

      case 'file_implemented': {
        const path = event.path as string
        const language = (event.language as string) || ''
        const status = (event.status as ProjectFile['status']) || 'done'
        store.addFile({ path, language, status })
        break
      }

      case 'iteration_complete': {
        const iteration = (event.iteration as number) || get().iterationCount + 1
        const quality = event.quality_score as number | undefined
        const errorsFixed = event.errors_fixed as number | undefined

        set({ iterationCount: iteration })

        if (typeof quality === 'number') {
          store.setQuality(quality)
        }
        if (typeof errorsFixed === 'number') {
          set({ errorsFixed: get().errorsFixed + errorsFixed })
        }
        break
      }

      case 'project_complete': {
        const quality = event.quality_score as number | undefined
        const server = event.server as ProjectServer | undefined

        if (typeof quality === 'number') {
          store.setQuality(quality)
        }
        if (server) {
          store.setServer(server)
        }

        // Mark all remaining active phases as done
        const { phases } = get()
        const finalPhases = phases.map((p) =>
          p.status === 'active'
            ? { ...p, status: 'done' as const, completedAt: new Date().toISOString() }
            : p,
        )
        set({
          isBuilding: false,
          phases: finalPhases,
          currentPhase: 'deliver',
        })
        break
      }

      case 'project_error': {
        const errorMsg = (event.error as string) || (event.message as string) || 'Unknown error'
        const phase = event.phase as string | undefined

        if (phase) {
          store.updatePhaseStatus(phase, 'error')
        }

        set({ error: errorMsg, isBuilding: false })
        break
      }

      default:
        // Unknown event type — ignore silently
        break
    }
  },
}))
