// ============================================================
// Nexus - Artifact Preview Store
// Lightweight cross-component communication for file preview
// When a file link is clicked in chat, this triggers the
// right panel Files tab to open and show that file.
// ============================================================

import { create } from 'zustand'

interface ArtifactPreviewState {
  /** File name/path requested to be previewed */
  requestedFile: string | null
  /** Timestamp to force re-trigger even for same file */
  requestTimestamp: number

  /** Request to preview a file (opens Files tab and scrolls to file) */
  requestPreview: (fileName: string) => void
  /** Clear the request after it's been handled */
  clearRequest: () => void
}

export const useArtifactPreviewStore = create<ArtifactPreviewState>((set) => ({
  requestedFile: null,
  requestTimestamp: 0,

  requestPreview: (fileName) =>
    set({ requestedFile: fileName, requestTimestamp: Date.now() }),

  clearRequest: () =>
    set({ requestedFile: null }),
}))
