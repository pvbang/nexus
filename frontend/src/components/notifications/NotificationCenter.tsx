// ============================================================
// Nexus - Notification Center
// Bell icon → slide-out panel with grouped notifications
// ============================================================

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Bell,
  X,
  CheckCheck,
  Trash2,
  CheckCircle2,
  XCircle,
  Bot,
  Users,
  AlertTriangle,
  Info,
  Inbox,
  Wrench,
  Brain,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import {
  useNotificationCenterStore,
  type CenterNotification,
  type NotificationKind,
} from '../../stores/notificationCenterStore'
import { formatDistanceToNowStrict, isToday, isYesterday } from 'date-fns'

// ============================================================
// Icon + color mapping per notification type
// ============================================================

const typeConfig: Record<NotificationKind, { icon: typeof CheckCircle2; color: string; badge: 'success' | 'error' | 'info' | 'purple' | 'warning' | 'default' }> = {
  task_complete: { icon: CheckCircle2, color: 'text-emerald-500', badge: 'success' },
  task_failed: { icon: XCircle, color: 'text-red-500', badge: 'error' },
  agent_spawn: { icon: Bot, color: 'text-blue-500', badge: 'info' },
  swarm_done: { icon: Users, color: 'text-purple-500', badge: 'purple' },
  tool_created: { icon: Wrench, color: 'text-orange-500', badge: 'warning' },
  xskill_learned: { icon: Brain, color: 'text-pink-500', badge: 'purple' },
  system: { icon: AlertTriangle, color: 'text-amber-500', badge: 'warning' },
  info: { icon: Info, color: 'text-blue-400', badge: 'default' },
}

// ============================================================
// Group notifications by date
// ============================================================

interface NotificationGroup {
  label: string
  notifications: CenterNotification[]
}

function groupByDate(notifications: CenterNotification[]): NotificationGroup[] {
  const groups: Map<string, CenterNotification[]> = new Map()

  for (const notif of notifications) {
    const date = new Date(notif.timestamp)
    let label: string
    if (isToday(date)) {
      label = 'Today'
    } else if (isYesterday(date)) {
      label = 'Yesterday'
    } else {
      label = 'Older'
    }
    const existing = groups.get(label)
    if (existing) {
      existing.push(notif)
    } else {
      groups.set(label, [notif])
    }
  }

  const order = ['Today', 'Yesterday', 'Older']
  return order
    .filter((label) => groups.has(label))
    .map((label) => ({
      label,
      notifications: groups.get(label)!,
    }))
}

// ============================================================
// Single notification item
// ============================================================

interface NotificationItemProps {
  notification: CenterNotification
  onMarkRead: (id: string) => void
  onRemove: (id: string) => void
}

function NotificationItem({ notification, onMarkRead, onRemove }: NotificationItemProps) {
  const config = typeConfig[notification.type]
  const Icon = config.icon

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      transition={{ duration: 0.15 }}
      className={cn(
        'group flex gap-3 rounded-lg px-3 py-2.5 transition-colors',
        notification.read
          ? 'opacity-60'
          : 'bg-blue-50/50 dark:bg-blue-900/10',
        'hover:bg-gray-50 dark:hover:bg-white/5',
      )}
    >
      {/* Icon */}
      <div className={cn('mt-0.5 shrink-0', config.color)}>
        <Icon className="h-4 w-4" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className={cn(
            'text-sm leading-snug',
            notification.read
              ? 'text-text-secondary dark:text-gray-400'
              : 'font-medium text-text-primary dark:text-gray-100',
          )}>
            {notification.title}
          </p>
          {!notification.read && (
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-blue-500" />
          )}
        </div>
        {notification.description && (
          <p className="mt-0.5 text-xs leading-relaxed text-text-muted dark:text-gray-500">
            {notification.description}
          </p>
        )}
        <div className="mt-1 flex items-center gap-2">
          <span className="text-[11px] text-text-muted dark:text-gray-500">
            {formatDistanceToNowStrict(new Date(notification.timestamp), { addSuffix: true })}
          </span>
          <Badge variant={config.badge} size="sm">
            {notification.type.replace('_', ' ')}
          </Badge>
        </div>
      </div>

      {/* Actions (visible on hover) */}
      <div className="flex shrink-0 items-start gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {!notification.read && (
          <button
            type="button"
            onClick={() => onMarkRead(notification.id)}
            aria-label="Mark as read"
            className="rounded p-1 text-text-muted hover:bg-gray-100 hover:text-text-primary dark:hover:bg-white/10 dark:hover:text-gray-200"
          >
            <CheckCheck className="h-3.5 w-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={() => onRemove(notification.id)}
          aria-label="Remove notification"
          className="rounded p-1 text-text-muted hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/20 dark:hover:text-red-400"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </motion.div>
  )
}

// ============================================================
// NotificationCenter (bell + panel)
// ============================================================

export function NotificationCenter() {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  const { notifications, unreadCount, markAsRead, markAllAsRead, clearAll, removeNotification } =
    useNotificationCenterStore()

  const groups = useMemo(() => groupByDate(notifications), [notifications])

  // Close on outside click
  useEffect(() => {
    if (!open) return

    const handleClick = (e: MouseEvent) => {
      if (
        panelRef.current &&
        !panelRef.current.contains(e.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  // Close on Escape
  useEffect(() => {
    if (!open) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open])

  const handleMarkRead = useCallback((id: string) => markAsRead(id), [markAsRead])
  const handleRemove = useCallback((id: string) => removeNotification(id), [removeNotification])

  return (
    <div className="relative">
      {/* Bell button */}
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
        className={cn(
          'relative inline-flex h-9 w-9 items-center justify-center rounded-lg',
          'text-text-muted hover:bg-black/5 hover:text-text-primary',
          'dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-gray-200',
          'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          open && 'bg-black/5 dark:bg-white/10',
        )}
      >
        <Bell className="h-5 w-5" />
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold leading-none text-white">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Slide-out panel */}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panelRef}
            initial={{ opacity: 0, y: 8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
            transition={{ duration: 0.15, ease: 'easeOut' }}
            className={cn(
              'absolute right-0 top-full z-50 mt-2',
              'w-[380px] max-h-[70vh] overflow-hidden',
              'rounded-xl border bg-white shadow-elevated',
              'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
              'flex flex-col',
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-4 py-3 dark:border-[#2a2a45]">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-text-primary dark:text-gray-100">
                  Notifications
                </h3>
                {unreadCount > 0 && (
                  <Badge variant="brand" size="sm">
                    {unreadCount} new
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-1">
                {unreadCount > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<CheckCheck />}
                    onClick={markAllAsRead}
                    aria-label="Mark all as read"
                  >
                    Read all
                  </Button>
                )}
                {notifications.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Trash2 />}
                    onClick={clearAll}
                    aria-label="Clear all notifications"
                    iconOnly
                  />
                )}
              </div>
            </div>

            {/* Notification list */}
            <div className="flex-1 overflow-y-auto px-2 py-2">
              {notifications.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-text-muted dark:text-gray-500">
                  <Inbox className="mb-3 h-10 w-10 opacity-40" />
                  <p className="text-sm font-medium">No notifications</p>
                  <p className="mt-1 text-xs">You&apos;re all caught up!</p>
                </div>
              ) : (
                <AnimatePresence mode="popLayout">
                  {groups.map((group) => (
                    <div key={group.label} className="mb-2">
                      <p className="mb-1 px-3 pt-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted dark:text-gray-500">
                        {group.label}
                      </p>
                      {group.notifications.map((notif) => (
                        <NotificationItem
                          key={notif.id}
                          notification={notif}
                          onMarkRead={handleMarkRead}
                          onRemove={handleRemove}
                        />
                      ))}
                    </div>
                  ))}
                </AnimatePresence>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
