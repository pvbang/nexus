// ============================================================
// Nexus - Toast Notification Component
// ============================================================

import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react'
import { useNotificationStore, type NotificationType } from '../../stores/notificationStore'

const iconMap: Record<NotificationType, typeof CheckCircle> = {
  success: CheckCircle,
  error: AlertCircle,
  info: Info,
  warning: AlertTriangle,
}

const colorMap: Record<NotificationType, { bg: string; border: string; icon: string; text: string }> = {
  success: { bg: 'bg-emerald-50', border: 'border-emerald-200', icon: 'text-emerald-600', text: 'text-emerald-900' },
  error: { bg: 'bg-red-50', border: 'border-red-200', icon: 'text-red-600', text: 'text-red-900' },
  info: { bg: 'bg-blue-50', border: 'border-blue-200', icon: 'text-blue-600', text: 'text-blue-900' },
  warning: { bg: 'bg-amber-50', border: 'border-amber-200', icon: 'text-amber-600', text: 'text-amber-900' },
}

export function ToastContainer() {
  const notifications = useNotificationStore((s) => s.notifications)
  const remove = useNotificationStore((s) => s.remove)

  if (notifications.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {notifications.map((n) => {
        const Icon = iconMap[n.type]
        const colors = colorMap[n.type]
        return (
          <div
            key={n.id}
            role={n.type === 'error' ? 'alert' : 'status'}
            aria-live={n.type === 'error' ? 'assertive' : 'polite'}
            aria-atomic="true"
            className={`${colors.bg} ${colors.border} border rounded-[12px] p-3.5 shadow-elevated animate-slide-up flex items-start gap-3`}
          >
            <Icon className={`w-5 h-5 ${colors.icon} shrink-0 mt-0.5`} strokeWidth={2} />
            <div className="flex-1 min-w-0">
              <p className={`text-[13px] font-semibold ${colors.text}`}>{n.title}</p>
              {n.message && (
                <p className={`text-[12px] mt-0.5 ${colors.text} opacity-80`}>{n.message}</p>
              )}
            </div>
            <button
              onClick={() => remove(n.id)}
              aria-label="Dismiss notification"
              className="shrink-0 w-6 h-6 rounded-[6px] flex items-center justify-center hover:bg-black/5 transition-colors"
            >
              <X className="w-3.5 h-3.5 text-text-muted" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
