// ============================================================
// MekongAI Nexus - TimelineCard (Shared)
// Renders a single TimelineItem using the same specialized card
// components used by ProcessStream. Reused in MessageEventExplorer
// for consistent UI between live and historical views.
// ============================================================

import {
  PlanningCard,
  AgentSpawnCard,
  ToolCallCard,
  DebateCard,
  SynthesisCard,
  XSkillCard,
  ToolCreationCard,
  AgentThinkingCard,
  AgentCommunicationCard,
  DeepAnalysisCard,
  SelfReflectionCard,
  CritiqueFeedbackCard,
  AutoVerifyCard,
  CompletionCheckCard,
  MidAnalysisCard,
  RevisionCard,
  ReactPhaseIndicator,
} from './cards'
import { formatEventTime } from './timeline-utils'
import type { TimelineItem } from './timeline-utils'

// ── Card renderer ─────────────────────────────────────────────

export function TimelineCard({ item }: { item: TimelineItem }) {
  const cardContent = (() => {
    switch (item.kind) {
      case 'plan':
        return <PlanningCard plan={item.data} />
      case 'agent_spawn':
        return <AgentSpawnCard agents={item.data} />
      case 'tool_call':
        return <ToolCallCard execution={item.data} />
      case 'debate':
        return <DebateCard round={item.data} />
      case 'synthesis':
        return <SynthesisCard event={item.data} />
      case 'xskill':
        return <XSkillCard events={item.data} />
      case 'tool_creation':
        return <ToolCreationCard event={item.data} />
      case 'agent_thinking':
        return <AgentThinkingCard {...item.data} />
      case 'agent_communication':
        return <AgentCommunicationCard message={item.data} />
      case 'deep_analysis':
        return <DeepAnalysisCard data={item.data} />
      case 'self_reflection':
        return <SelfReflectionCard data={item.data} />
      case 'auto_verify':
        return <AutoVerifyCard data={item.data} />
      case 'completion_check':
        return <CompletionCheckCard data={item.data} />
      case 'mid_analysis':
        return <MidAnalysisCard data={item.data} />
      case 'critique':
        return <CritiqueFeedbackCard data={item.data} />
      case 'revision':
        return <RevisionCard data={item.data} />
      case 'react_phase':
        return <ReactPhaseIndicator currentPhase={item.data.currentPhase} round={item.data.round} />
      default:
        return null
    }
  })()

  const timeStr = formatEventTime(item.timestamp)

  return (
    <div className="relative">
      {cardContent}
      {timeStr && (
        <div className="absolute bottom-3 right-4 text-[9px] text-gray-400 dark:text-gray-600 font-mono opacity-70">
          {timeStr}
        </div>
      )}
    </div>
  )
}
