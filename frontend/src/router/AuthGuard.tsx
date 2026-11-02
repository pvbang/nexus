// ============================================================
// Nexus - Authentication guard
// Redirects to /login if not authenticated
// ============================================================

import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

export function useAuthGuard(): void {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    if (!isAuthenticated && location.pathname !== '/login') {
      navigate('/login', { replace: true })
    }
  }, [isAuthenticated, location.pathname, navigate])
}
