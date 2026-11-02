// ============================================================
// Nexus - AgentSpawnCard
// Inline chat card showing spawned agents with roles and tools
// ============================================================

import { motion } from 'framer-motion'
import { Bot } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'

interface SpawnedAgent {
  id: string
  name: string
  role: string
  tools: string[]
}

interface AgentSpawnCardProps {
  agents: SpawnedAgent[]
}

const ROLE_COLORS: Record<string, string> = {
  researcher: 'bg-blue-500',
  analyst: 'bg-emerald-500',
  writer: 'bg-purple-500',
  coder: 'bg-amber-500',
  reviewer: 'bg-rose-500',
  default: 'bg-gray-500',
}

function roleColor(role: string): string {
  const key = (role || '').toLowerCase()
  return ROLE_COLORS[key] ?? ROLE_COLORS.default
}

export function AgentSpawnCard({ agents }: AgentSpawnCardProps) {
  if (agents.length === 0) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        type: 'spring',
        stiffness: 350,
        damping: 26,
        mass: 0.8,
      }}
      className={cn(
        'rounded-xl border border-border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
        'shadow-sm hover:shadow-md transition-shadow duration-300',
      )}
    >
      {/* Header */}
      <motion.div
        className="flex items-center gap-2 mb-2"
        initial={{ opacity: 0, x: -6 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 0.1, type: 'spring', stiffness: 400, damping: 25 }}
      >
        <motion.div
          animate={{ rotate: [0, 10, -10, 0] }}
          transition={{ duration: 0.5, delay: 0.2 }}
        >
          <Bot className="h-4 w-4 text-brand-500 dark:text-brand-400 shrink-0" />
        </motion.div>
        <span className="font-semibold text-text-primary dark:text-gray-100">
          Agents Spawned
        </span>
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 500, damping: 15, delay: 0.2 }}
        >
          <Badge variant="info" size="sm">{agents.length}</Badge>
        </motion.div>
      </motion.div>

      {/* Agent list with stagger */}
      <div className="space-y-1.5">
        {agents.map((agent, idx) => (
          <motion.div
            key={agent.id}
            initial={{ opacity: 0, x: -12, scale: 0.95 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            transition={{
              type: 'spring',
              stiffness: 400,
              damping: 25,
              delay: 0.15 + idx * 0.08,
            }}
            className="flex items-center gap-2 text-xs"
          >
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{
                type: 'spring',
                stiffness: 500,
                damping: 20,
                delay: 0.2 + idx * 0.08,
              }}
              className={cn(
                'h-5 w-5 rounded-full flex items-center justify-center text-white text-[10px] font-bold shrink-0',
                'shadow-sm',
                roleColor(agent.role),
              )}
              title={agent.role}
            >
              {agent.name.charAt(0).toUpperCase()}
            </motion.span>
            <span className="font-medium text-text-primary dark:text-gray-200">
              {agent.name}
            </span>
            {agent.tools.length > 0 && (
              <div className="flex flex-wrap gap-1 ml-auto">
                {agent.tools.slice(0, 3).map((tool) => (
                  <Badge key={tool} variant="default" size="sm">
                    {tool}
                  </Badge>
                ))}
                {agent.tools.length > 3 && (
                  <Badge variant="default" size="sm">
                    +{agent.tools.length - 3}
                  </Badge>
                )}
              </div>
            )}
          </motion.div>
        ))}
      </div>
    </motion.div>
  )
}
