// ============================================================
// MekongAI Nexus - ProjectPreview
// iframe sandbox for live preview with responsive toolbar
// ============================================================

import { useState, useRef, useCallback } from 'react'
import { useProjectStore } from '../../stores/projectStore'

// ============================================================
// Viewport presets
// ============================================================

const VIEWPORTS = {
  desktop: { width: '100%', height: '100%', label: '🖥️ Desktop' },
  tablet: { width: '768px', height: '1024px', label: '📱 Tablet' },
  mobile: { width: '375px', height: '812px', label: '📱 Mobile' },
} as const

type ViewportKey = keyof typeof VIEWPORTS

// ============================================================
// Component
// ============================================================

export function ProjectPreview() {
  const {
    previewUrl,
    showPreview,
    previewViewport,
    setPreviewViewport,
    togglePreview,
  } = useProjectStore()

  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [isLoading, setIsLoading] = useState(true)

  const handleRefresh = useCallback(() => {
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.location.reload()
      setIsLoading(true)
    }
  }, [])

  const handleLoad = useCallback(() => {
    setIsLoading(false)
  }, [])

  // Don't render if preview is hidden or no URL
  if (!showPreview || !previewUrl) return null

  const viewport = VIEWPORTS[previewViewport]

  return (
    <div className="flex flex-col h-full border-l border-zinc-700 bg-zinc-900">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700 bg-zinc-800">
        {/* Left: Label + URL bar */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-zinc-300 shrink-0">
            🌐 Preview
          </span>
          <div
            className="flex items-center bg-zinc-700 rounded px-2 py-1 text-xs text-zinc-400 min-w-[160px] max-w-[300px]"
            title={previewUrl}
          >
            <span className="truncate">{previewUrl}</span>
          </div>
        </div>

        {/* Right: Viewport switcher + actions */}
        <div className="flex items-center gap-1 shrink-0">
          {/* Viewport switcher */}
          {(Object.entries(VIEWPORTS) as [ViewportKey, typeof VIEWPORTS[ViewportKey]][]).map(
            ([key, { label }]) => (
              <button
                key={key}
                type="button"
                onClick={() => setPreviewViewport(key)}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  previewViewport === key
                    ? 'bg-blue-600 text-white'
                    : 'text-zinc-400 hover:text-white hover:bg-zinc-700'
                }`}
                aria-label={`Switch to ${key} viewport`}
                aria-pressed={previewViewport === key}
              >
                {label}
              </button>
            ),
          )}

          {/* Refresh */}
          <button
            type="button"
            onClick={handleRefresh}
            className="px-2 py-1 text-xs text-zinc-400 hover:text-white hover:bg-zinc-700 rounded transition-colors"
            aria-label="Refresh preview"
            title="Refresh"
          >
            🔄
          </button>

          {/* Open in new tab */}
          <a
            href={previewUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="px-2 py-1 text-xs text-zinc-400 hover:text-white hover:bg-zinc-700 rounded transition-colors"
            aria-label="Open in new tab"
            title="Open in new tab"
          >
            ↗
          </a>

          {/* Close */}
          <button
            type="button"
            onClick={togglePreview}
            className="px-2 py-1 text-xs text-zinc-400 hover:text-red-400 hover:bg-zinc-700 rounded transition-colors"
            aria-label="Close preview"
            title="Close"
          >
            ✕
          </button>
        </div>
      </div>

      {/* iframe container */}
      <div className="flex-1 flex items-center justify-center bg-zinc-950 overflow-hidden p-4">
        <div
          style={{
            width: viewport.width,
            height: viewport.height,
            maxWidth: '100%',
            maxHeight: '100%',
          }}
          className="relative bg-white rounded-lg shadow-2xl overflow-hidden transition-all duration-300"
        >
          {/* Loading overlay */}
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-zinc-800/50 z-10">
              <div className="flex flex-col items-center gap-2">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500" />
                <span className="text-xs text-zinc-400">Loading preview...</span>
              </div>
            </div>
          )}

          {/* Preview iframe */}
          <iframe
            ref={iframeRef}
            src={previewUrl}
            className="w-full h-full border-0"
            onLoad={handleLoad}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            title="Project Preview"
          />
        </div>
      </div>

      {/* Bottom status bar */}
      <div className="flex items-center justify-between px-3 py-1 border-t border-zinc-700 bg-zinc-800/50 text-xs text-zinc-500">
        <span>
          {previewViewport === 'desktop'
            ? 'Responsive'
            : `${viewport.width} × ${viewport.height}`}
        </span>
        <span className={isLoading ? 'text-yellow-400' : 'text-green-400'}>
          {isLoading ? '⏳ Loading...' : '● Connected'}
        </span>
      </div>
    </div>
  )
}
