// ============================================================
// Nexus - Page Loading Skeletons
// Context-aware skeleton screens for each lazy-loaded page
// ============================================================

import { cn } from '../../lib/cn'

function Pulse({ className }: { className?: string }) {
  return (
    <div className={cn('animate-pulse rounded-lg bg-gray-200 dark:bg-surface-raised', className)} />
  )
}

/** Chat page skeleton — sidebar + messages area */
export function ChatSkeleton() {
  return (
    <div className="flex h-full">
      {/* Sidebar skeleton */}
      <div className="hidden md:flex flex-col w-64 border-r border-gray-200 dark:border-border-subtle p-4 gap-3">
        <Pulse className="h-9 w-full" />
        <Pulse className="h-6 w-3/4" />
        <Pulse className="h-6 w-full" />
        <Pulse className="h-6 w-2/3" />
        <Pulse className="h-6 w-full" />
        <Pulse className="h-6 w-4/5" />
      </div>
      {/* Messages area */}
      <div className="flex-1 flex flex-col p-6 gap-4">
        <div className="flex gap-3 items-start">
          <Pulse className="w-8 h-8 rounded-full shrink-0" />
          <Pulse className="h-16 w-2/3" />
        </div>
        <div className="flex gap-3 items-start justify-end">
          <Pulse className="h-12 w-1/2" />
          <Pulse className="w-8 h-8 rounded-full shrink-0" />
        </div>
        <div className="flex gap-3 items-start">
          <Pulse className="w-8 h-8 rounded-full shrink-0" />
          <Pulse className="h-24 w-3/4" />
        </div>
        <div className="mt-auto">
          <Pulse className="h-14 w-full rounded-xl" />
        </div>
      </div>
    </div>
  )
}

/** Dashboard skeleton — stats cards + charts */
export function DashboardSkeleton() {
  return (
    <div className="p-6 space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Pulse className="h-24" />
        <Pulse className="h-24" />
        <Pulse className="h-24" />
        <Pulse className="h-24" />
      </div>
      {/* Charts area */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Pulse className="h-64" />
        <Pulse className="h-64" />
      </div>
      {/* Activity list */}
      <Pulse className="h-48" />
    </div>
  )
}

/** Agents/Swarms page skeleton — grid of cards */
export function CardGridSkeleton() {
  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <Pulse className="h-8 w-48" />
        <Pulse className="h-9 w-32" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Pulse key={i} className="h-40" />
        ))}
      </div>
    </div>
  )
}

/** Tasks page skeleton — table/list */
export function TableSkeleton() {
  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <Pulse className="h-8 w-48" />
        <Pulse className="h-9 w-40" />
      </div>
      <div className="space-y-2">
        <Pulse className="h-12 w-full" />
        {Array.from({ length: 8 }).map((_, i) => (
          <Pulse key={i} className="h-10 w-full" />
        ))}
      </div>
    </div>
  )
}

/** Terminal skeleton */
export function TerminalSkeleton() {
  return (
    <div className="p-6 space-y-4">
      <Pulse className="h-8 w-40" />
      <div className="bg-gray-900 dark:bg-[#0a0a12] rounded-xl p-4 space-y-2 h-[60vh]">
        <Pulse className="h-4 w-3/4 bg-gray-700" />
        <Pulse className="h-4 w-1/2 bg-gray-700" />
        <Pulse className="h-4 w-2/3 bg-gray-700" />
        <Pulse className="h-4 w-1/3 bg-gray-700" />
      </div>
    </div>
  )
}

/** Generic full-page skeleton (settings, analytics, etc.) */
export function GenericPageSkeleton() {
  return (
    <div className="p-6 space-y-6">
      <Pulse className="h-8 w-56" />
      <div className="space-y-4">
        <Pulse className="h-32" />
        <Pulse className="h-48" />
        <Pulse className="h-24" />
      </div>
    </div>
  )
}
