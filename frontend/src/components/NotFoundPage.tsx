// ============================================================
// MekongAI Nexus - 404 Not Found Page
// ============================================================

import { useNavigate } from 'react-router-dom'
import { Home, ArrowLeft, MessageSquare } from 'lucide-react'

export function NotFoundPage() {
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center h-full bg-white dark:bg-[#0f0f17] px-4">
      <div className="text-center max-w-md">
        {/* 404 Visual */}
        <div className="relative mb-8">
          <div className="text-[120px] sm:text-[160px] font-black text-transparent bg-clip-text bg-gradient-to-br from-[#3b82f6] to-[#60a5fa] leading-none select-none opacity-20">
            404
          </div>
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-[#3b82f6]/10 to-[#60a5fa]/10 dark:from-[#3b82f6]/20 dark:to-[#60a5fa]/20 flex items-center justify-center">
              <span className="text-4xl">🔍</span>
            </div>
          </div>
        </div>

        {/* Message */}
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-3">
          Page not found
        </h1>
        <p className="text-gray-500 dark:text-[#a0a0b8] mb-8 leading-relaxed">
          The page you're looking for doesn't exist or has been moved. 
          Let's get you back on track.
        </p>

        {/* Actions */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
          <button
            onClick={() => navigate('/chat')}
            className="flex items-center gap-2 px-5 py-2.5 bg-[#3b82f6] text-white rounded-xl font-medium hover:bg-[#2563eb] transition-colors shadow-sm"
          >
            <MessageSquare className="w-4 h-4" />
            Go to Chat
          </button>
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-2 px-5 py-2.5 bg-gray-100 dark:bg-[#1a1a2e] text-gray-700 dark:text-[#e4e4ed] rounded-xl font-medium hover:bg-gray-200 dark:hover:bg-[#252540] transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Go Back
          </button>
          <button
            onClick={() => navigate('/')}
            className="flex items-center gap-2 px-5 py-2.5 bg-gray-100 dark:bg-[#1a1a2e] text-gray-700 dark:text-[#e4e4ed] rounded-xl font-medium hover:bg-gray-200 dark:hover:bg-[#252540] transition-colors"
          >
            <Home className="w-4 h-4" />
            Home
          </button>
        </div>
      </div>
    </div>
  )
}
