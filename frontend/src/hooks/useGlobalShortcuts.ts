// ============================================================
// Nexus - Global keyboard shortcuts hook
// Ctrl+N (new chat), Ctrl+K (search), Ctrl+/ (sidebar), Ctrl+. (right panel)
// ============================================================

import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useSessionStore } from '@/stores/sessionStore'
import { usePreferenceStore } from '@/stores/preferenceStore'

export function useGlobalShortcuts(): void {
  const navigate = useNavigate()
  const location = useLocation()
  const toggleRightPanel = usePreferenceStore((s) => s.toggleRightPanel)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ctrl+N → New chat
      if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault()
        const { createNewSession } = useSessionStore.getState()
        const id = createNewSession()
        navigate(`/chat/${id}`)
      }
      // Ctrl+K → Focus search
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        if (!location.pathname.startsWith('/chat')) {
          navigate('/chat')
        }
        // Dispatch custom event for sidebar to focus search
        window.dispatchEvent(new CustomEvent('nexus:focus-search'))
      }
      // Ctrl+/ → Toggle sidebar
      if ((e.ctrlKey || e.metaKey) && e.key === '/') {
        e.preventDefault()
        usePreferenceStore.getState().toggleSidebar()
      }
      // Ctrl+. → Toggle right panel
      if ((e.ctrlKey || e.metaKey) && e.key === '.') {
        e.preventDefault()
        toggleRightPanel()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate, location.pathname, toggleRightPanel])
}
