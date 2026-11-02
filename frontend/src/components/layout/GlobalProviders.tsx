// ============================================================
// Nexus - Global floating components
// ToastContainer, SessionQuickSearch, GlobalProcessBar
// ============================================================

import { ToastContainer } from '@/components/ui/Toast'
import { SessionQuickSearch } from '@/components/chat/SessionQuickSearch'
import { GlobalProcessBar } from './GlobalProcessBar'

export function GlobalProviders() {
  return (
    <>
      {/* Global Process Bar — shows active task progress at bottom across all views */}
      <GlobalProcessBar />

      {/* Session Quick Search Overlay (Ctrl+K) — self-manages open/close via nexus:focus-search event */}
      <SessionQuickSearch />

      {/* Toast Notifications */}
      <ToastContainer />
    </>
  )
}
