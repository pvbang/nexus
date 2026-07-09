// ============================================================
// MekongAI Nexus - Agent Status Display
// Shows which agent/specialist is working with avatar, role, status
// ============================================================

import {
  Bot, Brain, Sparkles, Search, Code2, Shield,
  MessageSquare, Users, Loader2, CheckCircle2,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../lib/cn'

// ── Types ─────────────────────────────────────────────────────

export interface AgentInfo {
  id: string
  name: string
  role: string
  status: 'thinking' | 'executing' | 'waiting' | 'done' | 'idle'
  avatar?: string
  currentAction?: string
}

export interface SwarmInfo {
  id: string
  name: string
  strategy: string
  agents: AgentInfo[]
  currentRound: number
  totalRounds: number
  status: 'active' | 'debating' | 'synthesizing' | 'done'
}

export interface AgentStatusProps {
  agents: AgentInfo[]
  swarm?: SwarmInfo | null
  className?: string
}

// ── Agent Avatar ──────────────────────────────────────────────

function AgentAvatar({ agent, size = 'md' }: { agent: AgentInfo; size?: 'sm' | 'md' }) {
  const sizeClasses = size === 'sm' ? 'w-7 h-7' : 'w-9 h-9'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  const roleIcon = () => {
    const role = agent.role.toLowerCase()
    if (role.includes('research') || role.includes('search')) return <Search className={iconSize} />
    if (role.includes('code') || role.includes('developer') || role.includes('engineer')) return <Code2 className={iconSize} />
    if (role.includes('security') || role.includes('audit')) return <Shield className={iconSize} />
    if (role.includes('critic') || role.includes('review')) return <Brain className={iconSize} />
    if (role.includes('creative') || role.includes('writer')) return <Sparkles className={iconSize} />
    if (role.includes('communicate') || role.includes('chat')) return <MessageSquare className={iconSize} />
    return <Bot className={iconSize} />
  }

  const gradients: Record<string, string> = {
    thinking: 'from-purple-500 to-indigo-600',
    executing: 'from-blue-500 to-cyan-500',
    waiting: 'from-amber-400 to-orange-500',
    done: 'from-emerald-500 to-green-600',
    idle: 'from-gray-400 to-gray-500',
  }

  return (
    <div className="relative">
      <div className={cn(
        sizeClasses,
        'rounded-xl bg-gradient-to-br flex items-center justify-center text-white shadow-sm',
        gradients[agent.status]
      )}>
        {roleIcon()}
      </div>
      {(agent.status === 'thinking' || agent.status === 'executing') && (
        <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-400 border-2 border-white dark:border-[#1e1e35] animate-pulse" />
      )}
    </div>
  )
}

// ── Thinking Animation ────────────────────────────────────────

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-0.5 ml-1">
      {[0, 1, 2].map(i => (
        <motion.span
          key={i}
          className="w-1 h-1 rounded-full bg-current"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
        />
      ))}
    </span>
  )
}

// ── Single Agent Card ─────────────────────────────────────────

function AgentCard({ agent }: { agent: AgentInfo }) {
  const statusLabels: Record<AgentInfo['status'], string> = {
    thinking: 'Thinking',
    executing: 'Executing',
    waiting: 'Waiting',
    done: 'Complete',
    idle: 'Idle',
  }

  const statusColors: Record<AgentInfo['status'], string> = {
    thinking: 'text-purple-600 dark:text-purple-400',
    executing: 'text-blue-600 dark:text-blue-400',
    waiting: 'text-amber-600 dark:text-amber-400',
    done: 'text-emerald-600 dark:text-emerald-400',
    idle: 'text-gray-400 dark:text-gray-500',
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 10 }}
      className={cn(
        'flex items-center gap-2.5 px-3 py-2 rounded-xl',
        'bg-white dark:bg-[#1a1a30] border border-gray-100 dark:border-[#2a2a45]',
        'shadow-sm hover:shadow-md transition-shadow'
      )}
    >
      <AgentAvatar agent={agent} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] font-semibold text-gray-800 dark:text-gray-200 truncate">
            {agent.name}
          </span>
          {agent.status === 'thinking' && <ThinkingDots />}
        </div>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className="text-[10px] text-gray-500 dark:text-gray-400 truncate">
            {agent.role}
          </span>
          <span className="text-[10px] opacity-30">{'\u2022'}</span>
          <span className={cn('text-[10px] font-medium', statusColors[agent.status])}>
            {statusLabels[agent.status]}
            {agent.status === 'executing' && agent.currentAction && (
              <span className="opacity-70"> — {agent.currentAction}</span>
            )}
          </span>
        </div>
      </div>
      {agent.status === 'executing' && (
        <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin shrink-0" />
      )}
    </motion.div>
  )
}

// ── Swarm Status Card ─────────────────────────────────────────

function SwarmStatusCard({ swarm }: { swarm: SwarmInfo }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="px-3 py-2.5 rounded-xl bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/20 dark:to-purple-900/20 border border-indigo-100 dark:border-indigo-800/40"
    >
      <div className="flex items-center gap-2 mb-2">
        <Users className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
        <span className="text-[12px] font-semibold text-indigo-800 dark:text-indigo-300">
          Swarm: {swarm.name}
        </span>
        <span className="ml-auto text-[10px] text-indigo-600 dark:text-indigo-400 font-medium px-2 py-0.5 rounded-full bg-indigo-100 dark:bg-indigo-900/30">
          Round {swarm.currentRound}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-500 dark:text-gray-400">
          Strategy: {swarm.strategy}
        </span>
        <span className="text-[10px] opacity-30">{'\u2022'}</span>
        <span className="text-[10px] text-gray-500 dark:text-gray-400">
          {swarm.agents.length} agents
        </span>
      </div>
      <div className="flex items-center gap-1 mt-2">
        {swarm.agents.slice(0, 5).map((agent) => (
          <AgentAvatar key={agent.id} agent={agent} size="sm" />
        ))}
        {swarm.agents.length > 5 && (
          <span className="text-[10px] text-gray-400 ml-1">
            +{swarm.agents.length - 5}
          </span>
        )}
      </div>
    </motion.div>
  )
}

// ── Main Component ────────────────────────────────────────────

export function AgentStatusDisplay({ agents, swarm, className }: AgentStatusProps) {
  const activeAgents = agents.filter(a => a.status !== 'idle' && a.status !== 'done')
  const completedAgents = agents.filter(a => a.status === 'done')

  if (agents.length === 0 && !swarm) return null

  return (
    <div className={cn('space-y-2', className)}>
      {swarm && <SwarmStatusCard swarm={swarm} />}

      <AnimatePresence mode="popLayout">
        {activeAgents.map(agent => (
          <AgentCard key={agent.id} agent={agent} />
        ))}
      </AnimatePresence>

      {completedAgents.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 text-[10px] text-gray-400 dark:text-gray-500">
          <span className="flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-emerald-500" />
            {completedAgents.length} agent{completedAgents.length > 1 ? 's' : ''} completed
          </span>
          <span className="flex-1 h-px bg-gray-200 dark:bg-[#2a2a45]" />
        </div>
      )}
    </div>
  )
}

// ── Compact Inline Status ─────────────────────────────────────

export function AgentStatusInline({ agent }: { agent: AgentInfo }) {
  return (
    <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-50 dark:bg-[#1a1a30] border border-gray-100 dark:border-[#2a2a45]">
      <AgentAvatar agent={agent} size="sm" />
      <span className="text-[11px] font-medium text-gray-700 dark:text-gray-300">
        {agent.name}
      </span>
      {agent.status === 'thinking' && <ThinkingDots />}
      {agent.status === 'executing' && (
        <Loader2 className="w-3 h-3 text-blue-500 animate-spin" />
      )}
    </div>
  )
}
