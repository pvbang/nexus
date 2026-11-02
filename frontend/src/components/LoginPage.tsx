// ============================================================
// Nexus - Login Page
// Simple login form that authenticates via /api/v1/auth/login
// ============================================================

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Zap, LogIn, Eye, EyeOff, AlertCircle } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'

export function LoginPage() {
  const navigate = useNavigate()
  const setTokens = useAuthStore((s) => s.setTokens)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null)

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const res = await fetch('/api/v1/auth/status')
        if (res.ok) {
          const data = await res.json()
          setAuthEnabled(data.auth_enabled)
          // If auth is disabled, auto-generate token and redirect
          if (!data.auth_enabled) {
            await handleDevLogin()
          }
        }
      } catch {
        // Server might be down
        setError('Cannot reach server. Please check the connection.')
      }
    }
    // If already logged in, redirect
    if (isAuthenticated) {
      navigate('/chat', { replace: true })
      return
    }
    checkAuth()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleDevLogin() {
    // Dev mode: generate token without password
    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'dev_user' }),
      })
      if (res.ok) {
        const data = await res.json()
        setTokens(data.access_token, data.refresh_token ?? null, data.user_id)
        navigate('/chat', { replace: true })
      }
    } catch {
      // Silently fail — user can still use the app without auth in dev mode
      navigate('/chat', { replace: true })
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: username.trim(),
          password: password || undefined,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Login failed' }))
        setError(err.detail || `Error ${res.status}`)
        return
      }

      const data = await res.json()
      setTokens(data.access_token, data.refresh_token ?? null, data.user_id)
      navigate('/chat', { replace: true })
    } catch (err) {
      setError('Network error. Please check your connection.')
    } finally {
      setLoading(false)
    }
  }

  // Show loading while checking auth status
  if (authEnabled === null) {
    return (
      <div className="flex items-center justify-center h-full bg-white dark:bg-[#0f0f17]">
        <div className="w-8 h-8 border-3 border-[#3b82f6] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex items-center justify-center h-full bg-white dark:bg-[#0f0f17] px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#1456f0] via-[#3b82f6] to-[#60a5fa] flex items-center justify-center shadow-lg mb-4">
            <Zap className="w-7 h-7 text-white" strokeWidth={2.5} />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Nexus
          </h1>
          <p className="text-sm text-gray-500 dark:text-[#a0a0b8] mt-1">
            Sign in to your account
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Username */}
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter your username"
              required
              autoFocus
              className="w-full px-4 py-2.5 rounded-xl border border-gray-200 dark:border-[#2a2a45] bg-white dark:bg-[#1a1a2e] text-gray-900 dark:text-white placeholder:text-gray-400 dark:placeholder:text-[#6b6b85] focus:outline-none focus:ring-2 focus:ring-[#3b82f6]/50 focus:border-[#3b82f6] transition-colors"
            />
          </div>

          {/* Password (only shown when auth is enabled) */}
          {authEnabled && (
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                  className="w-full px-4 py-2.5 pr-10 rounded-xl border border-gray-200 dark:border-[#2a2a45] bg-white dark:bg-[#1a1a2e] text-gray-900 dark:text-white placeholder:text-gray-400 dark:placeholder:text-[#6b6b85] focus:outline-none focus:ring-2 focus:ring-[#3b82f6]/50 focus:border-[#3b82f6] transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
              <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
              <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading || !username.trim()}
            className="w-full flex items-center justify-center gap-2 px-5 py-2.5 bg-[#3b82f6] text-white rounded-xl font-medium hover:bg-[#2563eb] disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
          >
            {loading ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <LogIn className="w-4 h-4" />
            )}
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        {/* Dev mode hint */}
        {!authEnabled && (
          <div className="mt-6 p-3 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
            <p className="text-xs text-blue-700 dark:text-blue-400 text-center">
              🔓 Dev mode: Authentication is disabled. Enter any username to continue.
            </p>
          </div>
        )}

        {/* Footer */}
        <p className="mt-6 text-center text-xs text-gray-400 dark:text-[#6b6b85]">
          Core Agent System with Swarm Intelligence
        </p>
      </div>
    </div>
  )
}
